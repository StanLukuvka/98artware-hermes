"""Scene: a script for one recording of the desktop."""

from __future__ import annotations

from pathlib import Path

from .. import win32
from ..actions.pointer import Pointer
from ..base.animation import Animation
from ..base.props import Box, Prop
from ..base.space import STOCK, DesktopSpace
from . import setstate, timeline

DESKTOPS = ("blank", "stock")


class Scene:
    """Subclass this and write `construct`.

    `self.guest` is the raw Bridge, so any Win32 call can sit between
    animations, `self.vm` is the VM, and `self.pointer` is the mouse pointer.
    `self.time` is video time, frames written over fps, and it never reads a
    clock. `self.recorder` (also `self.video`) writes the frames, and
    `cut()` starts a new clip, and `next_section(name)` does the same with a label. Only the `free` clock exists so far: the guest runs all the time,
    and what the script drives lands on exact frames while anything the guest
    times for itself runs by the host's clock.

    `desktop` is what the scene starts on. "blank", the default, is a black
    screen with no icons or taskbar (see art98.desktop), and no pointer
    unless `pointer_visible` is True. "stock" is the Windows 98 desktop as
    the `ready` snapshot has it, pointer included. Either way it is set again
    after every revert, so `set_state` starts from it too.

    `state`, a path to a state file, sets the desktop before `setup` runs.
    `set_state` jumps to another state at any point, and `prop(id)` is the
    prop for a window the state opened.
    """

    fps = 30
    clock = "free"
    desktop = "blank"
    pointer_visible = False
    state: str | Path | None = None
    strict_placement = (
        False  # True: a clamped size or a lost window raises PlacementError
    )

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
    def video(self):
        """Another name for `self.recorder`."""
        return self.recorder

    @video.setter
    def video(self, recorder) -> None:
        self.recorder = recorder

    @property
    def time(self) -> float:
        """Video time so far, across every clip."""
        return self.recorder.total / self.fps

    def run(self, session, video) -> None:
        """Play this scene on a booted session, into an open recorder."""
        if self.clock != "free":
            raise NotImplementedError(
                f"clock {self.clock!r}: only 'free' exists so far"
            )
        if self.desktop not in DESKTOPS:
            raise ValueError(
                f"desktop {self.desktop!r}: use one of {', '.join(DESKTOPS)}"
            )
        self.session, self.recorder = session, video
        self.sections = []
        self.guest, self.vm = session.bridge, session.vm
        # Every scene starts from the baseline: revert unless nothing has touched
        # the guest since the last revert. Forget whatever an earlier run left.
        if not session.fresh:
            session.revert()
        for prop in self.props:
            prop.forget()
        self.props.clear()
        self._state_props = {}
        self.space = STOCK
        if self.state is not None:
            self.set_state(self.state)  # owns the flag; reverts only if not fresh
        else:
            session.fresh = False  # before the start: a half-done start touched it
            self._start_desktop()
        self.setup()
        self.construct()
        self.tear_down()

    def render(self, output: Path | str | None = None, *, session, **options) -> Path:
        """Play the scene on `session` (see `art98.attach`) and write the video.

        `options` are those of `renderer.render`: gif, keep_clips and so on.
        """
        from .renderer import render

        return render(self, output, session=session, **options)

    # ---- desktop state ----

    def set_state(self, path: Path | str) -> None:
        """Make the desktop look like a state file, all within one frame.

        This is a Set: revert the guest to the snapshot, apply the state, and
        check it took. Props from before are gone, as are their windows. A
        relative path is looked for next to the scene's file, then in the
        current folder. Nothing is recorded, so the next frame shows the state.
        """
        setstate.set_state(self, path)

    def prop(self, window_id: str) -> Prop:
        """The prop for a window the last state opened, by its id."""
        try:
            return self._state_props[window_id]
        except KeyError:
            known = ", ".join(self._state_props) or "none"
            raise KeyError(
                f"no window {window_id!r} in the state (it has {known})"
            ) from None

    def _start_desktop(self) -> None:
        """After a boot or a revert: the desktop the scene asked for, and a new pointer."""
        setstate.start_desktop(self)

    def prepare_desktop(self) -> None:
        """Put the desktop the scene asked for in place, then read the work area."""
        setstate.prepare_desktop(self)

    def calibrate(self) -> None:
        """Read the desktop from the guest again: screen, work area and metrics.
        Call it after a script changes any of them with raw guest calls."""
        self.space = win32.calibrate(self.guest, self.strict_placement)

    @property
    def work_area(self) -> tuple[int, int, int, int]:
        """(left, top, right, bottom) above the taskbar, or the whole screen when blank."""
        return self.space.work_area

    def _find_state(self, path: Path) -> Path:
        return setstate.find_state(self, path)

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

        run_time and rate_func override every animation given. Shorter
        animations finish early and hold. With live=True, frames follow the
        host clock, for guest behaviour that needs real time to happen.
        """
        timeline.play(self, animations, run_time, rate_func, live)

    def wait(
        self, duration: float = 1.0, stop_condition=None, frozen_frame: bool = False
    ) -> None:
        """Hold for a while, or until stop_condition() is true.

        The guest keeps running, so a caret blinks and a clock ticks. These
        frames follow the host clock. frozen_frame=True repeats one capture
        instead, which costs almost nothing. See also `pause`.
        """
        timeline.wait(self, duration, stop_condition, frozen_frame)

    def pause(self, duration: float = 1.0) -> None:
        """Hold one capture. Nothing the guest does in this time is seen."""
        timeline.pause(self, duration)

    def live(self, seconds: float, stop_condition=None) -> None:
        """Record something that moves by itself, such as a screensaver.

        Frames follow the host clock and skip the repaint wait, so this
        stretch depends on host speed. It wraps `Recorder.live`.
        """
        timeline.live(self, seconds, stop_condition)

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
            raise ValueError("next_section needs a name")
        self.sections.append((name, self.time))
        return self.cut()

    def wait_until(self, stop_condition, max_time: float = 60.0) -> None:
        """Hold until stop_condition() is true, giving up after max_time."""
        timeline.wait_until(self, stop_condition, max_time)

    def _apply(self) -> None:
        timeline.apply_props(self)

    @staticmethod
    def _check_claims(animations) -> None:
        timeline.check_claims(animations)
