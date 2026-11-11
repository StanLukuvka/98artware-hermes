"""Starts and controls the VM."""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Self

QEMU_BINARY = "qemu-system-i386"


class QemuError(RuntimeError):
    pass


def _opt(path: Path | str) -> str:
    """Escape a value for a QEMU comma separated option."""
    return str(path).replace(",", ",,")


@dataclass
class VMConfig:
    """Hardware of the virtual machine.

    The defaults are chosen so Windows 98 finds every device with its own
    drivers. Software emulation is deliberate: KVM on a recent CPU exposes the
    Windows 9x TLB bug and needs a patched VMM, and the emulated Pentium III
    also keeps Windows away from its timing bugs on fast processors.

    Guest reboots end the QEMU process instead of resetting the machine. After
    a warm reset IO.SYS spins on the BIOS tick counter, because the timer
    interrupt stays pending and is never taken, so Windows freezes on its boot
    logo. Only a new process boots, so a reboot here means start the VM again.
    """

    disk: Path
    disk_format: str = "qcow2"
    machine: str = "pc"
    accel: str = "tcg"
    cpu: str = "pentium3"
    memory_mb: int = 128
    vga: str = "cirrus"
    sound: bool = True
    rtc_base: str = "1998-06-25T09:00:00"
    icount_shift: int | None = None
    floppy: Path | None = None
    boot_once: str | None = None
    loadvm: str | None = None
    audio_wav: Path | None = None
    display: str = "none"
    extra_args: list[str] = field(default_factory=list)

    def argv(self, qmp: Path, serial: Path) -> list[str]:
        boot = "order=c" + (f",once={self.boot_once}" if self.boot_once else "")
        args = [
            QEMU_BINARY,
            "-machine",
            f"{self.machine},accel={self.accel},acpi=off,hpet=off",
            "-cpu",
            self.cpu,
            "-m",
            str(self.memory_mb),
            "-vga",
            self.vga,
            "-rtc",
            f"base={self.rtc_base},clock=vm",
            "-drive",
            f"file={_opt(self.disk)},format={self.disk_format},if=ide,index=0",
            "-boot",
            boot,
            "-no-reboot",
            "-nic",
            "none",
            "-display",
            self.display,
            "-chardev",
            f"socket,id=qmp,path={_opt(qmp)},server=on,wait=off",
            "-mon",
            "chardev=qmp,mode=control",
            "-chardev",
            f"socket,id=guest,path={_opt(serial)},server=on,wait=off",
            "-serial",
            "chardev:guest",
        ]
        if self.icount_shift is not None:
            args += ["-icount", f"shift={self.icount_shift},sleep=off,align=off"]
        if self.floppy:
            args += ["-drive", f"file={_opt(self.floppy)},format=raw,if=floppy,index=0"]
        if self.sound:
            backend = f"wav,path={_opt(self.audio_wav)}" if self.audio_wav else "none"
            args += ["-audiodev", f"{backend},id=snd", "-device", "sb16,audiodev=snd"]
        if self.loadvm:
            args += ["-loadvm", self.loadvm]
        return args + self.extra_args

    def to_json(self) -> dict:
        data = asdict(self)
        return {k: str(v) if isinstance(v, Path) else v for k, v in data.items()}

    @classmethod
    def from_json(cls, data: dict) -> VMConfig:
        known = {f.name for f in fields(cls)}
        values = {k: v for k, v in data.items() if k in known}
        for key in ("disk", "floppy", "audio_wav"):
            if values.get(key):
                values[key] = Path(values[key])
        return cls(**values)


class QMP:
    """QEMU's control protocol."""

    def __init__(self, path: Path):
        self._sock = socket.socket(socket.AF_UNIX)
        self._sock.connect(str(path))
        self._file = self._sock.makefile("rwb")
        self._read()
        self.execute("qmp_capabilities")

    def _read(self) -> dict:
        line = self._file.readline()
        if not line:
            raise QemuError("QEMU closed the control connection")
        return json.loads(line)

    def execute(self, command: str, **arguments) -> object:
        message: dict = {"execute": command}
        if arguments:
            message["arguments"] = arguments
        self._file.write(json.dumps(message).encode() + b"\n")
        self._file.flush()
        while True:
            reply = self._read()
            if "return" in reply:
                return reply["return"]
            if "error" in reply:
                raise QemuError(f"{command}: {reply['error'].get('desc')}")

    def human(self, command: str) -> str:
        """Run a monitor command and return its text."""
        return str(self.execute("human-monitor-command", **{"command-line": command}))

    def human_silent(self, command: str) -> None:
        """Run a monitor command that prints nothing unless it failed."""
        output = self.human(command).strip()
        if output:
            raise QemuError(f"{command}: {output}")

    def close(self) -> None:
        self._file.close()
        self._sock.close()


def parse_snapshots(text: str) -> list[str]:
    """Tags from `info snapshots`: the second column of the rows under the ID/TAG header."""
    tags, rows = [], False
    for line in text.splitlines():
        words = line.split()
        if rows and len(words) >= 2:
            tags.append(words[1])
        elif words[:2] == ["ID", "TAG"]:
            rows = True
    return tags


class VM:
    """A running QEMU process and its control sockets."""

    def __init__(self, config: VMConfig, log: Path | None = None):
        self.config = config
        self._log_path = log
        self._dir = Path(
            tempfile.mkdtemp(prefix="art98-", dir=os.environ.get("XDG_RUNTIME_DIR"))
        )
        self.qmp_path = self._dir / "qmp"
        self.serial_path = self._dir / "serial"
        self._process: subprocess.Popen | None = None
        self.qmp: QMP | None = None

    def start(self) -> VM:
        log = open(self._log_path, "ab") if self._log_path else subprocess.DEVNULL  # noqa: SIM115
        self._process = subprocess.Popen(
            self.config.argv(self.qmp_path, self.serial_path),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
        )
        deadline = time.monotonic() + 30
        while True:
            self._check_alive()
            try:
                self.qmp = QMP(self.qmp_path)
                return self
            except (FileNotFoundError, ConnectionRefusedError):
                if time.monotonic() > deadline:
                    raise QemuError("timed out waiting for QEMU to start") from None
                time.sleep(0.05)

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _check_alive(self) -> None:
        if not self.running:
            code = self._process.returncode if self._process else None
            where = f"; see {self._log_path}" if self._log_path else ""
            raise QemuError(f"QEMU exited with status {code}{where}")

    def screenshot(self, path: Path | str, format: str = "png") -> Path:
        """Save the display as seen by the VM's video card."""
        self.qmp.execute(
            "screendump", filename=str(Path(path).resolve()), format=format
        )
        return Path(path)

    def send_keys(self, *qcodes: str, hold_ms: int = 80) -> None:
        """Press keys together, such as ("alt", "a"), on the guest's keyboard."""
        keys = [{"type": "qcode", "data": code} for code in qcodes]
        self.qmp.execute("send-key", keys=keys, **{"hold-time": hold_ms})

    def pause(self) -> None:
        self.qmp.execute("stop")

    def resume(self) -> None:
        self.qmp.execute("cont")

    def save_snapshot(self, name: str) -> None:
        self.qmp.human_silent(f"savevm {name}")

    def load_snapshot(self, name: str) -> None:
        self.qmp.human_silent(f"loadvm {name}")

    def snapshots(self) -> list[str]:
        """Tags of the saved snapshots, from the monitor's `info snapshots`."""
        return parse_snapshots(self.qmp.human("info snapshots"))

    # The default CD drive. `device` commands take this name; `blockdev-remove-medium`
    # takes only an id, the qdev path from query-block (it moves with the device list).
    CD_DEVICE = "ide1-cd0"

    def _cd(self) -> dict | None:
        return next(
            (
                d
                for d in self.qmp.execute("query-block")
                if d.get("device") == self.CD_DEVICE
            ),
            None,
        )

    def insert_disc(self, iso: bytes, name: str = "art98.iso") -> None:
        """Put an ISO image in the CD drive; the file lives in this VM's own folder."""
        path = self._dir / name
        path.write_bytes(iso)
        self.qmp.execute(
            "blockdev-change-medium",
            device=self.CD_DEVICE,
            filename=str(path),
            format="raw",
            force=True,
            **{"read-only-mode": "read-only"},
        )

    def eject_disc(self) -> None:
        """Open the tray and take the medium out; harmless on an empty drive. Call before loadvm."""
        cd = self._cd()
        if cd is None or "inserted" not in cd:
            return
        self.qmp.execute("blockdev-open-tray", device=self.CD_DEVICE, force=True)
        self.qmp.execute("blockdev-remove-medium", id=cd["qdev"])
        self.qmp.execute("blockdev-close-tray", device=self.CD_DEVICE)

    def disc_state(self) -> dict:
        cd = self._cd()
        return {
            "present": cd is not None,
            "inserted": bool(cd and "inserted" in cd),
            "tray_open": bool(cd and cd.get("tray_open")),
        }

    def wait_exit(self, timeout: float) -> bool:
        """Wait for QEMU to end on its own, as it does when the guest reboots."""
        try:
            self._process.wait(timeout)
        except subprocess.TimeoutExpired:
            return False
        return True

    def quit(self, timeout: float = 15.0) -> None:
        if not self.running:
            return
        try:
            self.qmp.execute("quit")
        except (OSError, QemuError):
            pass
        try:
            self._process.wait(timeout)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait()

    def close(self) -> None:
        self.quit()
        if self.qmp:
            self.qmp.close()
        shutil.rmtree(self._dir, ignore_errors=True)

    def __enter__(self) -> Self:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.close()
