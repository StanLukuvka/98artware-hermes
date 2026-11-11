"""The plugin's data folders, beside its code.

recordings/   videos the tool records or renders (.mp4, .gif)
animations/   art98 scene scripts the tool can render (.py)
states/       desktop state files a session can start from (.json)
screenshots/  frames the tool captured (.png)

No imports from the rest of the plugin, so dashboard/plugin_api.py can load it by path.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent
KINDS = {
    "recordings": (".mp4", ".gif"),
    "animations": (".py",),
    "states": (".json",),
    "screenshots": (".png",),
}


def folder(kind: str) -> Path:
    if kind not in KINDS:
        raise ValueError(f"unknown media kind {kind!r}; choose from {', '.join(KINDS)}")
    path = ROOT / kind
    path.mkdir(exist_ok=True)
    return path


def listing(kind: str) -> list[dict]:
    """Files of one kind, newest first."""
    files = [
        p for p in folder(kind).iterdir()
        if p.is_file() and p.suffix.lower() in KINDS[kind]
    ]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.name, "bytes": p.stat().st_size, "mtime": p.stat().st_mtime} for p in files]


def resolve(kind: str, name: str) -> Path:
    """A file inside one media folder; names with a path in them are refused."""
    if not name or Path(name).name != name or name.startswith("."):
        raise ValueError(f"bad file name {name!r}")
    return folder(kind) / name


def counts() -> dict[str, int]:
    return {kind: len(listing(kind)) for kind in KINDS}
