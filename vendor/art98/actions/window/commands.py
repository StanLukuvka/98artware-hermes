"""Window commands: minimize, maximize, restore, close, and a move in one frame."""

from __future__ import annotations

from ...base.animation import Animation, Instant
from ...base.vectors import ORIGIN
from ...errors import GuestError, UsageError
from ...guest import win32
from ...guest.bridge import wait_for
from ..pointer import CLICK_HOLD, PointerAction
from .win import Win


class Minimize(Instant):
    """Send a window to the taskbar."""

    def act(self) -> None:
        self.target.minimize()


class Maximize(Instant):
    """Fill the desktop with a window."""

    def act(self) -> None:
        self.target.maximize()


class RestoreWindow(Instant):
    """Bring a minimized or maximized window back."""

    def act(self) -> None:
        self.target.restore()


class Close(PointerAction):
    """End a window's program, and take the prop off stage. The only way a window ends.

    A window that goes any other way (the X button, a menu's Exit, a dialog's
    button) makes the next tracking check raise ImplicitClose.

    Without `via` it takes one frame and leaves the pointer alone. With `via`
    (a spot such as `window.corner(...)`, or a control such as
    `box.button("Yes")`) the pointer glides there and the press is shown, then
    the program is ended on the release. No mouse button event reaches the guest:
    a control is drawn pressed with BM_SETSTATE, and a dialog's chosen button
    gets BM_CLICK, so the box returns that answer. A Notepad closes without its
    save prompt.
    """

    run_time = 0.65
    travel = 0.74

    def __init__(self, target, via=None, **kwargs):
        if not isinstance(target, Win):
            raise UsageError(f"Close needs a window, not {type(target).__name__}")
        super().__init__(target, **kwargs)
        self.via = via
        if via is None:
            run_time = kwargs.get("run_time")
            self.run_time = 0.0 if run_time is None else run_time
        # A screensaver is ended by moving the pointer.
        pointer = via is not None or type(target).__name__ == "Screensaver"
        self.claims = ("pointer",) if pointer else ()
        self._ended = False

    def aim(self):
        return self.via

    def begin(self, scene) -> None:
        if self.target not in scene.props:
            raise UsageError(f"can't close {self.target._name()}: it is not on stage")
        self._ended = False
        if self.via is None:
            Animation.begin(self, scene)
        else:
            super().begin(scene)
        self.target.closing = True

    def schedule(self) -> list:
        return [
            (self.travel, self._press),
            (self.travel + self.at_seconds(CLICK_HOLD), self._end),
        ]

    def update(self, progress: float) -> None:
        if self.via is not None:
            super().update(progress)

    def _press(self) -> None:
        self._set_button(1)

    def _set_button(self, pressed: int) -> None:
        if getattr(self.via, "hwnd", None) is not None:
            self.scene.guest.user32.SendMessageA(
                self.via.hwnd, win32.BM_SETSTATE, pressed, 0
            )

    def finish(self, scene) -> None:
        if not self._ended:
            self._end()

    def abort(self, scene) -> None:
        """The play raised: the window is not closing after all, and the button is let go."""
        if not self._ended:  # `_end` clears both itself
            self.target.closing = False
            self._set_button(0)

    def _end(self) -> None:
        """Run the prop's own ending, wait for the window to go, and drop the prop."""
        from ..programs.dialog import (
            Dialog,
        )  # not at the top: Dialog imports this package

        self._ended = True
        scene, window = self.scene, self.target
        try:
            if isinstance(window, Dialog) and getattr(self.via, "hwnd", None):
                window.remove(button=self.via)
            else:
                window.remove()
            if window._identity is not None and not wait_for(
                lambda: self._gone() or None, win32.CLOSE_TIMEOUT, win32.CLOSE_POLL
            ):
                raise GuestError(f"{window._name()} didn't close" + scene.stray_note())
        finally:
            window.closing = False
            self._set_button(0)
        if window in scene.props:
            scene.props.remove(window)

    def _gone(self) -> bool:
        rows = {w.hwnd: w for w in self.scene.guest.windows()}
        return self.target.is_gone(rows)


class Teleport(Instant):
    """Put a window somewhere else in one frame, with no movement in between.

    `to` and `aligned_edge` work as in `move_to`, so by default the window's
    centre lands on `to`. Give `size` to change its outer size as well.
    """

    def __init__(
        self,
        target,
        to=None,
        *,
        aligned_edge=ORIGIN,
        size: tuple[int, int] | None = None,
        **kwargs,
    ):
        if to is None and size is None:
            raise UsageError("Teleport needs somewhere to go, a size, or both")
        super().__init__(target, **kwargs)
        self.claims = target.claims
        self.to, self.aligned_edge, self.new_size = to, aligned_edge, size

    def act(self) -> None:
        self.target.refresh()
        if self.new_size is not None:
            self.target.resize(*self.new_size)
        if self.to is not None:
            self.target.move_to(self.to, aligned_edge=self.aligned_edge)
        self.target.apply()  # `finish` settles it
