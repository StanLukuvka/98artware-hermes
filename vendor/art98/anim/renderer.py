"""Rendering a scene: VM, recorder, output file."""

from __future__ import annotations

import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

from ..recorder import (
    GIF_COLORS,
    GIF_FPS,
    H264,
    LOSSLESS_SUFFIX,
    RecorderError,
    part_path,
    require_ffmpeg,
    run_ffmpeg,
)


def write_gif(
    source: Path | str,
    output: Path | str,
    fps: int = GIF_FPS,
    colors: int = GIF_COLORS,
    scale: float = 1.0,
    dither: str = "none",
    stage: bool = False,
) -> Path:
    """Convert a video to a GIF with its own palette, keeping pixels sharp.

    With stage, the GIF is left as `part_path(output)` for the caller to rename.
    """
    size = "" if scale == 1 else f"scale=iw*{scale}:ih*{scale}:flags=neighbor,"
    graph = (
        f"fps={fps},{size}split[a][b];"
        f"[a]palettegen=max_colors={colors}:stats_mode=diff[palette];"
        f"[b][palette]paletteuse=dither={dither}:diff_mode=rectangle"
    )
    run_ffmpeg(
        ["-i", str(source), "-filter_complex", graph, "-loop", "0"],
        Path(output),
        "making the GIF",
        stage,
    )
    return Path(output)


def render(
    scene,
    output: Path | str | None = None,
    *,
    session,
    gif: Path | str | None = None,
    gif_fps: int = GIF_FPS,
    gif_colors: int = GIF_COLORS,
    gif_scale: float = 1.0,
    gif_dither: str = "none",
    keep_clips: bool = True,
) -> Path:
    """Play a scene on `session` and write the video.

    `session` is required, e.g. from `art98.attach()`; render never boots or
    closes it. `output` ends in .mp4 or .gif. `gif` asks for a GIF as well. A
    GIF is made from a lossless recording, because 4:2:0 video smears 1-pixel
    coloured text.

    When the scene calls `cut()`, the clips are joined into the output and
    kept beside it as H.264 MP4, `<stem>-01.mp4` and on, even when the output
    is only a GIF. keep_clips=False drops them after the join.

    Every file, clips and output alike, is written as a `.part` file beside its
    final name, and all are renamed (the output last) only once the whole render
    has worked. A render that fails deletes its own part files and touches
    nothing an earlier render left, the output and its clips included.
    Numbered clips left beside the output by an earlier render with more cuts
    are never deleted: a warning on stderr names them.
    """
    require_ffmpeg()
    scene = scene() if isinstance(scene, type) else scene
    output = Path(output or f"{type(scene).__name__}.mp4")
    if output.suffix == ".gif":
        gif, output = output, None
    temp = Path(tempfile.mkdtemp(prefix="art98-render-")) if gif else None
    master = temp / "master.mkv" if temp else output
    named = output or Path(gif)
    video = None
    staged, kept, results = (
        [],
        [],
        [],
    )  # part files made; (part, final) clips; (part, final) outputs
    try:
        video = session.record(master, fps=scene.fps, lossless=bool(gif))
        video.defer = True  # the parts stay parts until the render is done
        with video:
            scene.run(session, video)
        staged += video.parts
        if not video.clips:
            raise RecorderError(f"{type(scene).__name__} recorded no frames")
        cut = video.clips != [master]
        source = video.parts[0]
        if cut:
            source = video.join(master, stage=True)
            staged.append(source)
        if gif:
            write_gif(
                source, gif, gif_fps, gif_colors, gif_scale, gif_dither, stage=True
            )
            staged.append(part_path(gif))
            results.append((part_path(gif), gif))
            if output:
                _encode(source, output)
                staged.append(part_path(output))
                results.append((part_path(output), output))
        else:
            results.append((source, output))
        for clip, part in zip(video.clips, video.parts) if cut and keep_clips else []:
            if (
                clip.suffix == LOSSLESS_SUFFIX
            ):  # kept as H.264, the lossless one dropped
                clip = (
                    named.with_name(f"{named.stem}-{clip.stem.rsplit('-', 1)[-1]}.mp4")
                    if clip.parent == temp
                    else clip.with_suffix(".mp4")
                )
                _encode(part, clip)
                part = part_path(clip)
                staged.append(part)
            kept.append((part, clip))
        for part, final in [*kept, *results]:  # the output last
            os.replace(part, final)
    finally:
        # parts that were not renamed: all of them after a failure, the dropped ones after a success
        for part in [*staged, *(video.parts if video else [])]:
            part.unlink(missing_ok=True)
        if temp:
            shutil.rmtree(temp, ignore_errors=True)
    written = {final for _, final in kept}
    stale = stale_clips(named, written)
    if stale:
        print(
            f"art98: warning: {', '.join(p.name for p in stale)} left from an earlier render; "
            f"this render wrote {len(written)} clips.",
            file=sys.stderr,
        )
    return named


def stale_clips(named_after: Path, written: set[Path]) -> list[Path]:
    """Numbered clips beside the output that this render did not write."""
    pattern = re.compile(rf"^{re.escape(named_after.stem)}-\d{{2}}\.mp4$")
    keep = {w.resolve() for w in written}
    return sorted(
        p
        for p in named_after.parent.glob(f"{named_after.stem}-*.mp4")
        if pattern.match(p.name) and p.resolve() not in keep
    )


def _encode(source: Path, output: Path) -> None:
    """Encode to H.264, left as `part_path(output)` for render to rename."""
    run_ffmpeg(
        ["-i", str(source), *H264], output, f"encoding {output.name}", stage=True
    )
