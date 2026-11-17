"""Scene: a script for one recording of the desktop."""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import shutil
import warnings
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Self

from ..actions.pointer import Pointer
from ..actions.window.commands import Close
from ..actions.window.win import Win
from ..base.animation import Animation
from ..base.props import Box, Prop
from ..base.space import STOCK, DesktopSpace
from ..base.units import frames
from ..desktop import blank
from ..errors import Art98Error, GuestError, MissingFile, NotFound, UsageError
from ..guest import win32
from ..guest.bridge import wait_for
from ..legacy import scene_fps
from ..output.recorder import Pacer
from ..state import VERSION, FileRef, State, dump_state, id_problem, load_state
from . import setstate
from .renderer import render

DESKTOPS = ("blank", "stock")

# Only Scene class attributes a scene author sets, whose rename or removal would
# otherwise be silently ignored, go here. It is emptied at each release.
RETIRED: dict[str, str] = {}


def pin_legacy(cls) -> None:
    """Last resort: refuse a retired setting a scene still defines, rather than
    let it be ignored silently. Never maps an old name onto a new one."""
    for name, why in RETIRED.items():
        if name in vars(cls):
            raise UsageError(f"{cls.__name__}.{name}: {why}")


def check_desktop(scene) -> None:
    """Refuse a `desktop` that isn't one of DESKTOPS (a class or an instance)."""
    if scene.desktop not in DESKTOPS:
        raise UsageError(
            f"{getattr(scene, '__name__', type(scene).__name__)}.desktop "
            f"{scene.desktop!r}: use one of {', '.join(DESKTOPS)}"
        )


class UntrackedWindow(GuestError):
    """A visible top-level window that is neither the desktop's nor a prop."""


class ImplicitClose(GuestError):
    """A prop's window went, and no Close asked it to."""


class SectionNotFound(UsageError):
    """`--section` names a section the scene doesn't have."""


class _SectionDone(Art98Error):
    """Not an error: a solo run has played its section. It stops `construct`, and
    `run` goes on to `tear_down`. (An Art98Error because test_errors wants every
    exception class to be one.)"""


def _source_hash(what, label: str) -> str:
    """sha256 of a function's source, for the staleness check on a capture."""
    try:
        source = inspect.getsource(what)
    except (OSError, TypeError):
        raise UsageError(
            f"{label}: its source isn't available, so a capture can't be checked "
            "against it. Sections need methods defined in a file"
        ) from None
    return hashlib.sha256(source.encode()).hexdigest()


@dataclass(eq=False)
class Claim:
    """A program or action that is handling windows the scene has no prop for.

    `matches` takes a `windows()` row. A claim made with `Scene.claim` lasts until
    `release()`, which a `with` block calls at its end. One made with
    `Scene.claim_until_gone` waits for a window to match, then ends at the first
    check that finds none. If nothing ever matches, it ends `win32.CLOSE_TIMEOUT`
    seconds of host time after `made`. `hint` is what the error at the end of a run
    says when the claim is still up.
    """

    owner: str
    matches: Callable
    until_gone: bool = False
    seen: bool = False
    made: float = 0.0
    hint: str = ""
    scene: Scene | None = None
    active: bool = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if exc_type is None:
            self.release()
        elif self.scene is not None:
            self.scene._drop(self)

    def release(self) -> None:
        """End the claim. Its windows must be gone, or UntrackedWindow names the owner."""
        if self.active and self.scene is not None:
            self.scene._release(self)


def describe(window) -> str:
    return f"{window.title!r} ({window.class_name}, {window.hwnd:#x})"


class Scene:
    """Subclass this and write `construct`.

    `self.guest` is the raw Bridge, so any Win32 call can sit between
    animations, `self.vm` is the VM, and `self.pointer` is the mouse pointer.
    `self.time` is video time, frames written over output_fps, and it never reads a
    clock. `self.recorder` writes the frames, and
    `cut()` starts a new clip, and `section(name, method)` starts a named one that
    `art98 render --section` can render alone. The guest runs
    all the time: what the script drives lands on exact frames, while anything
    the guest times for itself follows the host clock.

    `desktop` is what the scene starts on. "blank", the default, is a black
    screen with no icons or taskbar (see art98.desktop), and no pointer
    unless `pointer_visible` is True. "stock" is the Windows 98 desktop as
    the `ready` snapshot has it, pointer included. Either way it is set again
    after every revert, so `set_state` starts from it too.

    `state`, a path to a state file, sets the desktop before `setup` runs.
    `set_state` jumps to another state at any point, and `prop(id)` is the
    prop for a window the state opened.

    `output_fps` is the finished video's frame rate: set it to match the edit
    timeline (24, 30, 60). It is not a capture or run rate.
    """

    output_fps = 30
    desktop = "blank"
    pointer_visible = False
    state: str | Path | None = None
    # True: a clamped size or a lost window raises PlacementError
    strict_placement = False
    # Set by `render`: the one section to play (None: all of them), and the folder the
    # captures of a full run go to (None: the scene's file can't be found).
    only_section: str | None = None
    captures: Path | None = None
    # A solo run of a later section starts at the time its capture was made.
    _time_offset: float = 0.0

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        scene_fps(cls)
        pin_legacy(cls)
        check_desktop(cls)

    def __init__(self):
        self.session = None
        self.recorder = None
        self.guest = None
        self.vm = None
        self._pointer: Pointer | None = None
        self.props: list[Prop] = []
        self.space: DesktopSpace = STOCK
        self._baseline: frozenset[int] = frozenset()
        self._claims: list[Claim] = []
        self.sections: list[tuple[str, float]] = []
        # The (name, method) of each section() call before the current one.
        self._earlier: list[tuple[str, Callable]] = []
        # (frames, props) when the last section ended: see `_check_between`.
        self._section_mark: tuple[int, int] = (0, 0)
        # The wallpaper the last Set applied, and its style: see `get_state`.
        self._wallpaper: tuple[FileRef, str] | None = None

    # ---- what a scene writes ----

    def setup(self) -> None:
        pass

    def construct(self) -> None:
        pass

    def tear_down(self) -> None:
        pass

    # ---- running ----

    @property
    def time(self) -> float:
        """Video time so far, across every clip."""
        if self.recorder is None:
            raise UsageError("the scene isn't running: time exists only inside run")
        return self._time_offset + self.recorder.total / self.output_fps

    def run(self, session, video) -> None:
        """Play this scene on a booted session, into an open recorder.

        With `only_section` set, only that section plays (see `section`).
        """
        check_desktop(self)
        self.session, self.recorder = session, video
        self.sections = []
        self._earlier = []
        self._time_offset = 0.0
        self.guest, self.vm = session.bridge, session.vm
        self.space = STOCK
        solo = self.only_section is not None
        if not solo:
            self._begin()
        self._section_mark = (video.total, len(self.props))
        try:
            self.construct()
        except _SectionDone:
            pass
        else:
            if solo:
                names = ", ".join(name for name, _ in self._earlier) or "none"
                raise SectionNotFound(
                    f"{type(self).__name__} has no section {self.only_section!r} "
                    f"(it has {names})"
                )
            if self._earlier:
                self._check_between()
        self.tear_down()
        self._finish()

    def _begin(self) -> None:
        """The start of a scene: its state or a fresh desktop, then `setup`."""
        if self.state is not None:
            self._set_state(self.state)
        else:
            self._reset()
        self.setup()

    def _finish(self) -> None:
        """The check at the end of a run: claims no longer cover anything."""
        claims = list(self._claims)
        for claim in claims:
            self._drop(claim)
        try:
            self.check_tracking()
        except UntrackedWindow as error:
            strays = self._strays(self.guest.windows())
            hints = [
                c.hint or f"{c.owner} is still up"
                for c in claims
                if any(c.matches(w) for w in strays)
            ]
            if not hints:
                raise
            raise UntrackedWindow(f"{error} {'; '.join(hints)}.") from None

    def render(self, output: Path | str | None = None, *, session, **options) -> Path:
        """Play the scene on `session` (see `art98.attach`) and write the video.

        `options` are those of `renderer.render`: gif, keep_clips and so on.
        """
        return render(self, output, session=session, **options)

    # ---- desktop state ----

    def set_state(self, path: Path | str) -> None:
        """Make the desktop look like a state file, all within one frame.

        This is a Set: revert the guest to the snapshot, apply the state, and
        check it took. Props from before are gone, as are their windows. A
        relative path is looked for next to the scene's file, then in the
        current folder. Nothing is recorded, so the next frame shows the state.
        """
        self._need_section()
        self._set_state(path)

    def _set_state(self, path: Path | str) -> None:
        state = load_state(self._find_state(Path(path)))
        self._reset()
        props = setstate.apply_state(self, state)
        setstate.check_state(self.guest, self.space, state, props)

    def get_state(self, path: Path | str) -> State:
        """Write the desktop as a state file, and return it as `load_state` reads it.

        Files the state needs (images, a Notepad's text, the wallpaper) are copied to
        `<path stem>.files/` beside it (windows under `windows/<id>/`, the wallpaper under
        `desktop/`). The capture is built aside and moved in only once it loads, so a
        failed capture leaves the earlier one alone. Every window prop
        needs an id (`self.add(prop, id=...)`).
        """
        self._need_section()
        self.check_tracking()
        own = {p.hwnd for p in self.props if isinstance(p, Win)}
        shown = [w for w in self.guest.windows() if w.visible and w.hwnd not in own]
        for claim in self._claims:
            if any(claim.matches(w) for w in shown):
                raise UsageError(
                    f"{claim.hint or claim.owner + ' is still up'}: a capture would "
                    f"drop its window ({claim.owner}). Finish or close it before the "
                    "section boundary"
                )
        self.refresh_props()
        self.pointer.refresh()
        path = Path(path)
        files = path.with_name(path.stem + ".files")
        # `windows` is written in creation order: the top-level Win props in
        # `self.props`, which follows add() and set_state (list order).
        creation = [
            p
            for p in self.props
            if isinstance(p, Win) and p._top_level and p.hwnd is not None
        ]
        wins = {p.hwnd: p for p in creation}
        # Back to front: `windows()` lists the front window first.
        stack = [wins[w.hwnd] for w in reversed(self.guest.windows()) if w.hwnd in wins]
        for prop in creation:
            if prop.state_id is None:
                raise UsageError(f"{prop._name()} needs an id: self.add(prop, id=...)")
            if problem := id_problem(prop.state_id):
                raise UsageError(f"{prop._name()} id: {problem}")
        colour = win32.rgb(
            self.guest.user32.GetSysColor(win32.COLOR_DESKTOP).value & 0xFFFFFF
        )
        desktop: dict = {"fill_colour": list(colour)}
        # The capture is built and checked in a sibling folder, then moved in, so a
        # failure leaves the earlier capture untouched.
        part = path.parent / f".{path.name}.part"
        shutil.rmtree(part, ignore_errors=True)  # left by a crash
        if part.is_file():
            part.unlink()
        part_files = part / files.name
        part_files.mkdir(parents=True)
        try:
            # lazy: a wallpaper set by a raw guest call is invisible here (it can't be read back, setstate.py wallpaper_problem); upgrade: track every set_wallpaper call
            if self._wallpaper is not None:
                ref, style = self._wallpaper
                target = part_files / "desktop" / ref.path.name
                target.parent.mkdir()
                target.write_bytes(ref.data)
                desktop["wallpaper"] = {
                    "file": target.relative_to(part).as_posix(),
                    "sha256": ref.sha256,
                }
                desktop["style"] = style
            windows = [{"id": p.state_id, **p.to_state(part_files)} for p in creation]
            dump_state(
                {
                    "version": VERSION,
                    "display": list(setstate.display_mode(self.guest)),
                    "desktop": desktop,
                    "windows": windows,
                    "stack": [p.state_id for p in stack],
                    "pointer": list(win32.cursor_pos(self.guest)),
                },
                part / path.name,
            )
            if files.is_dir() and not files.is_symlink():
                shutil.rmtree(files)
            elif files.exists() or files.is_symlink():
                files.unlink()
            os.replace(part_files, files)
            os.replace(part / path.name, path)
        finally:
            shutil.rmtree(part, ignore_errors=True)
        return load_state(path)

    def _reset(self) -> None:
        """Back to the baseline and a fresh desktop: what `run` and a Set both start with.

        Reverts unless nothing has touched the guest since the last revert, so a
        scene's first Set follows `run`'s start without a second revert. The
        session is marked touched before the desktop starts, so a half-done start
        counts. Props from before are forgotten.
        """
        if not self.session.fresh:
            self.session.revert()
        self.session.fresh = False
        self._wallpaper = None
        for prop in self.props:
            prop.forget()
        self.props.clear()
        for claim in list(self._claims):
            self._drop(claim)
        self._start_desktop()

    def prop(self, window_id: str) -> Prop:
        """The prop on stage with this id: one a state opened, or one added with `id=`."""
        known = {p.state_id: p for p in self.props if p.state_id is not None}
        try:
            return known[window_id]
        except KeyError:
            raise NotFound(
                f"no prop {window_id!r} on stage (it has {', '.join(known) or 'none'})"
            ) from None

    def _start_desktop(self) -> None:
        """After a boot or a revert: the desktop the scene asked for, and a new pointer."""
        self.prepare_desktop()
        self._pointer = Pointer()
        self._pointer.attach(self)
        self._baseline = frozenset(w.hwnd for w in self.guest.windows() if w.visible)

    def check_tracking(self) -> None:
        """Raise unless every visible top-level window is the desktop's or a prop's,
        and every window prop still has its window.

        One `windows()` call. `play`, `wait` and `wait_until` run it when they
        return, and `run` runs it at the end. A window gone without `Close`
        raises ImplicitClose, a window nothing added raises UntrackedWindow.
        """
        rows = self.guest.windows()
        by_hwnd = {w.hwnd: w for w in rows}
        wins = [p for p in self.props if isinstance(p, Win) and p.hwnd is not None]
        for prop in wins:
            if not prop.closing and prop.is_gone(by_hwnd):
                raise ImplicitClose(
                    f"{prop._name()}'s window closed, but nothing asked it to: "
                    "end it with Close(window), or Close(window, via=...) to show the click"
                )
        for claim in [c for c in self._claims if c.until_gone]:
            if any(w.visible and claim.matches(w) for w in rows):
                claim.seen = True
            elif claim.seen or monotonic() - claim.made > win32.CLOSE_TIMEOUT:
                self._claims.remove(claim)
        stray = self._strays(rows)
        if stray:
            owners = list(dict.fromkeys(c.owner for c in self._claims))
            held = f" Claimed now: {', '.join(owners)}." if owners else ""
            raise UntrackedWindow(
                "a window is on screen that the scene doesn't track: "
                + ", ".join(describe(w) for w in stray)
                + ". Add it as a prop (self.add(Win(hwnd=...))) or close it."
                + held
            )

    def claim(self, owner: str, matches: Callable, *, hint: str = "") -> Claim:
        """Let `owner` handle the windows `matches(row)` picks out, until `release()`.

        Use it as a `with` block, or keep the handle and call `release()` later.
        Tracking checks in between skip those windows. They must be gone at
        release, or UntrackedWindow names `owner`. A claim still open at the end
        of `run` is dropped, and its windows are then errors, with `hint` said.
        """
        claim = Claim(owner, matches, hint=hint, scene=self)
        self._claims.append(claim)
        return claim

    def _drop(self, claim: Claim) -> None:
        claim.active = False
        with suppress(ValueError):
            self._claims.remove(claim)

    def _release(self, claim: Claim) -> None:
        self._drop(claim)
        owner, matches = claim.owner, claim.matches
        left: list = []

        def gone():
            left[:] = [w for w in self._strays(self.guest.windows()) if matches(w)]
            return True if not left else None

        # The last step may not wait on the guest, so give closing windows time.
        if wait_for(gone, win32.CLOSE_TIMEOUT, win32.CLOSE_POLL) is None:
            raise UntrackedWindow(
                f"{owner} left a window up: " + ", ".join(describe(w) for w in left)
            )
        self.check_tracking()

    def claim_until_gone(self, owner: str, matches: Callable) -> None:
        """A claim for a window an action opens: kept until a check has seen a match and
        then finds none, or `win32.CLOSE_TIMEOUT` seconds pass with none seen."""
        self._claims.append(Claim(owner, matches, until_gone=True, made=monotonic()))

    def _strays(self, rows) -> list:
        known = self._baseline | {
            p.hwnd for p in self.props if isinstance(p, Win) and p.hwnd is not None
        }
        return [
            w
            for w in rows
            if w.visible
            and w.hwnd not in known
            and not any(c.matches(w) for c in self._claims)
        ]

    def stray_note(self) -> str:
        """For an error message: ' (untracked: ...)' naming what is on screen that no prop owns."""
        stray = self._strays(self.guest.windows())
        return (
            " (untracked: " + ", ".join(describe(w) for w in stray) + ")"
            if stray
            else ""
        )

    def prepare_desktop(self) -> None:
        """Put the desktop the scene asked for in place, then read the work area."""
        if self.desktop == "blank":
            blank(self.guest, pointer=self.pointer_visible)
        self.calibrate()

    def calibrate(self) -> None:
        """Read the desktop from the guest again: screen, work area and metrics.
        Call it after a script changes any of them with raw guest calls."""
        self.space = win32.calibrate(self.guest, self.strict_placement)

    @property
    def work_area(self) -> tuple[int, int, int, int]:
        """(left, top, right, bottom) above the taskbar, or the whole screen when blank."""
        return self.space.work_area

    def _find_state(self, path: Path) -> Path:
        if path.is_absolute():
            return path
        try:
            beside = Path(inspect.getfile(type(self))).resolve().parent / path
        except (TypeError, OSError):
            beside = None
        if beside is not None and beside.exists():
            return beside
        here = Path.cwd() / path
        if not here.exists():
            looked = [candidate for candidate in (beside, here) if candidate]
            raise MissingFile(
                f"state file {str(path)!r} not found; looked in "
                + " and ".join(str(candidate) for candidate in looked)
            )
        return here

    @property
    def screen(self) -> Box:
        """The work area, as a box to measure against."""
        left, top, right, bottom = self.work_area
        return Box(left, top, right - left, bottom - top)

    def add(self, *props: Prop, id: str | None = None) -> None:
        """Bring props on stage: start the program, find the window, place it.

        `id` names a single prop, for `prop(id)` and a captured state.
        """
        self._need_section()
        if id is not None and len(props) != 1:
            raise UsageError("add takes id= with exactly one prop")
        if id is not None and (problem := id_problem(id)):
            raise UsageError(f"add(id=): {problem}")
        for prop in props:
            if prop not in self.props:
                prop.attach(self)
                self.props.append(prop)
            if id is not None:
                prop.state_id = id

    def remove(self, *props: Prop) -> None:
        """Take props off stage. For a window that means closing it, as `Close` does."""
        for prop in props:
            close = Close(prop)
            close.begin(self)
            close.finish(self)

    def refresh_props(self) -> None:
        """Read positions back, after something other than the script moved them."""
        for prop in self.props:
            prop.refresh()

    # ---- time ----

    def play(
        self,
        *animations: Animation,
        run_time: float | None = None,
        rate_func=None,
        live: bool = False,
    ) -> None:
        """Run animations together, one captured frame at a time.

        run_time and rate_func override every animation given, for this call
        only: the animations keep their own afterwards. Shorter
        animations finish early and hold. With live=True, frames follow the
        host clock, for guest behaviour that needs real time to happen.
        """
        self._need_section()
        if not animations:
            raise UsageError("play needs at least one animation")
        self._check_claims(animations)
        running: list[Animation] = []  # begun and not finished
        try:
            with _overrides(animations, run_time, rate_func):
                for animation in animations:
                    running.append(animation)
                    animation.begin(self)
                total = max(animation.run_time for animation in animations)
                count = max(1, frames(total, self.output_fps))
                live = live or any(animation.live for animation in animations)
                pacer = Pacer(self.output_fps)
                for index in range(1, count + 1):
                    elapsed = index / count * total
                    for animation in list(running):
                        length = animation.run_time
                        progress = (
                            1.0
                            if length <= 0 or index == count
                            else min(1.0, elapsed / length)
                        )
                        animation.update(progress)
                        if progress >= 1:
                            animation.finish(self)
                            running.remove(animation)
                    self._apply()
                    if live:
                        pacer.wait(index - 1)
                    self.recorder.frame()
                if live:
                    pacer.done(count, stacklevel=2)
        except BaseException:
            for animation in running:
                with suppress(Exception):  # the play's own error is the one to show
                    animation.abort(self)
            raise
        self.check_tracking()

    def wait(self, duration: float = 1.0, stop_condition=None) -> None:
        """Hold for a while, or until stop_condition() is true.

        The guest keeps running, so a caret blinks and a clock ticks, and
        something that moves by itself, such as a screensaver, is recorded. These
        frames follow the host clock and skip the repaint wait, so the stretch
        depends on host speed. See also `pause`.
        """
        self._live(duration, stop_condition)
        self.check_tracking()

    def pause(self, duration: float = 1.0) -> None:
        """Hold one capture. Nothing the guest does in this time is seen."""
        self._apply()
        self.recorder.frame(hold=max(1, frames(duration, self.output_fps)))

    def _live(self, seconds: float, stop_condition) -> None:
        # Called by wait and wait_until, each called by the scene's own script:
        # three frames up from Recorder.live is that script's line.
        self._apply()
        self.recorder.live(seconds, stop_condition, stacklevel=3)

    def cut(self, path: Path | str | None = None) -> Path | None:
        """End the current clip here and start a new one, between two frames.

        `render` keeps the clips beside the output, as `<stem>-01.mp4` and
        on, and joins them into the output. It wraps `Recorder.cut`.
        """
        return self.recorder.cut(path)

    # ---- sections ----

    def section(self, name: str, method: Callable[[], None]) -> None:
        """Play `method` as the section `name`. Call it from `construct`, once per section.

        A full render cuts the clip before each section after the first and writes the
        desktop as a capture, so `art98 render --section NAME` can play that section
        alone: it sets the desktop to the capture and skips `setup` and every earlier
        section. A window a later section uses is found by id (`prop(id)`), not by a
        variable or `self.<attr>` an earlier section made. `name` is a label for
        `sections` and the capture, letters, digits, `_` and `-` only.
        """
        if problem := id_problem(name):
            raise UsageError(f"section name: {problem}")
        if not callable(method):
            raise UsageError(f"section {name!r} needs a method to call")
        if self.captures is None:
            raise UsageError(
                "a scene with sections needs to live in a file: art98 keeps the "
                "captures of its sections beside it"
            )
        if any(name == earlier for earlier, _ in self._earlier):
            raise UsageError(f"two sections are called {name!r}")
        number = len(self._earlier) + 1
        solo = self.only_section is not None
        if not solo:
            self._check_between()
            if number > 1:
                self._capture(number, name)
        elif name == self.only_section:
            if number > 1:
                self._start_from_capture(number, name)
            else:
                self._begin()
        else:  # a solo run skips it, and the capture holds what it did
            self._earlier.append((name, method))
            return
        self._earlier.append((name, method))
        self.sections.append((name, self.time))
        method()
        if solo:
            raise _SectionDone
        self._section_mark = (self.recorder.total, len(self.props))

    def _check_between(self) -> None:
        # lazy: only frames and props are noticed, so raw guest calls between sections go unseen; upgrade: compare a windows() listing as well
        if (self.recorder.total, len(self.props)) != self._section_mark:
            raise UsageError(
                "code between section() calls: it records frames or adds props outside "
                "any section, and a solo --section run would skip it. Move it into a "
                "section method"
            )

    @staticmethod
    def _capture_names(number: int, name: str) -> tuple[str, str]:
        stem = f"{number:02}-{name}"
        return f"{stem}.state.json", f"{stem}.meta.json"

    def _hashes(self) -> dict[str, str | None]:
        """What a capture depends on: `setup`, the scene's state file and every earlier section.

        lazy: the files a state file points at aren't hashed; upgrade: hash them too.
        """
        hashes: dict[str, str | None] = {
            "setup": _source_hash(type(self).setup, "setup()"),
            "state": None,
        }
        if self.state is not None:
            data = self._find_state(Path(self.state)).read_bytes()
            hashes["state"] = hashlib.sha256(data).hexdigest()
        for name, method in self._earlier:
            hashes[f"section {name}"] = _source_hash(method, f"section {name!r}")
        return hashes

    def _capture(self, number: int, name: str) -> None:
        """A full run, at the start of a later section: cut, then write the capture and its meta."""
        self.cut()
        state_name, meta_name = self._capture_names(number, name)
        part = self.captures.with_name(self.captures.name + ".part")
        part.mkdir(parents=True, exist_ok=True)
        self.get_state(part / state_name)
        meta = {"time": self.time, "hashes": self._hashes()}
        (part / meta_name).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")

    def _start_from_capture(self, number: int, name: str) -> None:
        """A solo run, at its section: check the capture is fresh, then Set to it."""
        state_name, meta_name = self._capture_names(number, name)
        state, meta_path = self.captures / state_name, self.captures / meta_name
        try:
            meta = json.loads(meta_path.read_text())
            start, recorded = float(meta["time"]), dict(meta["hashes"])
        except (OSError, ValueError, KeyError, TypeError):
            start = recorded = None
        if recorded is None or not state.is_file():
            raise MissingFile(
                f"no capture for section {name!r} in {self.captures}: "
                "render the whole scene once first"
            )
        now, methods = self._hashes(), dict(self._earlier)
        for key in dict.fromkeys([*now, *recorded]):
            if now.get(key) == recorded.get(key):
                continue
            if key == "setup":
                changed = "setup()"
            elif key == "state":
                changed = "the state file"
            else:
                earlier = key.removeprefix("section ")
                method = methods.get(earlier)
                changed = (
                    f"{getattr(method, '__name__', earlier)}()"
                    if method
                    else f"section {earlier!r}"
                )
            raise UsageError(
                f"section {name!r} starts from a capture made before {changed} "
                "changed: render the whole scene again"
            )
        self._set_state(state)
        self._time_offset = start

    def wait_until(self, stop_condition, max_time: float = 60.0) -> None:
        """Hold until stop_condition() is true, giving up after max_time."""
        self._live(max_time, stop_condition)
        if not stop_condition():
            warnings.warn(f"wait_until gave up after {max_time} s", stacklevel=2)
        self.check_tracking()

    @property
    def pointer(self) -> Pointer | None:
        """The mouse pointer. Before a solo run's section there is no desktop: an error."""
        self._need_section()
        return self._pointer

    @pointer.setter
    def pointer(self, value: Pointer | None) -> None:
        self._pointer = value

    def _need_section(self) -> None:
        """A solo run starts at its section: work before one has no desktop to run on."""
        if self._pointer is None and self.only_section is not None:
            raise SectionNotFound(
                f"section {self.only_section!r} was not reached: a scene run with "
                "--section must call self.section(...) before doing anything else"
            )

    def _apply(self) -> None:
        self._need_section()
        self.pointer.apply()
        for prop in self.props:
            prop.apply()

    @staticmethod
    def _check_claims(animations) -> None:
        taken: set[str] = set()
        for animation in animations:
            for claim in animation.claims:
                if claim in taken:
                    raise UsageError(
                        f"two animations in one play both drive the {claim}"
                    )
                taken.add(claim)


@contextmanager
def _overrides(animations, run_time, rate_func) -> Iterator[None]:
    """Give the animations a run_time and rate_func for one `play`, then put back
    what each had, whether it was its own or its class's."""
    saved = [(animation, dict(vars(animation))) for animation in animations]
    for animation in animations:
        if run_time is not None:
            animation.run_time = run_time
        if rate_func is not None:
            animation.rate_func = rate_func
    try:
        yield
    finally:
        for animation, before in saved:
            for name in ("run_time", "rate_func"):
                if name in before:
                    setattr(animation, name, before[name])
                else:
                    vars(animation).pop(name, None)
