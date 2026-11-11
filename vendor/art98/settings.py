"""The one home of names and ports: built-in defaults, overridden by art98.toml.

Precedence: defaults < art98.toml < ART98_IMAGE (image only) < the --work flag.
ART98_ENGINE and ART98_TRANSPORT are read where they always were, not here
(ART98_TRANSPORT beats [vm] transport).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_NAME = "art98.toml"
# Ports INSIDE the image, fixed; the host-side ports are Settings fields.
CONTAINER_CONTROL_PORT = 7598
CONTAINER_VNC_PORT = 5900


class SettingsError(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    container: str = "art98-vm"
    image: str = "localhost/art98:latest"
    work: str = "work"
    bind: str = "127.0.0.1"
    connect: str | None = None
    transport: str | None = None
    token_file: str | None = None
    # The token itself; never from art98.toml.
    token: str | None = field(default=None, repr=False)
    control: int = 7598
    vnc: int = 5900
    setup_vnc: int = 5998
    source: Path | None = None
    root: Path = Path(".")

    @property
    def work_dir(self) -> Path:
        return (self.root / self.work).resolve()

    @property
    def connect_host(self) -> str:
        """The host a client dials: `connect`, else `bind`, else loopback for a wildcard."""
        if self.connect:
            return self.connect
        return "127.0.0.1" if self.bind in ("0.0.0.0", "::") else self.bind

    @property
    def token_path(self) -> Path:
        """Where vm.token lives: `token_file` against the toml's folder, else in work/."""
        if self.token_file:
            return self.root / Path(self.token_file).expanduser()
        return self.work_dir / "vm.token"


SECTIONS = {
    "vm": {
        "container": str,
        "image": str,
        "work": str,
        "transport": str,
        "token_file": str,
    },
    "ports": {
        "bind": str,
        "connect": str,
        "control": int,
        "vnc": int,
        "setup_vnc": int,
    },
}
TRANSPORTS = ("unix", "tcp")  # also host.TRANSPORTS; settings cannot import host


def find(start: Path | None = None) -> Path | None:
    """The first art98.toml from `start` (default: the cwd) up to the root."""
    here = Path(start or Path.cwd()).resolve()
    for folder in (here, *here.parents):
        if (folder / CONFIG_NAME).is_file():
            return folder / CONFIG_NAME
    return None


def _read(path: Path) -> dict:
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise SettingsError(f"{path}: {error}") from error
    values: dict = {}
    for section, body in data.items():
        keys = SECTIONS.get(section)
        if keys is None or not isinstance(body, dict):
            raise SettingsError(f"{path}: unknown section [{section}]")
        for key, value in body.items():
            kind = keys.get(key)
            if kind is None:
                raise SettingsError(f"{path}: unknown key {section}.{key}")
            if type(value) is not kind or (kind is str and not value):
                raise SettingsError(
                    f"{path}: {section}.{key} must be a "
                    f"{'non-empty string' if kind is str else 'whole number'}"
                )
            if kind is int and not 1 <= value <= 65535:
                raise SettingsError(f"{path}: {section}.{key} is not a port: {value}")
            if key == "transport" and value not in TRANSPORTS:
                raise SettingsError(
                    f"{path}: vm.transport must be one of {', '.join(TRANSPORTS)}"
                )
            values[key] = value
    return values


def load(work: Path | str | None = None) -> Settings:
    """Settings for the cwd; `work` is the --work flag (resolved against the cwd)."""
    path = find()
    values = _read(path) if path else {}
    if os.environ.get("ART98_IMAGE"):
        values["image"] = os.environ["ART98_IMAGE"]
    if work is not None:
        values["work"] = str(Path(work).resolve())
    return Settings(**values, source=path, root=path.parent if path else Path.cwd())
