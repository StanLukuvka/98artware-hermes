"""Animations: what changes over a run_time, and how the changes combine."""

from __future__ import annotations

from collections.abc import Callable
from itertools import pairwise

from .props import Box, Prop, point_of
from .rate_functions import linear, smooth
from .vectors import lerp


class Animation:
    """One change over time.

    `Scene.play` calls `begin` once, then `update(progress)` on every frame
    with progress running to exactly 1, then `finish`. `update` applies the
    rate function and calls `interpolate(alpha)`, which is the method to
    override. Subclasses that run several timed stages override `update`
    instead, so the rate function shapes only the part it suits.

    `claims` names things only one animation may drive at a time, and `live`
    makes `play` pace frames by the host clock so the guest's own timers run
    at video speed.
    """

    run_time = 1.0
    rate_func = staticmethod(smooth)
    claims: tuple[str, ...] = ()
    live = False

    def __init__(
        self,
        target=None,
        *,
        run_time: float | None = None,
        rate_func: Callable[[float], float] | None = None,
    ):
        self.target = target
        self.scene = None
        if run_time is not None:
            self.run_time = run_time
        if rate_func is not None:
            self.rate_func = rate_func

    def begin(self, scene) -> None:
        self.scene = scene
        if isinstance(self.target, Prop) and self.target.scene is None:
            scene.add(self.target)

    def update(self, progress: float) -> None:
        self.interpolate(self.rate_func(progress))

    def interpolate(self, alpha: float) -> None:
        pass

    def finish(self, scene) -> None:
        """Once, at the end: a window target is read back (not per frame) so a
        place the guest did not give is reported."""
        settle = getattr(self.target, "settle", None)  # Box targets and None have none
        if settle is not None:
            settle()


class Wait(Animation):
    """Do nothing for a while. Useful inside a Succession."""

    rate_func = staticmethod(linear)

    def __init__(self, run_time: float = 1.0):
        super().__init__(run_time=run_time)


class Instant(Animation):
    """Something Windows does at once. It takes one frame on its own."""

    run_time = 0.0
    rate_func = staticmethod(linear)

    def __init__(self, target=None, **kwargs):
        super().__init__(target, **kwargs)
        self._done = False

    def update(self, progress: float) -> None:
        if not self._done:
            self._done = True
            self.act()

    def act(self) -> None:
        raise NotImplementedError


class AnimationGroup(Animation):
    """Run animations together, or staggered by lag_ratio.

    With lag_ratio 0 they all start at once. With 1 each starts as the one
    before it ends. A child's `begin` runs when its turn comes, so a Click
    starts from wherever the pointer is by then.
    """

    rate_func = staticmethod(linear)

    def __init__(
        self,
        *animations: Animation,
        lag_ratio: float = 0.0,
        run_time: float | None = None,
        rate_func=None,
    ):
        super().__init__(run_time=run_time, rate_func=rate_func)
        self.animations = animations
        self.lag_ratio = lag_ratio
        self._spans: list[tuple[float, float]] = []
        start = 0.0
        for animation in animations:
            self._spans.append((start, start + animation.run_time))
            start += animation.run_time * lag_ratio
        self.natural_time = max((end for _, end in self._spans), default=0.0)
        if run_time is None:
            self.run_time = self.natural_time
        self.claims = tuple({claim for a in animations for claim in a.claims})
        self._check_overlaps()
        self._begun: set[int] = set()
        self._finished: set[int] = set()

    def _check_overlaps(self) -> None:
        for i, first in enumerate(self.animations):
            for j, second in enumerate(self.animations[:i]):
                a, b = self._spans[i], self._spans[j]
                shared = set(first.claims) & set(second.claims)
                if shared and a[0] < b[1] and b[0] < a[1]:
                    raise ValueError(
                        f"two animations in a group both drive the {shared.pop()}"
                    )

    def begin(self, scene) -> None:
        super().begin(scene)
        self._begun.clear()
        self._finished.clear()

    def update(self, progress: float) -> None:
        now = self.rate_func(progress) * self.natural_time
        if progress >= 1:
            now = self.natural_time
        for index, (animation, (start, end)) in enumerate(
            zip(self.animations, self._spans)
        ):
            if index in self._finished or now < start:
                continue
            if index not in self._begun:
                self._begun.add(index)
                animation.begin(self.scene)
            local = 1.0 if end <= start else min(1.0, (now - start) / (end - start))
            animation.update(local)
            if local >= 1:
                self._finished.add(index)
                animation.finish(self.scene)


class Succession(AnimationGroup):
    """Run animations one after another."""

    def __init__(self, *animations: Animation, **kwargs):
        kwargs.setdefault("lag_ratio", 1.0)
        super().__init__(*animations, **kwargs)


class LaggedStart(AnimationGroup):
    """Start each animation a fraction of the way through the one before."""

    def __init__(self, *animations: Animation, lag_ratio: float = 0.05, **kwargs):
        super().__init__(*animations, lag_ratio=lag_ratio, **kwargs)


class MoveAlongPath(Animation):
    """Carry a prop's centre along a path.

    The path is a list of points, or a function from 0..1 to a point.
    """

    def __init__(self, target: Box, path, **kwargs):
        super().__init__(target, **kwargs)
        self.path = path_function(path)

    def begin(self, scene) -> None:
        super().begin(scene)
        self.target.refresh()

    def interpolate(self, alpha: float) -> None:
        self.target.move_to(self.path(alpha))


def path_function(path) -> Callable[[float], tuple[float, float]]:
    """Turn a list of points into a function of 0..1, by distance along it."""
    if callable(path):
        return path
    points = [point_of(p) for p in path]
    if len(points) < 2:
        raise ValueError("a path needs at least two points")
    lengths = [abs(b.x - a.x) + abs(b.y - a.y) for a, b in pairwise(points)]
    total = sum(lengths) or 1.0

    def along(t: float):
        remaining = max(0.0, min(1.0, t)) * total
        for (a, b), length in zip(pairwise(points), lengths):
            if remaining <= length and length:
                return lerp(a, b, remaining / length)
            remaining -= length
        return points[-1]

    return along


class Animate(Animation):
    """What `prop.animate` builds: layout methods played as an animation.

    The calls are replayed on a copy to find the end state, and only the
    fields that differ are driven, so a move and a resize of one window can
    share a `play` and still reach the guest as one SetWindowPos per frame.
    """

    def __init__(self, target: Box):
        super().__init__(target)
        self.claims = target.claims
        self._calls: list[tuple[str, tuple, dict]] = []
        self._start: dict[str, float] = {}
        self._end: dict[str, float] = {}

    def __call__(self, *, run_time=None, rate_func=None) -> Animate:
        if run_time is not None:
            self.run_time = run_time
        if rate_func is not None:
            self.rate_func = rate_func
        return self

    def __getattr__(self, name: str):
        if name not in Box.LAYOUT:
            raise AttributeError(f"{name!r} cannot be animated")

        def record(*args, **kwargs) -> Animate:
            self._calls.append((name, args, kwargs))
            return self

        return record

    def begin(self, scene) -> None:
        super().begin(scene)
        self.target.refresh()
        ghost = self.target.ghost()
        for name, args, kwargs in self._calls:
            getattr(ghost, name)(*args, **kwargs)
        fields = ("x", "y", "width", "height")
        self._start = {f: getattr(self.target, f) for f in fields}
        self._end = {
            f: getattr(ghost, f) for f in fields if getattr(ghost, f) != self._start[f]
        }

    def interpolate(self, alpha: float) -> None:
        for field, end in self._end.items():
            start = self._start[field]
            setattr(self.target, field, start + (end - start) * alpha)
