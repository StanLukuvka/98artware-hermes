"""Plugin settings. Defaults and checks live here; plugin.yaml's config_schema declares the same keys.

Inside Hermes the values live in Hermes's config.yaml under plugins.entries.art98.settings: read
with hermes_cli.config.load_config_readonly, written with ctx.set_config (register() hands ctx over
with bind()). Hermes's own settings form edits the same keys. Outside Hermes (tests, demo.py) they
fall back to config.json in the data folder.

No imports from the rest of the plugin, so dashboard/plugin_api.py can load it by path.
"""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

PLUGIN_ID = "art98"
PLUGIN_DIR = Path(__file__).resolve().parent
_lock = threading.Lock()
_ctx = None  # the PluginContext, once register() binds it

# name: (default, kind, rule). kind is the JSON type; rule is a tuple of choices or (min, max).
FIELDS: dict[str, tuple[Any, type, Any]] = {
    "backend": ("stub", str, ("stub", "demo", "drive")),  # which Desk the tools use; demo = fake data
    "desktop": ("blank", str, ("blank", "stock")),      # what a revert starts from
    "screenshot_after": (True, bool, None),             # default for the action tools
    "allow_win32": (False, bool, None),                 # art98_win32 refuses unless on
    "live_interval_ms": (1000, int, (250, 10000)),      # dashboard live view refresh
    "record_fps": (30, int, (1, 60)),
    "host_folder": ("", str, None),                     # the data folder as the PC sees it, for VS Code links
}


class ConfigError(ValueError):
    pass


def _data_root() -> Path:
    """Hermes's plugin-data/art98/ (kept across plugin updates), ART98_DATA, or beside the code."""
    if os.environ.get("ART98_DATA"):
        root = Path(os.environ["ART98_DATA"])
    else:
        try:
            from plugins.plugin_storage import plugin_data_dir
            return plugin_data_dir(PLUGIN_ID)
        except Exception:  # noqa: BLE001  (not running inside Hermes)
            root = PLUGIN_DIR
    root.mkdir(parents=True, exist_ok=True)
    return root


def bind(ctx) -> None:
    """Called from register(ctx): settings are saved through ctx.set_config from then on."""
    global _ctx
    _ctx = ctx


def _bound_ctx():
    """This module's ctx, or the one bound in the plugin's own copy of config.py (the dashboard
    loads config.py by path, so it is a second module object in the same process)."""
    if _ctx is not None:
        return _ctx
    import sys
    for module in list(sys.modules.values()):
        if module is not sys.modules.get(__name__) and getattr(module, "__file__", None) == __file__:
            if getattr(module, "_ctx", None) is not None:
                return module._ctx
    return None


def _fallback_file() -> Path:
    return _data_root() / "config.json"


def _saved() -> dict:
    try:
        from hermes_cli.config import load_config_readonly
    except ImportError:
        try:
            saved = json.loads(_fallback_file().read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {}
        return saved if isinstance(saved, dict) else {}
    entries = ((load_config_readonly() or {}).get("plugins") or {}).get("entries") or {}
    settings = (entries.get(PLUGIN_ID) or {}).get("settings") if isinstance(entries, dict) else None
    return settings if isinstance(settings, dict) else {}


def defaults() -> dict[str, Any]:
    return {name: spec[0] for name, spec in FIELDS.items()}


def load() -> dict[str, Any]:
    """Saved values over the defaults; unknown or broken entries are ignored."""
    values = defaults()
    for name, value in _saved().items():
        try:
            values[name] = _check(name, value)
        except ConfigError:
            pass
    return values


def _check(name: str, value: Any) -> Any:
    if name not in FIELDS:
        raise ConfigError(f"unknown setting {name!r}")
    _, kind, rule = FIELDS[name]
    if kind is int and isinstance(value, bool) or not isinstance(value, kind):
        raise ConfigError(f"{name} must be {kind.__name__}")
    if isinstance(rule, tuple) and kind is str and value not in rule:
        raise ConfigError(f"{name} must be one of {', '.join(rule)}")
    if isinstance(rule, tuple) and kind is int and not rule[0] <= value <= rule[1]:
        raise ConfigError(f"{name} must be between {rule[0]} and {rule[1]}")
    return value


def update(changes: dict[str, Any]) -> dict[str, Any]:
    """Check every change first, then save them all, or none."""
    if not isinstance(changes, dict):
        raise ConfigError("expected an object of settings")
    checked = {name: _check(name, value) for name, value in changes.items()}
    with _lock:
        ctx = _bound_ctx()
        if ctx is not None:
            try:
                for name, value in checked.items():
                    ctx.set_config(name, value)
            except (ValueError, PermissionError) as e:
                raise ConfigError(str(e)) from e
            return load()
        try:
            import hermes_cli.config  # noqa: F401
        except ImportError:  # outside Hermes
            values = {**load(), **checked}
            _fallback_file().write_text(json.dumps(values, indent=2) + "\n", encoding="utf-8")
            return values
        raise ConfigError("can't save here: the art98 plugin isn't loaded in this process. Change it in "
                          "Hermes's plugin settings, or: hermes config set plugins.entries.art98.settings.<name> <value>")


def schema() -> dict[str, dict[str, Any]]:
    """What the dashboard needs to draw the form."""
    out = {}
    for name, (default, kind, rule) in FIELDS.items():
        entry: dict[str, Any] = {"type": kind.__name__, "default": default}
        if isinstance(rule, tuple) and kind is str:
            entry["choices"] = list(rule)
        elif isinstance(rule, tuple):
            entry["min"], entry["max"] = rule
        out[name] = entry
    return out
