"""The mouse pointer, and animations that move it and press its buttons.

Pointer moves go through SetCursorPos and buttons through mouse_event, which
is real input: Windows routes it, so menus open and windows drag themselves.
"""

from __future__ import annotations

from ..base.animation import Animation, path_function
from ..base.props import Prop, Spot, point_of
from ..base.rate_functions import smooth
from ..base.units import clamp, lerp
from ..base.vectors import Vec
from ..errors import UsageError
from ..guest import win32

# Button timings are in seconds, so a scene's run_time= doesn't change them. Each
# is turned into a share of run_time by `PointerAction.at_seconds`.
CLICK_HOLD = 0.065  # seconds a click holds the button down
DOUBLE_CLICK_HOLD = (
    0.045  # seconds each press of a double click holds; presses are 0.09 s apart
)
# A pen's travel to the start of a path takes at most this share of the run time.
STROKE_TRAVEL_CAP = 0.5


class Pointer(Prop):
    """The guest's mouse pointer. Every scene has one, as `self.pointer`."""

    claims = ("pointer",)

    def attach(self, scene) -> None:
        super().attach(scene)
        self.refresh()
        self._sent = self.get_center().rounded()
        # A fresh boot shows a brighter, pointerless desktop until the first move: experiments/first-frame/README.md (dev branch)
        self.guest.user32.SetCursorPos(*self._sent)

    def refresh(self) -> None:
        self.x, self.y = win32.cursor_pos(self.guest)

    def apply(self) -> None:
        wanted = self.get_center().rounded()
        if wanted != self._sent:
            self.guest.user32.SetCursorPos(*wanted)
            self._sent = wanted

    def place(self, point) -> None:
        """Jump there now, with no frames in between."""
        self.x, self.y = point_of(point)
        self.apply()

    def press(self, button: str = "left") -> None:
        win32.mouse_button(self.guest, button, True)

    def release(self, button: str = "left") -> None:
        win32.mouse_button(self.guest, button, False)


def _span(progress: float, start: float, end: float) -> float:
    """How far progress is through the stretch from start to end, from 0 to 1."""
    if end <= start:  # an empty stretch is done as soon as progress reaches it
        return 0.0 if progress <= start else 1.0
    return clamp((progress - start) / (end - start), 0.0, 1.0)


class PointerAction(Animation):
    """Pointer travel, then button events at set points in the run time."""

    claims = ("pointer",)
    travel = 1.0

    def __init__(self, target, **kwargs):
        super().__init__(target, **kwargs)
        self.pointer = None
        self.start = self.end = Vec(0, 0)
        self.events: list = []

    def begin(self, scene) -> None:
        super().begin(scene)
        self.pointer = scene.pointer
        self.pointer.refresh()
        self.start = self.pointer.get_center()
        self.end = point_of(self.aim())
        self.events = self.schedule()

    def aim(self):
        """Where the pointer goes: the target, unless an action says otherwise."""
        return self.target

    def schedule(self) -> list:
        return []

    def at_seconds(self, seconds: float) -> float:
        """The share of run_time that `seconds` makes up, so an event keeps its real timing."""
        return seconds / self.run_time if self.run_time else 1.0

    def travel_to_start(self, progress: float) -> None:
        """Carry the pointer from where it was to `self.end`, over the travel stretch."""
        self.pointer.place(
            lerp(self.start, self.end, self.rate_func(_span(progress, 0, self.travel)))
        )

    def update(self, progress: float) -> None:
        self.travel_to_start(progress)
        self.fire(progress)

    def fire(self, progress: float) -> None:
        while self.events and progress >= self.events[0][0]:
            self.events.pop(0)[1]()


class Glide(PointerAction):
    """Move the pointer to a target with no buttons held."""

    run_time = 0.5


class Hover(PointerAction):
    """Move to a target and stay, in real time, so a tooltip can come up.

    `hold` is seconds at the target. Tooltips are timed by the guest, so the
    frames of this animation follow the host clock.

    A hover over the taskbar clock shows a tooltip that Windows times itself,
    and it can still be up when the play returns. The scene's tracking checks
    let it be, and the claim ends once it has been seen and is gone (or after
    `CLOSE_TIMEOUT` seconds if none shows).
    """

    live = True

    def __init__(
        self, target, *, hold: float = 1.0, travel_time: float = 0.5, **kwargs
    ):
        if "run_time" in kwargs:
            raise UsageError("Hover takes hold= and travel_time=, not run_time=")
        super().__init__(target, **kwargs)
        self.run_time = travel_time + hold
        self.travel = travel_time / self.run_time if self.run_time else 0.0

    def schedule(self) -> list:
        return [(self.travel, self._claim_tooltip)]

    def _claim_tooltip(self) -> None:
        self.scene.claim_until_gone(
            "Hover's tooltip", lambda row: row.class_name == win32.TOOLTIP_CLASS
        )


class Click(PointerAction):
    """Travel to a target, press and release a button."""

    run_time = 0.65
    travel = 0.74
    button = "left"

    def __init__(self, target, *, button: str | None = None, **kwargs):
        super().__init__(target, **kwargs)
        if button is not None:
            self.button = button
        win32.check_mouse_button(self.button)

    def schedule(self) -> list:
        return [
            (self.travel, self._press),
            (self.travel + self.at_seconds(CLICK_HOLD), self._release),
        ]

    def _press(self) -> None:
        self.pointer.press(self.button)

    def _release(self) -> None:
        self.pointer.release(self.button)


class RightClick(Click):
    """Click with the right button. A context menu opens on the release.

    The menu stays up until something dismisses it, such as `Keys("esc")`. Until
    then the scene's tracking checks let it be. The claim waits for the menu to
    show up, since the guest may draw it after this play ends, and ends once it
    has been seen and is gone. If no menu appears within `CLOSE_TIMEOUT` seconds
    the claim ends anyway. Pass `menu=False` for a right click that opens no menu
    (a flag in Minesweeper), so it claims nothing.
    """

    button = "right"

    def __init__(self, target, *, menu: bool = True, **kwargs):
        super().__init__(target, **kwargs)
        self.menu = menu

    def _release(self) -> None:
        super()._release()
        if self.menu:
            self._claim_menu()

    def _claim_menu(self) -> None:
        self.scene.claim_until_gone(
            "RightClick's menu", lambda row: row.class_name == win32.MENU_CLASS
        )


class DoubleClick(Click):
    """Two clicks, one after the other.

    Windows counts a double click by its own clock (500 ms). The presses here
    are 0.09 s apart at any run_time, so Windows sees one double click.
    """

    run_time = 0.75
    travel = 0.66

    def schedule(self) -> list:
        hold = self.at_seconds(DOUBLE_CLICK_HOLD)
        first_release = self.travel + hold
        second_press = first_release + hold
        return [
            (self.travel, self._press),
            (first_release, self._release),
            (second_press, self._press),
            (second_press + hold, self._release),
        ]


class Drag(PointerAction):
    """Press on one target, carry the pointer to another, and let go.

    `to` is a target, or use `by` for an offset from where the drag starts.
    Windows moves whatever was grabbed, so a title bar drags its window.
    """

    run_time = 1.2
    travel = 0.3
    drop = 0.9

    def __init__(self, source, to=None, *, by=None, button: str = "left", **kwargs):
        if (to is None) == (by is None):
            raise UsageError("Drag needs either to= or by=")
        super().__init__(source, **kwargs)
        win32.check_mouse_button(button)
        self.to, self.by, self.button = to, by, button
        self.grab = self.goal = Vec(0, 0)
        # The Win a title bar or corner belongs to, so the drag can read it back.
        self.window = source.window if isinstance(source, Spot) else None

    def begin(self, scene) -> None:
        super().begin(scene)
        self.grab = self.end
        self.goal = self.grab + self.by if self.by is not None else point_of(self.to)

    def schedule(self) -> list:
        return [
            (self.travel, self._take_hold),
            (self.drop, lambda: self.pointer.release(self.button)),
        ]

    def _take_hold(self) -> None:
        self.pointer.place(self.grab)
        self.pointer.press(self.button)

    def update(self, progress: float) -> None:
        if progress < self.travel:
            self.travel_to_start(progress)
            return
        self.fire(self.travel)
        carried = self.rate_func(_span(progress, self.travel, self.drop))
        self.pointer.place(lerp(self.grab, self.goal, carried))
        self.fire(progress)
        if self.window is not None:
            self.window.repaint_if_moved()

    def finish(self, scene) -> None:
        if self.window is not None:
            self.window.repaint_if_moved()
        scene.refresh_props()


class Stroke(PointerAction):
    """Hold a button down along a path, as a pen does.

    The path is a list of points, or a function from 0..1 to a point. The
    pointer first travels to the start of the path. Each frame moves it
    `steps_per_frame` times and waits for the guest after each move: Windows
    keeps only the latest pointer position, so without the wait an app sees a
    few corners instead of a curve.
    """

    run_time = 2.0

    def __init__(
        self,
        path,
        *,
        button: str = "left",
        steps_per_frame: int = 4,
        travel_time: float = 0.4,
        **kwargs,
    ):
        super().__init__(None, **kwargs)
        win32.check_mouse_button(button)
        if steps_per_frame < 1:
            raise UsageError(f"steps_per_frame is {steps_per_frame}; use 1 or more")
        self.path = path_function(path)
        self.button = button
        self.steps = steps_per_frame
        self.travel_time = travel_time
        self._done = 0.0

    def begin(self, scene) -> None:
        self.target = Spot(lambda: self.path(0))
        self.travel = (
            min(STROKE_TRAVEL_CAP, self.travel_time / self.run_time)
            if self.run_time
            else 0.0
        )
        self._done = 0.0
        super().begin(scene)

    def schedule(self) -> list:
        return [(self.travel, self._pen_down)]

    def _pen_down(self) -> None:
        self.pointer.place(self.end)
        self.pointer.press(self.button)

    def update(self, progress: float) -> None:
        if progress < self.travel:
            self.pointer.place(
                lerp(self.start, self.end, smooth(_span(progress, 0, self.travel)))
            )
            return
        self.fire(self.travel)
        drawn = self.rate_func(_span(progress, self.travel, 1.0))
        for step in range(1, self.steps + 1):
            self.pointer.place(
                self.path(self._done + (drawn - self._done) * step / self.steps)
            )
            self.scene.guest.sync()
        self._done = drawn
        if progress >= 1:
            self.pointer.release(self.button)
