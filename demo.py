"""Placeholder data so the dashboard tab has something to show before a real VM exists.

    python3 demo.py seed    # demo_* files in the media folders, sample history, backend = demo
    python3 demo.py clear   # removes exactly those again, backend back to stub

Only files named demo_* and history lines with "demo": true are touched.
No plugin imports at module level, so desk.py can load this file by path.
"""

from __future__ import annotations

import json
import os
import random
import shutil
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREFIX = "demo_"


def screen_png(colour=(0, 128, 128), w=800, h=600) -> bytes:
    """A plain teal 800x600 PNG standing in for the guest screen."""
    raw = (b"\x00" + bytes(colour) * w) * h
    chunk = lambda tag, data: struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def activity(t: float | None = None) -> dict | None:
    """A 45 s loop: render 20 s, idle 10 s, record 15 s."""
    t = time.time() if t is None else t
    phase, start = t % 45, t - t % 45
    if phase < 20:
        return {"kind": "render", "name": PREFIX + "start_menu_tour.py", "since": start}
    if phase < 30:
        return None
    return {"kind": "record", "name": PREFIX + "notepad_typing.mp4", "since": start + 30}


SCRIPTS = ["start_menu_tour", "notepad_typing", "paint_doodle", "explorer_folder_hop",
           "minesweeper_first_click", "a_rather_long_scene_name_that_should_truncate"]
STATES = ["clean_desktop", "notepad_open", "paint_maximized"]
FAILS = [("render_failed", "scene raised NameError on line 12"), ("timeout", "render took longer than 120 s"),
         ("not_connected", "no VM attached")]


def _touch(p: Path, data: bytes, rnd: random.Random, now: float) -> None:
    p.write_bytes(data)
    t = now - rnd.uniform(300, 5 * 86400)
    os.utime(p, (t, t))


def seed() -> None:
    sys.path.insert(0, str(ROOT))
    import config, media  # noqa: E401,E402

    now, rnd = time.time(), random.Random(98)
    for n in SCRIPTS:
        _touch(media.folder("animations") / f"{PREFIX}{n}.py", f"# placeholder scene: {n}\n".encode(), rnd, now)
    for n in STATES:
        _touch(media.folder("states") / f"{PREFIX}{n}.json", b"{}\n", rnd, now)
    for i in range(3):
        _touch(media.folder("screenshots") / f"{PREFIX}frame_{i + 1}.png", screen_png(), rnd, now)
    # 12 runs over the last few hours; most work and leave a 2 s test-pattern clip.
    runs, t = [], now - 4 * 3600
    for _ in range(12):
        t += rnd.uniform(600, 1200)
        script = rnd.choice(SCRIPTS[:-1])
        fail = rnd.choice(FAILS) if rnd.random() < 0.3 else None
        rec = None
        if not fail and shutil.which("ffmpeg"):
            rec = f"{PREFIX}{script}_{len(runs) + 1:02d}.mp4"
            out = media.folder("recordings") / rec
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                            f"testsrc=size=800x600:rate=10:duration=2", "-pix_fmt", "yuv420p", str(out)], check=True)
            os.utime(out, (t, t))
        runs.append({"t": t, "script": f"{PREFIX}{script}.py", "ok": not fail, "code": fail and fail[0],
                     "message": fail[1] if fail else "", "recording": rec,
                     "ms": round(rnd.uniform(4000, 30000), 1), "demo": True})
    with (media.data_root() / "history.jsonl").open("a", encoding="utf-8") as f:
        for r in runs:
            f.write(json.dumps(r) + "\n")

    config.update({"backend": "demo"})
    print(f"seeded {len(SCRIPTS)} scripts, {len(STATES)} states, 3 screenshots, {len(runs)} runs "
          f"({sum(1 for r in runs if r['recording'])} with recordings)")


def clear() -> None:
    sys.path.insert(0, str(ROOT))
    import config, media  # noqa: E401,E402

    for kind in media.KINDS:
        for p in media.folder(kind).glob(PREFIX + "*"):
            p.unlink()
    hist = media.data_root() / "history.jsonl"
    if hist.exists():
        keep = [l for l in hist.read_text(encoding="utf-8").splitlines() if l.strip() and '"demo": true' not in l]
        hist.write_text("".join(l + "\n" for l in keep), encoding="utf-8")
    if config.load()["backend"] == "demo":
        config.update({"backend": "stub"})
    print("demo data removed")


if __name__ == "__main__":
    {"seed": seed, "clear": clear}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit("usage: demo.py seed|clear"))()
