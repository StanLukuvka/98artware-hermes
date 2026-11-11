"""The plugin's data folders, in data_root() (Hermes's plugin-data/art98/), not beside the code.

recordings/   videos the tool records or renders (.mp4, .gif)
animations/   art98 scene scripts the tool can render (.py)
states/       desktop state files a session can start from (.json)
screenshots/  frames the tool captured (.png)

No imports from the rest of the plugin, so dashboard/plugin_api.py can load it by path.
"""

from __future__ import annotations

import ast
import os
import shutil
from pathlib import Path

SHIPPED = Path(__file__).resolve().parent  # files that come with the plugin (starter states)
KINDS = {
    "recordings": (".mp4", ".gif"),
    "animations": (".py",),
    "states": (".json",),
    "screenshots": (".png",),
}



def data_root() -> Path:
    """Where the plugin keeps its files: Hermes's plugin-data/art98/ (kept across plugin updates,
    follows the active profile), ART98_DATA if set, or beside the code when run outside Hermes."""
    if os.environ.get("ART98_DATA"):
        root = Path(os.environ["ART98_DATA"])
    else:
        try:
            from plugins.plugin_storage import plugin_data_dir
            return plugin_data_dir("art98")
        except Exception:  # noqa: BLE001  (not running inside Hermes)
            root = Path(__file__).resolve().parent
    root.mkdir(parents=True, exist_ok=True)
    return root


def folder(kind: str) -> Path:
    if kind not in KINDS:
        raise ValueError(f"unknown media kind {kind!r}; choose from {', '.join(KINDS)}")
    path = data_root() / kind
    if not path.is_dir():
        path.mkdir(parents=True)
        if (SHIPPED / kind).is_dir() and SHIPPED / kind != path:  # first use: copy the starter files
            for f in (SHIPPED / kind).iterdir():
                if f.is_file() and f.suffix.lower() in KINDS[kind]:
                    shutil.copy2(f, path / f.name)
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
    if not name or "\x00" in name or Path(name).name != name or name.startswith("."):
        raise ValueError(f"bad file name {name!r}")
    return folder(kind) / name


def counts() -> dict[str, int]:
    return {kind: len(listing(kind)) for kind in KINDS}


def scene_classes(source: str) -> list[str] | None:
    """Scene class names a script defines, read with ast (scene code is never run here).

    A class counts when a base is `Scene` (or `x.Scene`) or another scene class in the file.
    A scene built on a base from another file isn't seen; then the list is empty and the
    render picks the file's only scene itself. None: the file defines no class at all, so it
    is a helper other scripts import, not something to render. Raises SyntaxError (or
    ValueError for a null byte) when the source isn't valid Python.
    """
    classes = [node for node in ast.parse(source).body if isinstance(node, ast.ClassDef)]
    if not classes:
        return None

    def base_name(base):
        return base.id if isinstance(base, ast.Name) else base.attr if isinstance(base, ast.Attribute) else None

    scenes = {"Scene"}
    grew = True
    while grew:
        grew = False
        for node in classes:
            if node.name not in scenes and any(base_name(b) in scenes for b in node.bases):
                scenes.add(node.name)
                grew = True
    return [node.name for node in classes if node.name in scenes and node.name != "Scene"]
