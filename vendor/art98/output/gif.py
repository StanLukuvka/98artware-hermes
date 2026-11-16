"""Converting a video to a GIF, and the GIF defaults.

Kept apart from `anim` so the command line can read the defaults without
loading the animation package.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .recorder import run_ffmpeg

GIF_FPS = 15
GIF_SCALE = 1.0
GIF_DITHER = "none"
GIF_COLORS = 128


@dataclass(frozen=True)
class GifOptions:
    """How a GIF is made: frames per second, palette size, size factor, ffmpeg dither."""

    fps: int = GIF_FPS
    colors: int = GIF_COLORS
    scale: float = GIF_SCALE
    dither: str = GIF_DITHER


def write_gif(
    source: Path | str,
    output: Path | str,
    options: GifOptions | None = None,
    stage: bool = False,
) -> Path:
    """Convert a video to a GIF with its own palette, keeping pixels sharp.

    With stage, the GIF is left as `part_path(output)` for the caller to rename.
    """
    o = options or GifOptions()
    size = "" if o.scale == 1 else f"scale=iw*{o.scale}:ih*{o.scale}:flags=neighbor,"
    graph = (
        f"fps={o.fps},{size}split[a][b];"
        f"[a]palettegen=max_colors={o.colors}:stats_mode=diff[palette];"
        f"[b][palette]paletteuse=dither={o.dither}:diff_mode=rectangle"
    )
    run_ffmpeg(
        ["-i", str(source), "-filter_complex", graph, "-loop", "0"],
        Path(output),
        "making the GIF",
        stage,
    )
    return Path(output)
