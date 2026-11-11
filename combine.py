"""Join videos end to end with ffmpeg, into one .mp4 (or .gif).

Clips of different sizes are scaled to fit the output size (nearest-neighbour, so Win98
pixels stay sharp) and letterboxed on black; frame rates are evened out. Audio is
dropped (audio is deferred in art98). An optional fade cross-dissolves between clips.

As a tool: art98_combine (tools.py) joins files from recordings/ into recordings/.
On its own, with any paths:

    python3 combine.py out.mp4 a.mp4 b.gif c.mp4 [--fade 0.5] [--size 1920x1080] [--fps 30]

No imports from the rest of the plugin, so it can be loaded or run on its own.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

TIMEOUT_S = 900


class CombineError(RuntimeError):
    pass


def _need(tool: str) -> str:
    path = shutil.which(tool)
    if not path:
        raise CombineError(f"{tool} is not installed")
    return path


def probe(path: Path) -> dict:
    """Width, height and duration (seconds) of a video's first video stream."""
    out = subprocess.run(
        [_need("ffprobe"), "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=width,height,duration,nb_read_packets,r_frame_rate:format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise CombineError(f"can't read {path.name}: {out.stderr.strip() or 'not a video'}")
    data = json.loads(out.stdout)
    if not data.get("streams"):
        raise CombineError(f"{path.name} has no video stream")
    s = data["streams"][0]
    duration = s.get("duration") or data.get("format", {}).get("duration")
    if duration in (None, "N/A"):  # some GIFs: frames / rate
        num, den = (s.get("r_frame_rate") or "10/1").split("/")
        duration = int(s.get("nb_read_packets") or 0) / (float(num) / float(den or 1))
    return {"width": int(s["width"]), "height": int(s["height"]), "duration": float(duration)}


def _even(n: int) -> int:
    return n + (n % 2)


def combine(inputs: list[Path], out: Path, *, size: tuple[int, int] | None = None,
            fps: int = 30, fade: float = 0.0) -> dict:
    """Join inputs in order into out. size defaults to the first clip's. Returns a summary."""
    if len(inputs) < 2:
        raise CombineError("give at least two videos to combine")
    for p in inputs:
        if not p.is_file():
            raise CombineError(f"no such video: {p.name}")
        if p.resolve() == out.resolve():
            raise CombineError(f"the output {out.name} is also an input")
    if not 1 <= fps <= 60:
        raise CombineError("fps must be between 1 and 60")
    info = [probe(p) for p in inputs]
    w, h = size or (info[0]["width"], info[0]["height"])
    w, h = _even(int(w)), _even(int(h))
    if fade < 0 or (fade and fade >= min(i["duration"] for i in info)):
        raise CombineError(f"fade must be shorter than the shortest clip ({min(i['duration'] for i in info):.2f} s)")

    # Each clip: fit inside w x h without blurring, pad to exactly w x h, same rate and format.
    chains = [
        f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease:flags=neighbor,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,fps={fps},format=yuv420p,"
        f"settb=AVTB,setpts=PTS-STARTPTS[v{i}]"
        for i in range(len(inputs))]
    if fade:
        last, offset = "v0", 0.0
        for i in range(1, len(inputs)):
            offset += info[i - 1]["duration"] - fade
            label = f"x{i}"
            chains.append(f"[{last}][v{i}]xfade=transition=fade:duration={fade}:offset={offset:.3f}[{label}]")
            last = label
        total = sum(i["duration"] for i in info) - fade * (len(inputs) - 1)
    else:
        chains.append("".join(f"[v{i}]" for i in range(len(inputs))) + f"concat=n={len(inputs)}:v=1:a=0[x]")
        last, total = "x", sum(i["duration"] for i in info)

    graph = ";".join(chains)
    if out.suffix.lower() == ".gif":
        graph += f";[{last}]split[g1][g2];[g1]palettegen=stats_mode=diff[p];[g2][p]paletteuse[out]"
        codec = []
    else:
        graph += f";[{last}]null[out]"
        codec = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "18", "-movflags", "+faststart"]

    args = [_need("ffmpeg"), "-y", "-loglevel", "error"]
    for p in inputs:
        args += ["-i", str(p)]
    args += ["-filter_complex", graph, "-map", "[out]", "-an", *codec, str(out)]
    tmp = out.with_name(out.stem + ".part" + out.suffix)
    args[-1] = str(tmp)  # write aside, then move: a failed join never leaves a half file
    try:
        run = subprocess.run(args, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        raise CombineError(f"ffmpeg took longer than {TIMEOUT_S} s")
    if run.returncode != 0:
        tmp.unlink(missing_ok=True)
        raise CombineError("ffmpeg failed: " + (run.stderr.strip().splitlines() or ["no output"])[-1])
    tmp.replace(out)
    return {"output": out.name, "inputs": [p.name for p in inputs], "size": f"{w}x{h}", "fps": fps,
            "fade": fade, "duration": round(total, 2), "bytes": out.stat().st_size}


def _size(text: str) -> tuple[int, int]:
    try:
        w, h = (int(v) for v in text.lower().split("x"))
        return w, h
    except ValueError:
        raise argparse.ArgumentTypeError("size must look like 1920x1080")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Join videos end to end (silent output).")
    ap.add_argument("output", type=Path, help="the joined video, .mp4 or .gif")
    ap.add_argument("inputs", type=Path, nargs="+", help="videos to join, in order")
    ap.add_argument("--size", type=_size, help="output WxH (default: the first clip's size)")
    ap.add_argument("--fps", type=int, default=30)
    ap.add_argument("--fade", type=float, default=0.0, help="cross-fade seconds between clips")
    a = ap.parse_args(argv)
    try:
        print(json.dumps(combine(a.inputs, a.output, size=a.size, fps=a.fps, fade=a.fade), indent=2))
    except CombineError as e:
        print(f"combine: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
