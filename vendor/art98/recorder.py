"""The recorder: frames to video clips, cuts between them, and the GIF defaults."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from .session import Session


# The libx264 settings for every MP4 art98 writes.
H264 = ["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-crf", "12"]
FFV1 = ["-c:v", "ffv1", "-pix_fmt", "bgr0"]

# The GIF defaults, here so the command line can read them without loading the
# animation package.
GIF_FPS = 15
GIF_COLORS = 128

# The container a lossless clip is written in, whatever suffix it was given:
# FFV1 doesn't go in MP4.
LOSSLESS_SUFFIX = ".mkv"


class RecorderError(RuntimeError):
    """ffmpeg failed, or the frames can't go in one clip."""


def part_path(path: Path) -> Path:
    """Where a file is written until it is finished: out.part.mp4 beside out.mp4."""
    return path.with_name(f"{path.stem}.part{path.suffix}")


def require_ffmpeg() -> None:
    if shutil.which("ffmpeg") is None:
        raise RecorderError(
            "ffmpeg isn't on PATH: install it (`art98 doctor` lists the packages)"
        )


def run_ffmpeg(args: list[str], output: Path, what: str, stage: bool = False) -> Path:
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
        raise RecorderError(
            "ffmpeg isn't on PATH: install it (`art98 doctor`)"
        ) from None
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
    lives in `parts`, under a `<name>.part<suffix>` file beside its final name,
    and `close()` (or leaving the `with` block well) renames them all to `clips`. A run that
    fails drops its parts and touches no earlier file. With `defer` set, the
    `close()` leaves the parts in place for the caller to rename or delete,
    which is how `render` keeps a whole render all-or-nothing.
    """

    def __init__(
        self, session: Session, path: Path | str, fps: int = 30, lossless: bool = False
    ):
        self._session = session
        self._fps = fps
        self._lossless = lossless
        self._dir = Path(tempfile.mkdtemp(prefix="art98-frames-"))
        self._frame = self._dir / "frame.ppm"
        self._base = self._clip_path(path)
        self._ffmpeg: subprocess.Popen | None = None
        self._log = None
        self._size: tuple[int, int] | None = None
        self._sizes: list[tuple[int, int]] = []
        self._closed = False
        self.path = self._base
        self.defer = False
        self.clips: list[Path] = []
        self.parts: list[Path] = []
        self.count = 0
        self.total = 0

    # ---- frames ----

    def frame(self, hold: int = 1) -> None:
        """Wait for the guest to settle, capture, and write hold copies."""
        self._session.bridge.sync()
        self._capture(hold)

    def live(
        self, seconds: float, stop_condition: Callable[[], bool] | None = None
    ) -> None:
        """Record something that moves by itself, such as a screensaver.

        Frames follow the host's clock and skip the sync, so this stretch of
        video does depend on how fast the host runs. stop_condition, if given,
        is checked before each frame and ends the stretch early.
        """
        start = time.monotonic()
        for index in range(round(seconds * self._fps)):
            if stop_condition is not None and stop_condition():
                return
            time.sleep(max(0.0, start + index / self._fps - time.monotonic()))
            self._capture(1)

    def _capture(self, hold: int) -> None:
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
        index = len(self.clips) + 1
        self.path = self._clip_path(path) if path is not None else self._numbered(index)
        return closed

    def close(self) -> None:
        """Close the last clip and rename every part to its final name.

        Raises RecorderError if its ffmpeg failed, after dropping the parts.
        With `defer` set the parts stay where they are, for `commit()` or the
        caller to rename or delete.
        """
        if self._closed:
            return
        self._closed = True
        try:
            self._finish()
            if not self.defer:
                self.commit()
        except BaseException:
            self._discard()
            raise
        finally:
            shutil.rmtree(self._dir, ignore_errors=True)

    def commit(self) -> None:
        """Rename every part to its final name, replacing what is there."""
        for part, final in zip(self.parts, self.clips):
            os.replace(part, final)
        self.parts = list(self.clips)

    def join(self, output: Path | str, stage: bool = False) -> Path:
        """Join the finished clips into one file, by stream copy.

        Every clip has the same codec settings, so nothing is re-encoded.
        Clips of different sizes can't be joined, and raise RecorderError.
        With stage, the joined file is left as a part and the part's path is
        returned, as for `run_ffmpeg`.
        """
        output = Path(output)
        if not self.parts:
            raise RecorderError("no clips to join: nothing was recorded")
        if output.resolve() in [clip.resolve() for clip in self.clips]:
            raise ValueError(f"{output} is one of the clips being joined")
        if len(set(self._sizes)) > 1:
            sizes = ", ".join(
                f"{clip.name} {_size(size)}"
                for clip, size in zip(self.clips, self._sizes)
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
        self._log = (self._dir / f"ffmpeg-{len(self.clips) + 1:02d}.log").open("w+b")
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
                    str(self._fps),
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
            raise RecorderError(
                "ffmpeg isn't on PATH: install it (`art98 doctor`)"
            ) from None

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
            if first_is_numbered and path == self._base and not self.clips
            else path
        )
        if final != path:  # keep the join's part name, `<stem>.part`, free
            os.replace(part_path(path), part_path(final))
        self.clips.append(final)
        self.parts.append(part_path(final))
        self._sizes.append(size)
        return final

    def _discard(self) -> None:
        """Delete the parts of the finished clips; this recorder's files only."""
        for part in self.parts:
            part.unlink(missing_ok=True)
        self.clips, self.parts, self._sizes = [], [], []

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
            except (BrokenPipeError, OSError):
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
