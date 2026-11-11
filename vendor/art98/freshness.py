"""Warnings when the image or the guest programs are older than the code.

Warnings only: nothing here stops a render. Mtimes are compared, and an
installed package (no guest/ or setup/Dockerfile beside it) gets none. The
engine image check (`container_warning`) is for `vm status` and `up`.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# (built file under guest/build, its source under guest/)
# (renders never use mbr.bin, only setup does, so it is not checked here)
PROGRAMS = (("agent.exe", "call_proxy/agent.c"), ("view.exe", "picture_viewer/view.c"))
CODE_GLOBS = (
    "core/art98/**/*.py",
    "setup/art98_setup/**/*.py",
    "guest/**/*.c",
    "guest/**/*.S",
    "guest/Makefile",
    "setup/Dockerfile",
)


def parse_created(text: str) -> float | None:
    """Docker: 2026-11-11T10:00:00.123456789Z. Podman's docker CLI: 2026-11-11 10:00:00.1 +0000 UTC.
    None when unreadable, which means no warning."""
    parts = text.strip().replace("T", " ").split()
    if len(parts) < 2:
        return None
    # lazy: a non-UTC offset (podman) is read as UTC; at worst a warning is an hour or so off
    clock = parts[1].rstrip("Z")
    seconds, _, fraction = clock.partition(".")
    try:
        stamp = datetime.strptime(f"{parts[0]} {seconds}", "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return stamp.replace(tzinfo=UTC).timestamp() + (
        float(f"0.{fraction}") if fraction.isdigit() else 0
    )


def inspect_created(engine, image: str) -> tuple[bool, float | None]:
    """(the image exists, when it was made), from one `inspect` call."""
    done = engine("image", "inspect", "--format", "{{.Created}}", image, check=False)
    if done.returncode != 0:
        return False, None
    return True, parse_created(done.stdout.decode(errors="replace"))


def container_warning(
    created: float | None, image: str, root: Path = ROOT
) -> list[str]:
    if created is None or not (root / "setup" / "Dockerfile").exists():
        return []
    newest = max(
        (p.stat().st_mtime for g in CODE_GLOBS for p in root.glob(g) if p.is_file()),
        default=0,
    )
    if newest > created:
        return [
            (
                f"the container image {image} is older than the code in this checkout: "
                "`art98 up --rebuild`"
            )
        ]
    return []


def stale_warnings(work: Path | str, root: Path = ROOT) -> list[str]:
    """What is out of date, one line each. [] when all is current or unknowable."""
    guest = root / "guest"
    if not guest.is_dir():
        return []
    out = []
    for built, source in PROGRAMS:
        binary, text = guest / "build" / built, guest / source
        if (
            binary.exists()
            and text.exists()
            and binary.stat().st_mtime < text.stat().st_mtime
        ):
            out.append(
                f"guest/build/{built} is older than {source}: run `make -C guest`, "
                "or `art98 up --rebuild` on a container-only host"
            )
    image, agent = Path(work) / "image.json", guest / "call_proxy" / "agent.c"
    if (
        image.exists()
        and agent.exists()
        and image.stat().st_mtime < agent.stat().st_mtime
    ):
        out.append(
            "the prepared image was built before the agent changed; scenes run the old "
            "AGENT.EXE. Rebuilding it means a full reinstall: `art98 setup --from vm`"
        )
    return out


def warn(work) -> None:
    for line in stale_warnings(work):
        print(f"warning: {line}", file=sys.stderr)
