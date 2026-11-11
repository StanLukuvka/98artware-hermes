"""Rate functions map progress from 0 to 1 onto eased progress.

These use the same formulas as Manim, so a scene reads and moves the same.
"""

from __future__ import annotations

import math


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


def linear(t: float) -> float:
    return t


def smooth(t: float, inflection: float = 10.0) -> float:
    error = _sigmoid(-inflection / 2)
    return min(
        1.0, max(0.0, (_sigmoid(inflection * (t - 0.5)) - error) / (1 - 2 * error))
    )


def smoothstep(t: float) -> float:
    return t * t * (3 - 2 * t)


def rush_into(t: float, inflection: float = 10.0) -> float:
    return 2 * smooth(t / 2, inflection)


def rush_from(t: float, inflection: float = 10.0) -> float:
    return 2 * smooth(t / 2 + 0.5, inflection) - 1


def there_and_back(t: float, inflection: float = 10.0) -> float:
    return smooth(2 * t if t < 0.5 else 2 * (1 - t), inflection)
