"""Screen directions, in pixels with y running down, as Win32 has it."""

from __future__ import annotations

from typing import NamedTuple

from .units import pixel


class Vec(NamedTuple):
    """A point or offset on the screen. It adds and scales like a vector."""

    x: float
    y: float

    def __add__(self, other) -> Vec:
        return Vec(self.x + other[0], self.y + other[1])

    def __radd__(self, other) -> Vec:
        """Lets `sum(points)` work: its starting 0 counts as the zero vector."""
        if isinstance(other, int) and other == 0:
            return self
        return self + other

    def __sub__(self, other) -> Vec:
        return Vec(self.x - other[0], self.y - other[1])

    def __rsub__(self, other) -> Vec:
        return Vec(other[0] - self.x, other[1] - self.y)

    def __mul__(self, factor: float) -> Vec:
        return Vec(self.x * factor, self.y * factor)

    __rmul__ = __mul__

    def __truediv__(self, factor: float) -> Vec:
        return Vec(self.x / factor, self.y / factor)

    def __neg__(self) -> Vec:
        return Vec(-self.x, -self.y)

    def rounded(self) -> tuple[int, int]:
        return pixel(self.x), pixel(self.y)


ORIGIN = Vec(0, 0)
UP = Vec(0, -1)
DOWN = Vec(0, 1)
LEFT = Vec(-1, 0)
RIGHT = Vec(1, 0)
UL = UP + LEFT
UR = UP + RIGHT
DL = DOWN + LEFT
DR = DOWN + RIGHT
