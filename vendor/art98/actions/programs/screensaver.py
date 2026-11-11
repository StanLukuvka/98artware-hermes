"""Screensaver: one of the image's screensavers, started directly with /s."""

from __future__ import annotations

import time

from ...base.vectors import Vec
from ...bridge import BridgeTimeout
from ..window.win import Win


class Screensaver(Win):
    """A screensaver, started directly with /s.

    Adding it starts it. Follow with `self.live(seconds)`, because it runs by
    itself. `stop` nudges the pointer, which is how a real one is dismissed.
    """

    window_class = "WindowsScreenSaverClass"
    NAMES = (
        "3D Flower Box",
        "3D Flying Objects",
        "3D Maze",
        "3D Pipes",
        "3D Text",
        "Blank Screen",
        "Curves and Colors",
        "Flying Through Space",
        "Flying Windows",
        "Mystify Your Mind",
    )

    def __init__(self, name: str = "3D Pipes"):
        if name not in self.NAMES:
            raise ValueError(
                f"no screensaver {name!r}; the image has {', '.join(self.NAMES)}"
            )
        super().__init__(command=f'"C:\\WINDOWS\\SYSTEM\\{name}.scr" /s')
        self.name = name

    def ready(self) -> None:
        pass  # it paints for ever, so waiting for it to settle would only time out

    def apply(self) -> None:
        pass

    def stop(self, timeout: float = 5.0) -> None:
        """Move the pointer, and wait for the screensaver to end."""
        pointer = self.scene.pointer
        pointer.refresh()
        deadline = time.monotonic() + timeout
        step = 40
        while self.guest.user32.IsWindow(self.hwnd):
            if time.monotonic() > deadline:
                raise BridgeTimeout(f"{self.name} did not stop")
            pointer.place(pointer.get_center() + Vec(step, step))
            step = -step
            time.sleep(0.1)

    def remove(self) -> None:
        self.stop()
