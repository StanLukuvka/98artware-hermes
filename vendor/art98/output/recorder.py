"""The recorder: frames to video clips and cuts between them."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
import warnings
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self

from ..base.units import frames
from ..errors import Art98Error

if TYPE_CHECKING:
    from ..session import Session


# The libx264 settings for every MP4 art98 writes. No reason for crf 12 or
# veryfast is recorded in the history or the docs. What is recorded is the
# cost: about 10 to 14 KB a second at crf 12, against 1.5 to 2.2 MB for FFV1.
H264 = ("-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-crf", "12")
FFV1 = ("-c:v", "ffv1", "-pix_fmt", "bgr0")

NO_FFMPEG = "ffmpeg isn't on PATH: install it (`art98 doctor` lists the packages)"

# The container a lossless clip is written in, whatever suffix it was given:
# FFV1 doesn't go in MP4.
LOSSLESS_SUFFIX = ".mkv"


class RecorderError(Art98Error, RuntimeError):
    """ffmpeg failed, or the frames can't go in one clip."""


@dataclass(frozen=True)
class Clip:
    """One finished clip: its final name, the file it is until committed, its size."""

    final: Path
    part: Path
    size: tuple[int, int]


def part_path(path: Path) -> Path:
    """Where a file is written until it is finished: out.part.mp4 beside out.mp4."""
    return path.with_name(f"{path.stem}.part{path.suffix}")


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RecorderError(NO_FFMPEG)


def run_ffmpeg(
    args: Sequence[str], output: Path, what: str, stage: bool = False
) -> Path:
    """Run ffmpeg into a part file and rename it once ffmpeg exited 0.

    With stage, the part file is left in place and returned: the caller
    renames it to `output` when everything else has worked, and deletes it
    otherwise. Raises RecorderError carrying ffmpeg's stderr; a failed run
    leaves no file at `output` or at its part.
    """
    output = Path(output)
    part = part_path(output)
    try:
        result = subprocess.run(  # noqa: PLW1510
            ["ffmpeg", "-y", "-loglevel", "error", *args, str(part)],
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise RecorderError(NO_FFMPEG) from None
    if result.returncode != 0:
        part.unlink(missing_ok=True)
        raise RecorderError(
            f"{what} failed (ffmpeg exit {result.returncode}): {result.stderr.strip()}"
        )
    if stage:
        return part
    os.replace(part, output)
    return output


def ppm_size(data: bytes) -> tuple[int, int]:
    """Width and height from a binary PPM's header."""
    fields = data[:64].split(maxsplit=3)
    if len(fields) < 3 or fields[0] != b"P6":
        raise RecorderError("a captured frame isn't a binary PPM image")
    return int(fields[1]), int(fields[2])


class Pacer:
    """Paces frames by the host clock: frame `index` is due at `index / output_fps`
    after the pacer was made, so frame 0 is due at once.

    A stretch that runs late stretches what the guest times for itself, so
    caret blinks and screensavers look slower in the video than they were.
    """

    def __init__(self, output_fps: float):
        self._output_fps = output_fps
        self._started = time.monotonic()

    def wait(self, index: int) -> None:
        """Sleep until frame `index` is due."""
        due = self._started + index / self._output_fps
        time.sleep(max(0.0, due - time.monotonic()))

    def done(self, count: int, stacklevel: int = 1) -> None:
        """Warn once if `count` frames ended more than a frame later than planned.

        `stacklevel` counts from the caller of this method (1 is that caller), so
        the warning can point at the scene author's line.
        """
        planned = count / self._output_fps
        took = time.monotonic() - self._started
        if took - planned > 1 / self._output_fps:
            warnings.warn(
                f"a live stretch meant to take {planned:.2f} s took {took:.2f} s: "
                "capturing is slower than the frame rate, so guest-timed motion in it "
                "will look slower in the video",
                stacklevel=stacklevel + 1,
            )


class Recorder:
    """Encodes captured frames to video clips, one explicit frame at a time.

    Frames are taken when the script says so, not on a wall clock, so a slow
    host produces the same video as a fast one.

    A run writes one file at `path` unless the scene calls `cut()`. The first
    cut names that clip `<stem>-01<suffix>`, and later clips are numbered on
    from there. `clips` lists the finished ones' final names and `join()` puts
    them back together. Each clip's ffmpeg starts at its first frame, so a cut
    with nothing recorded since the last one makes no empty file.

    Nothing is written under a final name while the run is going. Every clip
    lives as a part, a `<name>.part<suffix>` file beside its final name, and
    `close()` (or leaving the `with` block well) renames them all to `clips`.
    A run that fails drops its parts and touches no earlier file. With
    `defer`, `close()` leaves the parts in place for the caller to rename or delete,
    which is how `render` keeps a whole render all-or-nothing.
    """

    def __init__(
        self,
        session: Session,
        path: Path | str,
        output_fps: int,
        lossless: bool = False,
        defer: bool = False,
    ):
        self._session = session
        self._output_fps = output_fps
        self._lossless = lossless
        self._dir = Path(tempfile.mkdtemp(prefix="art98-frames-"))
        self._frame = self._dir / "frame.ppm"
        self._base = self._clip_path(path)
        self._ffmpeg: subprocess.Popen | None = None
        self._log = None
        self._size: tuple[int, int] | None = None
        self._clips: list[Clip] = []
        self._committed = False
        self._defer = defer
        self._closed = False
        self.path = self._base
        self.count = 0
        self.total = 0

    @property
    def clips(self) -> list[Path]:
        """The finished clips' final names."""
        return [clip.final for clip in self._clips]

    @property
    def parts(self) -> list[Path]:
        """The finished clips' files now: parts until `commit()`, then finals."""
        return [clip.part for clip in self._clips]

    @property
    def finished(self) -> list[Clip]:
        """The finished clips, each with its final name, current file and size."""
        return list(self._clips)

    # ---- frames ----

    def frame(self, hold: int = 1) -> None:
        """Wait for the guest to settle, capture, and write hold copies."""
        self._session.bridge.sync()
        self._capture(hold)

    def live(
        self,
        seconds: float,
        stop_condition: Callable[[], bool] | None = None,
        stacklevel: int = 1,
    ) -> None:
        """Record something that moves by itself, such as a screensaver.

        Frames follow the host's clock and skip the sync, so this stretch of
        video does depend on how fast the host runs. stop_condition, if given,
        is checked before each frame and ends the stretch early. A late-capture
        warning points `stacklevel` frames up from here (1 is the caller).
        """
        pacer = Pacer(self._output_fps)
        taken = 0
        for index in range(frames(seconds, self._output_fps)):
            if stop_condition is not None and stop_condition():
                break
            pacer.wait(index)
            self._capture(1)
            taken += 1
        pacer.done(taken, stacklevel + 1)

    def _capture(self, hold: int) -> None:
        self._check_open()
        self._session.vm.screenshot(self._frame, format="ppm")
        data = self._frame.read_bytes()
        self._write(data, hold)

    def _write(self, data: bytes, hold: int) -> None:
        size = ppm_size(data)
        if self._size is None:
            self._size = size
        elif size != self._size:
            raise RecorderError(
                f"the screen changed from {_size(self._size)} to {_size(size)} inside the clip "
                f"{self.path.name}, at frame {self.count + 1}: call cut() before the set_state "
                f"or revert that changes the screen size"
            )
        if self._ffmpeg is None:
            self._start()
        try:
            for _ in range(hold):
                self._ffmpeg.stdin.write(data)
        except BrokenPipeError:
            self._finish()  # raises with ffmpeg's own message
            raise RecorderError(
                f"ffmpeg stopped reading frames for {self.path}"
            ) from None
        self.count += hold
        self.total += hold

    # ---- clips ----

    def cut(self, path: Path | str | None = None) -> Path | None:
        """Close the current clip and start the next one, between two frames.

        Returns the closed clip's final name (its file is a part until the run
        ends), or None when nothing was recorded
        since the last cut, in which case there is no clip to close and `path`
        only renames the clip to come. Raises RecorderError if the closed
        clip's ffmpeg failed. Without `path`, clips are numbered
        `<stem>-02<suffix>` and so on, after the output's name.
        """
        self._check_open()
        if self.count == 0:
            if path is not None:
                self.path = self._clip_path(path)
            return None
        closed = self._finish(first_is_numbered=True)
        self.count = 0
        index = len(self._clips) + 1
        self.path = self._clip_path(path) if path is not None else self._numbered(index)
        return closed

    def close(self) -> None:
        """Close the last clip and rename every part to its final name.

        Raises RecorderError if its ffmpeg failed, after dropping the parts.
        With `defer` the parts stay where they are, for `commit()` or the
        caller to rename or delete.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._finish()
            if not self._defer:
                self.commit()
        except BaseException:
            self._discard()
            raise
        finally:
            shutil.rmtree(self._dir, ignore_errors=True)

    def commit(self) -> None:
        """Rename every part to its final name, replacing what is there."""
        for clip in self._clips:
            os.replace(clip.part, clip.final)
        self._clips = [Clip(c.final, c.final, c.size) for c in self._clips]
        self._committed = True

    def join(self, output: Path | str, stage: bool = False) -> Path:
        """Join the finished clips into one file, by stream copy.

        Every clip has the same codec settings, so nothing is re-encoded.
        Clips of different sizes can't be joined, and raise RecorderError.
        With stage, the joined file is left as a part and the part's path is
        returned, as for `run_ffmpeg`.
        """
        output = Path(output)
        if not self._clips:
            raise RecorderError("no clips to join: nothing was recorded")
        if output.resolve() in [clip.resolve() for clip in self.clips]:
            raise RecorderError(f"{output} is one of the clips being joined")
        if len({clip.size for clip in self._clips}) > 1:
            sizes = ", ".join(
                f"{clip.final.name} {_size(clip.size)}" for clip in self._clips
            )
            raise RecorderError(f"clips of different sizes can't be joined: {sizes}")
        # join usually runs after close(), so the listing gets its own folder
        with tempfile.TemporaryDirectory(prefix="art98-join-") as folder:
            listing = Path(folder) / "clips.txt"
            listing.write_text(
                "".join(
                    "file '{}'\n".format(str(part.resolve()).replace("'", "'\\''"))
                    for part in self.parts
                )
            )
            return run_ffmpeg(
                ["-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy"],
                output,
                f"joining {len(self.clips)} clips into {output}",
                stage,
            )

    # ---- inside ----

    def _clip_path(self, path: Path | str) -> Path:
        path = Path(path)
        return path.with_suffix(LOSSLESS_SUFFIX) if self._lossless else path

    def _numbered(self, index: int) -> Path:
        return self._base.with_name(f"{self._base.stem}-{index:02d}{self._base.suffix}")

    def _start(self) -> None:
        codec = FFV1 if self._lossless else H264
        self._log = (self._dir / f"ffmpeg-{len(self._clips) + 1:02d}.log").open("w+b")
        try:
            self._ffmpeg = subprocess.Popen(
                [
                    "ffmpeg",
                    "-y",
                    "-loglevel",
                    "error",
                    "-f",
                    "image2pipe",
                    "-c:v",
                    "ppm",
                    "-framerate",
                    str(self._output_fps),
                    "-i",
                    "-",
                    *codec,
                    str(part_path(self.path)),
                ],
                stdin=subprocess.PIPE,
                stderr=self._log,
            )
        except FileNotFoundError:
            self._log.close()
            self._log = None
            raise RecorderError(NO_FFMPEG) from None

    def _finish(self, first_is_numbered: bool = False) -> Path | None:
        """End the current clip's ffmpeg and file its part under clips.

        A failed ffmpeg drops every part and raises RecorderError.
        first_is_numbered gives a first clip still at the output's name the
        final name `<stem>-01`: a cut does that, while close() leaves a run's
        only clip where it is.
        """
        ffmpeg, log, path = self._ffmpeg, self._log, self.path
        self._ffmpeg = self._log = None
        size, self._size = self._size, None
        if ffmpeg is None:
            return None
        try:
            ffmpeg.stdin.close()
        except BrokenPipeError:
            pass
        code = ffmpeg.wait()
        log.seek(0)
        message = log.read().decode(errors="replace").strip()
        log.close()
        if code != 0:
            part_path(path).unlink(missing_ok=True)
            self._discard()
            raise RecorderError(
                f"ffmpeg failed writing {path} (exit {code})"
                + (f": {message}" if message else "")
            )
        final = (
            self._numbered(1)
            if first_is_numbered and path == self._base and not self._clips
            else path
        )
        if final != path:  # keep the join's part name, `<stem>.part`, free
            os.replace(part_path(path), part_path(final))
        self._clips.append(Clip(final, part_path(final), size))
        return final

    def _discard(self) -> None:
        """Delete the parts of the finished clips; this recorder's files only."""
        if not self._committed:  # a committed clip's part is its final file
            for clip in self._clips:
                clip.part.unlink(missing_ok=True)
        self._clips = []

    def abort(self) -> None:
        """Drop the clip being written and every finished part: no file is left behind.

        `clips` is emptied. A recorder that is already closed is left alone.
        """
        if self._closed:
            return
        self._closed = True
        ffmpeg, log, path = self._ffmpeg, self._log, self.path
        self._ffmpeg = self._log = None
        if ffmpeg is not None:
            try:
                ffmpeg.stdin.close()
            except OSError:
                pass
            ffmpeg.terminate()
            ffmpeg.wait()
            part_path(path).unlink(missing_ok=True)
        if log is not None:
            log.close()
        self._discard()
        shutil.rmtree(self._dir, ignore_errors=True)

    def _check_open(self) -> None:
        if self._closed:
            raise RecorderError("the recorder is closed")

    def __enter__(self) -> Self:
        return self

    def __exit__(self, kind, error, traceback) -> None:
        # When the scene raised, its error is the one to see: drop the half
        # clip and raise nothing over it.
        if error is not None:
            self.abort()
            return
        self.close()


def _size(size: tuple[int, int]) -> str:
    return f"{size[0]}x{size[1]}"
