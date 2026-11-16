"""Warnings when the image or the guest programs are older than the code.

Warnings only: nothing here stops a render. The guest programs are checked by
content (guest/build/sources.sha256), the rest by git commit time, because a clone
or pull resets file mtimes. An installed package (no guest/ or setup/Dockerfile beside it) gets none. The
engine image check (`container_warning`) is for `vm status` and `up`.
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
# (built file under guest/build, its source under guest/)
# (renders never use mbr.bin, only setup does, so it is not checked here)
PROGRAMS = (("agent.exe", "call_proxy/agent.c"), ("view.exe", "picture_viewer/view.c"))
CODE_GLOBS = (
    "core/art98/**/*.py",
    "setup/art98_setup/**/*.py",
    "guest/**/*.c",
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


def digests(path: Path) -> dict[str, str] | None:
    """Parse `sha256sum` output into {name: hex}. None when the file is absent or unreadable."""
    try:
        text = path.read_text()
    except (OSError, UnicodeDecodeError):
        return None
    found = {}
    for line in text.splitlines():
        hexsum, _, name = line.partition("  ")
        if hexsum and name:
            found[name.lstrip("*")] = hexsum
    return found


def source_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(root: Path, *args: str) -> str | None:
    """Stdout of `git -C root ...`, None on any failure."""
    try:
        done = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.decode(errors="replace") if done.returncode == 0 else None


def _content_commit_time(root: Path, path: str) -> float | None:
    """Time of the newest commit that changed `path`'s content: pure moves (R100) are skipped.
    0 when no commit has it, None on a git failure."""
    log = _git(
        root, "log", "--follow", "-M", "--name-status", "--format=%ct", "--", path
    )
    if log is None:
        return None
    when = 0.0
    for line in log.splitlines():
        if line.isdigit():
            when = float(line)
        elif line and not line.startswith("R100"):
            return when
    return 0.0


def code_changed_since(root: Path, paths: list[str], since: float) -> bool:
    """True when a listed file (relative to root) changed after `since`.

    In a git checkout of root: the last commit time of the files, plus the mtime of files
    git lists as modified or untracked (a pull resets mtimes but not commit times).
    Otherwise (no git, not root's own repo, any git failure): the mtime rule, only for
    files over 60 s newer than `since`."""
    files = [p for p in paths if (root / p).is_file()]
    if not files:
        return False
    top = _git(root, "rev-parse", "--show-toplevel")
    if top is not None and Path(top.strip()).resolve() == root.resolve():
        times = [_content_commit_time(root, f) for f in files]
        status = _git(root, "status", "--porcelain", "--", *files)
        if None not in times and status is not None:
            if max(times) > since:  # type: ignore[type-var]
                return True
            dirty = [ln[3:].split(" -> ")[-1].strip('"') for ln in status.splitlines()]
            return any(
                (root / d).is_file() and (root / d).stat().st_mtime > since
                for d in dirty
            )
    # lazy: without git a pull still reads as a change after 60 s; upgrade: image.json digest
    return any((root / p).stat().st_mtime > since + 60 for p in files)


def container_warning(
    created: float | None, image: str, root: Path = ROOT
) -> list[str]:
    if created is None or not (root / "setup" / "Dockerfile").exists():
        return []
    code = [str(p.relative_to(root)) for g in CODE_GLOBS for p in root.glob(g)]
    if code_changed_since(root, code, created):
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
    built_from = digests(guest / "build" / "sources.sha256") or {}
    for built, source in PROGRAMS:
        text = guest / source
        if (
            (guest / "build" / built).exists()
            and text.is_file()
            and built_from.get(source) not in (None, source_digest(text))
        ):
            out.append(
                f"guest/build/{built} was built from an older {source}: run "
                "`make -C guest`, or `art98 up --rebuild` on a container-only host"
            )
    image = Path(work) / "image.json"
    if image.exists() and code_changed_since(
        root, ["guest/call_proxy/agent.c"], image.stat().st_mtime
    ):
        out.append(
            "the prepared image was built before the agent changed; scenes run the old "
            "AGENT.EXE. Rebuilding it means a full reinstall: `art98 setup --from vm`"
        )
    return out


def warn(work) -> None:
    for line in stale_warnings(work):
        print(f"warning: {line}", file=sys.stderr)
