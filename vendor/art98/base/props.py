"""Props: host-side handles on things that exist inside the guest."""

from __future__ import annotations

from collections.abc import Callable

from .space import STOCK, DesktopSpace
from .vectors import DL, LEFT, ORIGIN, RIGHT, Vec

DEFAULT_BUFF = 8


class Spot:
    """A place on screen that is worked out when it is needed.

    Controls move with their windows, so a target such as a Paint tool is
    located when an animation begins, not when the scene names it.
    """

    def __init__(self, locate: Callable[[], tuple[float, float]]):
        self._locate = locate

    def point(self) -> Vec:
        return Vec(*self._locate())


def point_of(target) -> Vec:
    """Where a target is: a point, a Spot, or a prop's centre."""
    locate = getattr(target, "point", None)
    return Vec(*locate()) if locate else Vec(target[0], target[1])


class Box:
    """A rectangle in screen pixels, with Manim's layout methods.

    These only change numbers on the host. A prop's values reach the guest on
    the next frame, or at once with `apply()`.
    """

    claims: tuple[str, ...] = ()
    LAYOUT = frozenset(
        (
            "shift",
            "move_to",
            "center",
            "to_edge",
            "to_corner",
            "next_to",
            "resize",
            "set_rect",
            "scale",
        )
    )

    def __init__(self, x: float = 0, y: float = 0, width: float = 0, height: float = 0):
        self.x, self.y, self.width, self.height = x, y, width, height
        self._space: DesktopSpace | None = None

    @property
    def space(self) -> DesktopSpace:
        """The desktop this box is placed in. A plain box has the stock one."""
        return self._space or STOCK

    @property
    def rect(self) -> tuple[int, int, int, int]:
        """(left, top, right, bottom), the same order as a Win32 RECT."""
        x, y = round(self.x), round(self.y)
        return x, y, x + round(self.width), y + round(self.height)

    @property
    def size(self) -> tuple[int, int]:
        return round(self.width), round(self.height)

    def get_center(self) -> Vec:
        return Vec(self.x + self.width / 2, self.y + self.height / 2)

    def point(self) -> Vec:
        return self.get_center()

    def get_corner(self, direction) -> Vec:
        return self.get_center() + Vec(
            direction[0] * self.width / 2, direction[1] * self.height / 2
        )

    def refresh(self) -> None:
        """Read the real position back. A plain Box has nothing to read."""

    def ghost(self) -> Box:
        """A copy to try layout calls on, without touching the prop."""
        ghost = Box(self.x, self.y, self.width, self.height)
        ghost._space = self.space
        return ghost

    def shift(self, *vectors) -> Box:
        for vector in vectors:
            self.x += vector[0]
            self.y += vector[1]
        return self

    def move_to(self, target, aligned_edge=ORIGIN) -> Box:
        """Put this box's centre on the target, or the given edge or corner."""
        return self.shift(point_of(target) - self.get_corner(aligned_edge))

    def center(self) -> Box:
        """Centre in the work area: the whole screen, or above the taskbar."""
        left, top, right, bottom = self.space.work_area
        return self.move_to(((left + right) / 2, (top + bottom) / 2))

    def to_edge(self, edge=LEFT, buff: float = 0) -> Box:
        left, top, right, bottom = self.space.work_area
        if edge[0]:
            self.x = left + buff if edge[0] < 0 else right - buff - self.width
        if edge[1]:
            self.y = top + buff if edge[1] < 0 else bottom - buff - self.height
        return self

    def to_corner(self, corner=DL, buff: float = 0) -> Box:
        return self.to_edge(corner, buff)

    def next_to(self, other, direction=RIGHT, buff: float = DEFAULT_BUFF) -> Box:
        """Sit beside another box, centred on it along the other axis."""
        other.refresh()
        gap = Vec(
            direction[0] * (other.width + self.width) / 2 + direction[0] * buff,
            direction[1] * (other.height + self.height) / 2 + direction[1] * buff,
        )
        return self.move_to(other.get_center() + gap)

    def resize(self, width: float, height: float) -> Box:
        """Change size, keeping the top left corner where it is."""
        self.width, self.height = width, height
        return self

    def set_rect(self, x: float, y: float, width: float, height: float) -> Box:
        self.x, self.y, self.width, self.height = x, y, width, height
        return self

    def scale(self, factor: float) -> Box:
        """Grow or shrink about the centre. A real app keeps its font size."""
        centre = self.get_center()
        self.width *= factor
        self.height *= factor
        return self.move_to(centre)


class Prop(Box):
    """Something on stage. Adding it to a scene is what brings it to life."""

    def __init__(self, x: float = 0, y: float = 0, width: float = 0, height: float = 0):
        super().__init__(x, y, width, height)
        self.scene = None

    @property
    def animate(self):
        from .animation import Animate

        return Animate(self)

    @property
    def space(self) -> DesktopSpace:
        """The scene's space, read through the scene so a re-calibration reaches every prop."""
        return self.scene.space if self.scene is not None else super().space

    @property
    def guest(self):
        if self.scene is None:
            raise RuntimeError(
                f"{type(self).__name__} is not on stage: add it to the scene first"
            )
        return self.scene.guest

    def attach(self, scene) -> None:
        self.scene = scene

    def apply(self) -> None:
        """Send any changed values to the guest."""

    def remove(self) -> None:
        """Leave the stage."""

    def forget(self) -> None:
        """Leave the stage without touching the guest, after a revert."""
        self.scene = None
