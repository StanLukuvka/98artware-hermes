"""Rendering a scene: VM, recorder, output file."""

from __future__ import annotations

import inspect
import os
import re
import shutil
import tempfile
import warnings
from pathlib import Path

from ..errors import UsageError
from ..output.gif import GifOptions, write_gif
from ..output.recorder import (
    H264,
    LOSSLESS_SUFFIX,
    RecorderError,
    part_path,
    require_ffmpeg,
    run_ffmpeg,
)
from ..state import id_problem

OUTPUT_SUFFIXES = (".mp4", ".gif")


def render(
    scene,
    output: Path | str | None = None,
    *,
    session,
    gif: Path | str | None = None,
    gif_options: GifOptions | None = None,
    keep_clips: bool = True,
    section: str | None = None,
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
    are never deleted: a `warnings.warn` names them.

    A scene that calls `section()` has its captures in `<scene file stem>.<Scene>.sections/`
    beside its file. A full render writes them aside and swaps them in with the output, so a
    failed render leaves the earlier captures alone. `section="name"` plays only that
    section, from its capture, into one clip (`<Scene>-<name>.mp4` by default), and never
    writes captures.
    """
    scene = scene() if isinstance(scene, type) else scene
    name = type(scene).__name__
    output = Path(output or (f"{name}-{section}.mp4" if section else f"{name}.mp4"))
    if section is not None and (problem := id_problem(section)):
        raise UsageError(f"--section: {problem}")
    scene.only_section = section
    scene.captures = captures = captures_folder(scene)
    if section is not None and captures is None:
        raise UsageError("--section needs the scene to live in a file")
    if output.suffix not in OUTPUT_SUFFIXES:
        raise UsageError(f"output {output.name} must end in .mp4 or .gif")
    if gif is not None and Path(gif).suffix != ".gif":
        raise UsageError(f"gif {Path(gif).name} must end in .gif")
    require_ffmpeg()
    if output.suffix == ".gif":
        gif, output = output, None
    temp = Path(tempfile.mkdtemp(prefix="art98-render-")) if gif else None
    master = temp / "master.mkv" if temp else output
    named = output or Path(gif)
    video = None
    staged = []  # every part file made, for cleanup
    kept = []  # (part, final) clips
    results = []  # (part, final) outputs
    aside = old = None
    if captures is not None and section is None:
        aside, old = (captures.with_name(captures.name + s) for s in (".part", ".old"))
        # left by a crashed render
        shutil.rmtree(old, ignore_errors=True)
        shutil.rmtree(aside, ignore_errors=True)
    try:
        # defer: the parts stay parts until the render is done
        video = session.record(
            master, output_fps=scene.output_fps, lossless=bool(gif), defer=True
        )
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
            write_gif(source, gif, gif_options or GifOptions(), stage=True)
            staged.append(part_path(gif))
            results.append((part_path(gif), gif))
            if output:
                _encode(source, output)
                staged.append(part_path(output))
                results.append((part_path(output), output))
        else:
            results.append((source, output))
        clips = video.finished if cut and keep_clips else []
        for number, record in enumerate(clips, 1):  # the recorder numbers the same way
            clip, part = record.final, record.part
            if (
                clip.suffix == LOSSLESS_SUFFIX
            ):  # kept as H.264, the lossless one dropped
                clip = (
                    named.with_name(f"{named.stem}-{number:02d}.mp4")
                    if clip.parent == temp
                    else clip.with_suffix(".mp4")
                )
                _encode(part, clip)
                part = part_path(clip)
                staged.append(part)
            kept.append((part, clip))
        for part, final in kept:
            os.replace(part, final)
        if aside is not None and aside.is_dir():  # a scene with sections
            swap_captures(captures)
        for part, final in results:  # the output last
            os.replace(part, final)
    finally:
        if aside is not None:
            shutil.rmtree(aside, ignore_errors=True)
        # parts that were not renamed: all of them after a failure, the dropped ones after a success
        for part in [*staged, *(video.parts if video else [])]:
            part.unlink(missing_ok=True)
        if temp:
            shutil.rmtree(temp, ignore_errors=True)
    written = {final for _, final in kept}
    stale = stale_clips(named, written)
    if stale:
        warnings.warn(
            f"{', '.join(p.name for p in stale)} left from an earlier render; "
            f"this render wrote {len(written)} clips.",
            stacklevel=2,
        )
    return named


def captures_folder(scene) -> Path | None:
    """Where a scene's section captures live, or None when its file can't be found."""
    try:
        file = Path(inspect.getfile(type(scene))).resolve()
    except (TypeError, OSError):
        return None
    return file.with_name(f"{file.stem}.{type(scene).__name__}.sections")


def swap_captures(final: Path) -> None:
    """Move `<final>.part` in as `final`. `os.replace` onto a folder with files in it
    fails, so the old one goes aside first and is deleted last."""
    aside, old = (final.with_name(final.name + s) for s in (".part", ".old"))
    if final.exists():
        final.rename(old)
    try:
        aside.rename(final)
    except OSError:
        if old.exists():
            old.rename(final)
        raise
    shutil.rmtree(old, ignore_errors=True)


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
