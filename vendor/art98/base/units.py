"""Pixel, frame and progress math: the one home for rounding, distance and blending."""

from __future__ import annotations

import math


def pixel(v: float) -> int:
    """Round half up. Python's `round` goes to even, which steps 0.5 px/frame unevenly."""
    return math.floor(v + 0.5)


def frames(seconds: float, fps: float) -> int:
    """How many frames `seconds` takes at `fps`, rounded half up."""
    return pixel(seconds * fps)


def count(alpha: float, n: int) -> int:
    """How many of `n` items are done at progress `alpha`, half up, from 0 to n."""
    return clamp(pixel(alpha * n), 0, n)


def distance(a, b) -> float:
    """Straight-line distance between two points."""
    return math.hypot(b[0] - a[0], b[1] - a[1])


def clamp(v, lo, hi):
    """`v` limited to the range lo..hi."""
    return min(max(v, lo), hi)


def lerp(a, b, alpha: float):
    """The value `alpha` of the way from `a` to `b`: a number, or a `Vec` for points."""
    if isinstance(a, (int, float)):
        return a + (b - a) * alpha
    from .vectors import Vec  # vectors uses pixel from here

    return Vec(a[0] + (b[0] - a[0]) * alpha, a[1] + (b[1] - a[1]) * alpha)
