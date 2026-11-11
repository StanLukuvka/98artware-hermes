"""A running stage: the VM, the bridge to it, and frame capture."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Self

from . import disc
from .bridge import Bridge
from .qemu import VM, VMConfig
from .recorder import Recorder

SNAPSHOT = "ready"
IMAGE_FILE = "image.json"


class ImageMissing(FileNotFoundError):
    """The work directory has no finished image: setup hasn't run there."""


def image_disk(work: Path | str, config: dict) -> Path:
    """The disk image.json names, or the file of that name beside it.

    A work folder can be prepared in the container, which records
    /work/golden.qcow2, and used on the host, or the other way round.
    """
    disk = Path(config["disk"])
    beside = Path(work) / disk.name
    return disk if disk.exists() or not beside.exists() else beside


class Session:
    def __init__(self, vm: VM, bridge: Bridge):
        self.vm = vm
        self.bridge = bridge
        self._disc = (
            False  # True while a disc this session inserted may be in the drive
        )
        # True only between a revert and the next scene touching the guest. A new
        # Session is not fresh: nobody knows what its guest has been through.
        self.fresh = False

    def attach_disc(self, files: dict[str, bytes]) -> None:
        """Put an ISO of `files` (level 1 names, see disc.build_iso) in the CD drive."""
        self.vm.insert_disc(disc.build_iso(files))
        self._disc = True

    def record(
        self, path: Path | str, fps: int = 30, lossless: bool = False
    ) -> Recorder:
        return Recorder(self, path, fps, lossless)

    def revert(self, snapshot: str = SNAPSHOT) -> None:
        """Put the running guest back to the snapshot, keeping the bridge.

        Took 0.06 to 0.2 s in tests. Every window handle and process from
        before is gone, and open calls fail at once instead of timing out.
        """
        self.bridge.abandon(f"the desktop was reverted to {snapshot!r}")
        if self._disc:
            # loadvm restores the guest's empty tray but not QEMU's medium: eject first.
            # Only when we inserted one, so a scene without a disc never sends the op
            # (an older container image would answer "unknown op").
            self.vm.eject_disc()
            self._disc = False
        self.vm.load_snapshot(snapshot)
        self.bridge.ping()
        self.fresh = True

    def close(self) -> None:
        self.bridge.close()
        self.vm.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


@contextmanager
def private_stage(work: Path | str = "work", prefix: str = "render-") -> Iterator[Path]:
    """A throwaway copy of the prepared image, for one run.

    A VM writes to its disk, and two can't share one, so a run takes a
    copy-on-write copy under `work/runs` and it is deleted afterwards.
    """
    config = json.loads((Path(work) / IMAGE_FILE).read_text())
    runs = Path(work) / "runs"
    runs.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=prefix, dir=runs))
    try:
        source = image_disk(work, config)
        disk = stage / source.name
        subprocess.run(["cp", "--reflink=auto", str(source), str(disk)], check=True)
        config["disk"] = str(disk.resolve())
        (stage / IMAGE_FILE).write_text(json.dumps(config, indent=2))
        yield stage
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def boot_local(
    work: Path | str = "work",
    display: str = "none",
    ready_timeout: float = 30.0,
) -> Session:
    """Restore the prepared desktop in a QEMU run on this host and connect to it.

    For experiments only: scenes use the VM service (`art98.attach`). Each boot
    starts from the same snapshot, so nothing a previous run did to the guest
    survives. It runs QEMU on the image in the work dir it is given, so callers
    must pass a private copy (as `stage` and `private_stage` do), never the
    golden work dir.
    """
    image = Path(work) / IMAGE_FILE
    if not image.exists():
        raise ImageMissing(f"{image} not found; run `art98 setup` first")
    data = json.loads(image.read_text())
    data["disk"] = str(image_disk(work, data))
    config = VMConfig.from_json(data)
    config.loadvm = SNAPSHOT
    config.display = display
    vm = VM(config, log=Path(work) / "qemu.log").start()
    try:
        bridge = Bridge.connect(vm.serial_path)
        bridge.wait_ready(ready_timeout)
    except BaseException:
        vm.close()
        raise
    return Session(vm, bridge)


@contextmanager
def stage(work: Path | str = "work", display: str = "none") -> Iterator[Session]:
    """A local session on a throwaway copy of the image, closed afterwards.

    For experiments only: it boots QEMU on this host and copies the disk under
    work/runs. Scenes and tests use the VM service (`art98.attach`) instead.
    """
    with private_stage(work) as copy, boot_local(copy, display=display) as session:
        yield session
