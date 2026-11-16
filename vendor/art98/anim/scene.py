"""Scene: a script for one recording of the desktop."""

from __future__ import annotations

import inspect
import warnings
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..actions.pointer import Pointer
from ..base.animation import Animation
from ..base.props import Box, Prop
from ..base.space import STOCK, DesktopSpace
from ..base.units import frames
from ..desktop import blank
from ..errors import MissingFile, NotFound, UsageError
from ..guest import win32
from ..legacy import scene_fps
from ..output.recorder import Pacer
from ..state import load_state
from . import setstate
from .renderer import render

DESKTOPS = ("blank", "stock")

# Only Scene class attributes a scene author sets, whose rename or removal would
# otherwise be silently ignored, go here. It is emptied at each release.
RETIRED = {
    "clock": "removed: the guest always runs on its own clock",
}


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


class Scene:
    """Subclass this and write `construct`.

    `self.guest` is the raw Bridge, so any Win32 call can sit between
    animations, `self.vm` is the VM, and `self.pointer` is the mouse pointer.
    `self.time` is video time, frames written over output_fps, and it never reads a
    clock. `self.recorder` writes the frames, and
    `cut()` starts a new clip, and `next_section(name)` does the same with a label. The guest runs
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
        self.pointer: Pointer | None = None
        self.props: list[Prop] = []
        self.space: DesktopSpace = STOCK
        self._state_props: dict[str, Prop] = {}
        self.sections: list[tuple[str, float]] = []

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
        return self.recorder.total / self.output_fps

    def run(self, session, video) -> None:
        """Play this scene on a booted session, into an open recorder."""
        check_desktop(self)
        self.session, self.recorder = session, video
        self.sections = []
        self.guest, self.vm = session.bridge, session.vm
        self.space = STOCK
        if self.state is not None:
            self.set_state(self.state)
        else:
            self._reset()
        self.setup()
        self.construct()
        self.tear_down()

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
        state = load_state(self._find_state(Path(path)))
        self._reset()
        props = setstate.apply_state(self, state)
        setstate.check_state(self.guest, self.space, state, props)
        self._state_props = props

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
        for prop in self.props:
            prop.forget()
        self.props.clear()
        self._state_props = {}
        self._start_desktop()

    def prop(self, window_id: str) -> Prop:
        """The prop for a window the last state opened, by its id."""
        try:
            return self._state_props[window_id]
        except KeyError:
            known = ", ".join(self._state_props) or "none"
            raise NotFound(
                f"no window {window_id!r} in the state (it has {known})"
            ) from None

    def _start_desktop(self) -> None:
        """After a boot or a revert: the desktop the scene asked for, and a new pointer."""
        self.prepare_desktop()
        self.pointer = Pointer()
        self.pointer.attach(self)

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

    def add(self, *props: Prop) -> None:
        """Bring props on stage: start the program, find the window, place it."""
        for prop in props:
            if prop not in self.props:
                prop.attach(self)
                self.props.append(prop)

    def remove(self, *props: Prop) -> None:
        """Take props off stage. For a window that means closing it."""
        for prop in props:
            prop.remove()
            if prop in self.props:
                self.props.remove(prop)

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
        if not animations:
            raise UsageError("play needs at least one animation")
        self._check_claims(animations)
        with _overrides(animations, run_time, rate_func):
            for animation in animations:
                animation.begin(self)
            total = max(animation.run_time for animation in animations)
            count = max(1, frames(total, self.output_fps))
            live = live or any(animation.live for animation in animations)
            running = list(animations)
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

    def wait(self, duration: float = 1.0, stop_condition=None) -> None:
        """Hold for a while, or until stop_condition() is true.

        The guest keeps running, so a caret blinks and a clock ticks, and
        something that moves by itself, such as a screensaver, is recorded. These
        frames follow the host clock and skip the repaint wait, so the stretch
        depends on host speed. See also `pause`.
        """
        self._live(duration, stop_condition)

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

    def next_section(self, name: str) -> Path | None:
        """Start a new section here: end the current clip and begin the next, between two frames.

        A thin wrapper around cut(). `name` labels the section in `self.sections`
        as (name, video time it starts at); clip files stay numbered. Returns the
        closed clip's path, or None when nothing was recorded since the last cut.
        """
        if not isinstance(name, str) or not name.strip():
            raise UsageError("next_section needs a name")
        self.sections.append((name, self.time))
        return self.cut()

    def wait_until(self, stop_condition, max_time: float = 60.0) -> None:
        """Hold until stop_condition() is true, giving up after max_time."""
        self._live(max_time, stop_condition)
        if not stop_condition():
            warnings.warn(f"wait_until gave up after {max_time} s", stacklevel=2)

    def _apply(self) -> None:
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
