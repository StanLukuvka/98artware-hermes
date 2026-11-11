"""A log of animation script runs (art98_render), one JSON object per line in history.jsonl
in the plugin's data folder (Hermes's plugin-data/art98/).

Each run: {"t": unix time, "script": "intro.py", "ok": bool, "code": error code or null,
"message": error text, "recording": file in recordings/ when it worked, else null, "ms": float}.

No imports from the rest of the plugin, so dashboard/plugin_api.py can load it by path.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path



def _data_root() -> Path:
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


def _file() -> Path:
    return _data_root() / "history.jsonl"

_lock = threading.Lock()


def append(script: str, ok: bool, code: str | None = None, message: str = "",
           recording: str | None = None, ms: float = 0.0) -> None:
    entry = {"t": time.time(), "script": script, "ok": ok, "code": code,
             "message": (message or "")[:300], "recording": recording if ok else None, "ms": round(ms, 1)}
    try:
        with _lock, _file().open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except OSError:
        pass  # history is best-effort; a run never fails because of it


def log_run(script: str, out: Path, ok: bool, code: str | None, message: str, started: float) -> None:
    """One finished render: its recording counts only if it worked and the file exists.
    `started` is time.monotonic() when the run began. Used by the agent tool and the dashboard."""
    append(script, ok, code, message, out.name if ok and out.is_file() else None,
           (time.monotonic() - started) * 1000)


def recent(limit: int = 50) -> list[dict]:
    """The newest runs first. Lines in an older format (no "script") are skipped."""
    try:
        lines = _file().read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    out = []
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and "script" in entry:
            out.append(entry)
            if len(out) >= limit:
                break
    return out
