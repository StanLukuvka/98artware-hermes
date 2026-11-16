"""The one compatibility shim: names the Hermes agent plugin still uses.

The plugin (a separate repo) bundles art98 frozen at 94f0fd3 and talks to a VM server run
from this checkout. Re-vendoring the current art98 into it must keep working without editing
the plugin, so the old names live here and nowhere else. This module exists only for that
plugin; delete it when the plugin is re-vendored and updated to the current names. Nothing
else in art98 may import from it except `anim/scene.py` (`scene_fps`) and `art98/__init__.py`.

What the plugin's `desk.py` and its scene template rely on:

- `art98.service` and `art98.win32`, the old flat module paths (aliased below).
- `service.attach`, `service._connect`, `service._read_status`, `service._address`,
  `service._token`, `service.Busy`, `NotRunning`, `ServiceError`. The private ones are called
  by name, so they must keep their names and signatures: `_read_status(address, token,
  timeout=)`, `_address(settings)`, `_token(settings)`.
- `settings.find`, `settings._read` and `Settings(**_read(path), source=path, root=..., token=...)`:
  `source` was a Settings field; it is accepted and dropped (`accept_settings_source`).
- `win32.mouse_button`, `actions.keyboard` (KEY_HOLD_MS, KEY_SETTLE, char_chord, key_chord),
  `cli.main` (`render FILE [SCENE] -o OUT --wait N`), and `from art98.anim import *`.
- `Scene.fps = 24` in the skill's template: an alias of `output_fps` (`scene_fps`).

The wire exchange needs no shim: serve's handshake, status line, control ops and agent relay
are unchanged since 94f0fd3 (pinned by `core/tests/test_legacy.py`).
"""

from __future__ import annotations

import functools
import importlib
import sys

from .errors import UsageError
from .settings import Settings

# old flat module path -> where it lives now
MODULES = {
    "art98.service": "art98.container.service",
    "art98.win32": "art98.guest.win32",
}


def alias_modules() -> None:
    """Make `import art98.service` and `from art98 import service` give the new module."""
    package = sys.modules["art98"]
    for old, new in MODULES.items():
        module = importlib.import_module(new)
        sys.modules[old] = module
        setattr(package, old.rpartition(".")[2], module)


def accept_settings_source() -> None:
    """Settings(source=...) still constructs: the old field is accepted and ignored."""
    original = Settings.__init__

    @functools.wraps(original)
    def init(self, *args, source=None, **kwargs):
        original(self, *args, **kwargs)

    Settings.__init__ = init


def scene_fps(cls) -> None:
    """A scene class that sets `fps` gets the same value as `output_fps`."""
    if "fps" not in vars(cls):
        return
    if "output_fps" in vars(cls) and vars(cls)["output_fps"] != vars(cls)["fps"]:
        raise UsageError(
            f"{cls.__name__} sets both fps and output_fps; keep output_fps"
        )
    cls.output_fps = vars(cls)["fps"]


alias_modules()
accept_settings_source()
