"""Window commands: minimize, maximize, restore, close, and a move in one frame."""

from __future__ import annotations

from ...base.animation import Instant
from ...base.vectors import ORIGIN
from ...win32 import (  # noqa: F401
    SC_MAXIMIZE,
    SC_MINIMIZE,
    SC_RESTORE,
    WM_SYSCOMMAND,
    sys_command,
)


class _SysCommand(Instant):
    command = 0
    maximized: bool | None = (
        None  # what the command does to Win._maximized, if anything
    )

    def act(self) -> None:
        sys_command(self.target.guest, self.target.hwnd, self.command)
        self.target.guest.sync(self.target.hwnd)
        if self.maximized is not None:
            self.target._maximized = self.maximized
        self.target.refresh()  # Windows moved and sized the window itself


class Minimize(_SysCommand):
    """Send a window to the taskbar."""

    command = SC_MINIMIZE


class Maximize(_SysCommand):
    """Fill the desktop with a window."""

    command = SC_MAXIMIZE
    maximized = True


class RestoreWindow(_SysCommand):
    """Bring a minimized or maximized window back."""

    command = SC_RESTORE
    maximized = False


class Close(Instant):
    """Close a window. It leaves the scene too."""

    def act(self) -> None:
        self.scene.remove(self.target)


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
            raise ValueError("Teleport needs somewhere to go, a size, or both")
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
