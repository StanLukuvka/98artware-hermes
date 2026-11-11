"""The frame timeline of a Scene: play, wait, pause, live. Plain functions, scene first."""

from __future__ import annotations

import time
import warnings


def play(scene, animations, run_time, rate_func, live) -> None:
    if not animations:
        raise ValueError("play needs at least one animation")
    scene._check_claims(animations)
    for animation in animations:
        if run_time is not None:
            animation.run_time = run_time
        if rate_func is not None:
            animation.rate_func = rate_func
        animation.begin(scene)
    total = max(animation.run_time for animation in animations)
    count = max(1, round(total * scene.fps))
    live = live or any(animation.live for animation in animations)
    running = list(animations)
    started = time.monotonic()
    for index in range(1, count + 1):
        elapsed = index / count * total
        for animation in list(running):
            length = animation.run_time
            progress = (
                1.0 if length <= 0 or index == count else min(1.0, elapsed / length)
            )
            animation.update(progress)
            if progress >= 1:
                animation.finish(scene)
                running.remove(animation)
        scene._apply()
        if live:
            time.sleep(max(0.0, started + index / scene.fps - time.monotonic()))
        scene.recorder.frame()


def wait(scene, duration, stop_condition, frozen_frame) -> None:
    if frozen_frame:
        scene.pause(duration)
    else:
        scene.live(duration, stop_condition)


def pause(scene, duration) -> None:
    scene._apply()
    scene.recorder.frame(hold=max(1, round(duration * scene.fps)))


def live(scene, seconds, stop_condition) -> None:
    scene._apply()
    scene.recorder.live(seconds, stop_condition)


def wait_until(scene, stop_condition, max_time) -> None:
    scene.live(max_time, stop_condition)
    if not stop_condition():
        # stacklevel 3: this function, the Scene delegate, then the script line
        warnings.warn(f"wait_until gave up after {max_time} s", stacklevel=3)


def apply_props(scene) -> None:
    scene.pointer.apply()
    for prop in scene.props:
        prop.apply()


def check_claims(animations) -> None:
    taken: set[str] = set()
    for animation in animations:
        for claim in animation.claims:
            if claim in taken:
                raise ValueError(f"two animations in one play both drive the {claim}")
            taken.add(claim)
