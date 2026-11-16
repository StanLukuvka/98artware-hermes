"""Screensaver: one of the image's screensavers, started directly with /s."""

from __future__ import annotations

import time

from ...base.vectors import Vec
from ...errors import NotFound
from ...guest import win32
from ...guest.bridge import BridgeTimeout
from ..window.win import Win

STOP_TIMEOUT = 5.0  # seconds a screensaver has to end once the pointer moves
STOP_POLL = 0.1  # seconds between the pointer moves that end it
STOP_STEP = 40  # pixels each pointer move goes, towards the middle of the screen


class Screensaver(Win):
    """A screensaver, started directly with /s.

    Adding it starts it. It fills the screen, so it cannot be moved or sized:
    `at`, `size` and the layout calls do nothing to it. In a scene's
    `construct`, follow the add with `self.wait(seconds)`, because it runs by
    itself. `stop` moves the pointer, which is how a real one is dismissed, and
    puts the pointer back where it was.
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
        "Scrolling Marquee",
    )

    def __init__(self, name: str = "3D Pipes"):
        if name not in self.NAMES:
            raise NotFound(
                f"no screensaver {name!r}; the image has {', '.join(self.NAMES)}"
            )
        super().__init__(command=f'"C:\\WINDOWS\\SYSTEM\\{name}.scr" /s')
        self.name = name

    def ready(self) -> None:
        pass  # it paints for ever, so waiting for it to settle would only time out

    def apply(self) -> None:
        pass  # it fills the screen and has no place of its own to send

    def stop(self, timeout: float = STOP_TIMEOUT) -> None:
        """Move the pointer until the screensaver ends, then put it back.

        Raises BridgeTimeout if it has not ended after `timeout` seconds.
        """
        guest = self.guest
        pointer = self.scene.pointer
        pointer.refresh()
        home = pointer.get_center()
        width, height = win32.screen_size(guest)
        # Each move goes towards the middle, so the pointer stays on the screen.
        steps = Vec(
            -STOP_STEP if home.x > width / 2 else STOP_STEP,
            -STOP_STEP if home.y > height / 2 else STOP_STEP,
        )
        deadline = time.monotonic() + timeout
        while guest.user32.IsWindow(self.hwnd).value:
            if time.monotonic() > deadline:
                raise BridgeTimeout(f"{self.name} did not stop")
            pointer.place(pointer.get_center() + steps)
            steps = -steps
            time.sleep(STOP_POLL)
        pointer.place(home)

    def remove(self) -> None:
        """Stop it. A screensaver still running after `stop`'s timeout is an error, not a warning."""
        self.stop()
