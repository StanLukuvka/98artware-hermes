"""The compose project the containers run in: its files, its name and its variables.

compose.yaml at the repository root says how the containers run. This module only
passes it what lives elsewhere: the art98.toml settings (as ART98_* variables, so
nothing is defined twice), the transport, and the user's compose.override.yaml.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from art98.container.freshness import ROOT
from art98.settings import Settings

from .host import transport_name

FILE = ROOT / "compose.yaml"
TCP_FILE = ROOT / "compose.tcp.yaml"  # on top of FILE when the transport is tcp
OVERRIDE_FILE = ROOT / "compose.override.yaml"  # the user's own, gitignored


def _tag(settings: Settings) -> str:
    return hashlib.sha256(str(settings.work_dir).encode()).hexdigest()[:8]


def socket_dir(settings: Settings) -> Path:
    """Deterministic per work path, so two checkouts never share one."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = (
        Path(runtime) / "art98"
        if runtime
        else Path(tempfile.gettempdir())
        / f"art98-{os.getuid() if hasattr(os, 'getuid') else 0}"
    )
    return base / f"vm-{_tag(settings)}"


def project(settings: Settings) -> str:
    """One project per work folder, so `down` never touches another checkout's VM."""
    return f"art98-{_tag(settings)}"


def files(settings: Settings) -> list[Path]:
    tcp = transport_name(settings.transport) == "tcp"
    extra = [TCP_FILE] if tcp else []
    return [FILE, *extra, *([OVERRIDE_FILE] if OVERRIDE_FILE.exists() else [])]


def command(settings: Settings) -> list[str]:
    """`compose` and its global options, for the engine to run."""
    picked = [arg for path in files(settings) for arg in ("-f", str(path))]
    return [
        "compose",
        "-p",
        project(settings),
        "--project-directory",
        str(ROOT),
        *picked,
    ]


def variables(settings: Settings) -> dict[str, str]:
    """The values compose.yaml reads that art98 owns."""
    values = {
        "ART98_IMAGE": settings.image,
        "ART98_WORK": str(settings.work_dir),
        "ART98_TOKEN_PATH": str(settings.token_path),
        "ART98_SOCKET_DIR": str(socket_dir(settings)),
        "ART98_CONTROL_PORT": str(settings.control),
    }
    if hasattr(os, "getuid"):  # see art98.container.entry
        values["ART98_HOST_UID"] = str(os.getuid())
        values["ART98_HOST_GID"] = str(os.getgid())
    return values


def run(engine, settings: Settings, *args: str, extra: dict | None = None, **kw):
    """`<engine> compose ... args` with art98's variables on top of the environment."""
    env = {**os.environ, **variables(settings), **(extra or {})}
    return engine(*command(settings), *args, env=env, **kw)
