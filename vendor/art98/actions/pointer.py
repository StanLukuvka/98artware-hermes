"""The mouse pointer, and animations that move it and press its buttons.

Pointer moves go through SetCursorPos and buttons through mouse_event, which
is real input: Windows routes it, so menus open and windows drag themselves.
"""

from __future__ import annotations

from .. import win32
from ..base.animation import Animation, path_function
from ..base.props import Prop, Spot, point_of
from ..base.rate_functions import smooth
from ..base.vectors import Vec, lerp
from ..win32 import MOUSE_BUTTONS, cursor_pos

BUTTONS = MOUSE_BUTTONS


class Pointer(Prop):
    """The guest's mouse pointer. Every scene has one, as `self.pointer`."""

    claims = ("pointer",)

    def attach(self, scene) -> None:
        super().attach(scene)
        self.refresh()
        self._sent = self.get_center().rounded()
        # A fresh boot shows a brighter, pointerless desktop until the first move: experiments/first-frame/README.md
        self.guest.user32.SetCursorPos(*self._sent)

    def refresh(self) -> None:
        self.x, self.y = cursor_pos(self.guest)

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
    """How far progress is through the stretch from start to end."""
    return (
        0.0
        if progress <= start
        else 1.0
        if progress >= end
        else (progress - start) / (end - start)
    )


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
        self.end = point_of(self.target)
        self.events = self.schedule()

    def schedule(self) -> list:
        return []

    def update(self, progress: float) -> None:
        self.pointer.place(
            lerp(self.start, self.end, self.rate_func(_span(progress, 0, self.travel)))
        )
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
    """

    live = True

    def __init__(
        self, target, *, hold: float = 1.0, travel_time: float = 0.5, **kwargs
    ):
        super().__init__(target, **kwargs)
        self.run_time = travel_time + hold
        self.travel = travel_time / self.run_time


class Click(PointerAction):
    """Travel to a target, press and release a button."""

    run_time = 0.65
    travel = 0.74
    button = "left"

    def __init__(self, target, *, button: str | None = None, **kwargs):
        super().__init__(target, **kwargs)
        if button is not None:
            self.button = button

    def schedule(self) -> list:
        return [(self.travel, self._press), (0.84, self._release)]

    def _press(self) -> None:
        self.pointer.press(self.button)

    def _release(self) -> None:
        self.pointer.release(self.button)


class RightClick(Click):
    """Click with the right button. A context menu opens on the release."""

    button = "right"


class DoubleClick(Click):
    """Two clicks, a few frames apart.

    Windows counts a double click by its own clock (500 ms), so both clicks
    fall inside the same short stretch of the run time.
    """

    run_time = 0.75
    travel = 0.66

    def schedule(self) -> list:
        return [
            (0.66, self._press),
            (0.72, self._release),
            (0.78, self._press),
            (0.84, self._release),
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
            raise ValueError("Drag needs either to= or by=")
        super().__init__(source, **kwargs)
        self.to, self.by, self.button = to, by, button
        self.grab = self.goal = Vec(0, 0)
        self.window = getattr(source, "window", None)

    def schedule(self) -> list:
        self.grab = self.end
        self.goal = self.grab + self.by if self.by is not None else point_of(self.to)
        return [
            (self.travel, self._take_hold),
            (self.drop, lambda: self.pointer.release(self.button)),
        ]

    def _take_hold(self) -> None:
        self.pointer.place(self.grab)
        self.pointer.press(self.button)

    def update(self, progress: float) -> None:
        if progress < self.travel:
            self.pointer.place(
                lerp(
                    self.start,
                    self.grab,
                    self.rate_func(_span(progress, 0, self.travel)),
                )
            )
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
    travel_time = 0.4

    def __init__(
        self, path, *, button: str = "left", steps_per_frame: int = 4, **kwargs
    ):
        super().__init__(None, **kwargs)
        self.path = path_function(path)
        self.button = button
        self.steps = steps_per_frame
        self._done = 0.0

    def begin(self, scene) -> None:
        self.target = Spot(lambda: self.path(0))
        self.travel = (
            min(0.5, self.travel_time / self.run_time) if self.run_time else 0.0
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
