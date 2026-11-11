"""Win32 constants and small named helpers for calls the guest makes often.

Raw `guest.user32.X(...)` stays fine anywhere; these only name the repeated ones.
"""

from __future__ import annotations

import struct

from .base.space import DesktopSpace, Metrics
from .protocol import Out

# messages / commands
WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH, WM_CLOSE = 0x000C, 0x000D, 0x000E, 0x0010
WM_SYSCOMMAND = 0x0112
SC_MINIMIZE, SC_MAXIMIZE, SC_RESTORE = 0xF020, 0xF030, 0xF120
# ShowWindow
SW_HIDE = 0
SW_SHOWMINNOACTIVE, SW_MAXIMIZE = 7, 3
# SetWindowPos
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE = 0x0001, 0x0002, 0x0004, 0x0010
# RedrawWindow
RDW_INVALIDATE, RDW_ERASE, RDW_ALLCHILDREN, RDW_UPDATENOW, RDW_FRAME = (
    1,
    4,
    0x80,
    0x100,
    0x400,
)
RDW_REPAINT_ALL = (
    RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW | RDW_FRAME
)
# metrics / system parameters
SM_CXSCREEN, SM_CYSCREEN = 0, 1
SM_CYCAPTION, SM_CYSIZEFRAME = 4, 33
SM_CXMINTRACK, SM_CYMINTRACK = 34, 35
SM_CXMAXTRACK, SM_CYMAXTRACK = 59, 60
# window styles (GetWindowLongA: the bridge sends names as they are exported)
GWL_STYLE, WS_THICKFRAME = -16, 0x00040000
SPI_SETWORKAREA, SPI_GETWORKAREA = 0x2F, 0x30
# mouse_event flags: (down, up)
MOUSE_BUTTONS = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
MF_BYPOSITION = 0x0400


def window_rect(guest, hwnd: int) -> tuple[int, int, int, int] | None:
    """A window's (left, top, right, bottom) on the screen, or None if it has gone."""
    result = guest.user32.GetWindowRect(hwnd, Out(16))
    if not result:
        return None
    return struct.unpack("<iiii", result.buffers[0])


def cursor_pos(guest) -> tuple[int, int]:
    """Where the guest's pointer is."""
    return struct.unpack("<ii", guest.user32.GetCursorPos(Out(8)).buffers[0])


def screen_size(guest) -> tuple[int, int]:
    user32 = guest.user32
    return user32.GetSystemMetrics(SM_CXSCREEN).value, user32.GetSystemMetrics(
        SM_CYSCREEN
    ).value


def work_area(guest) -> tuple[int, int, int, int]:
    """The desktop area as (left, top, right, bottom): above the taskbar, or the
    whole screen on a blank desktop."""
    return struct.unpack(
        "<4i",
        guest.user32.SystemParametersInfoA(SPI_GETWORKAREA, 0, Out(16), 0).buffers[0],
    )


def calibrate(guest, strict: bool = False) -> DesktopSpace:
    """Read the desktop once: 8 GetSystemMetrics and the work area, 9 calls."""
    gm = guest.user32.GetSystemMetrics

    def read(*indexes: int) -> tuple[int, ...]:
        return tuple(gm(index).value for index in indexes)

    metrics = Metrics(
        *read(SM_CYCAPTION, SM_CYSIZEFRAME),
        read(SM_CXMINTRACK, SM_CYMINTRACK),
        read(SM_CXMAXTRACK, SM_CYMAXTRACK),
    )
    return DesktopSpace(screen_size(guest), work_area(guest), metrics, strict)


def place_window(
    guest, hwnd: int, x: int, y: int, width: int, height: int, flags: int
) -> None:
    """Move and size a window, then repaint it: moves leave stale pixels on the frame."""
    guest.user32.SetWindowPos(hwnd, 0, x, y, width, height, flags)
    repaint_window(guest, hwnd)


def repaint_window(guest, hwnd: int) -> None:
    guest.user32.RedrawWindow(hwnd, 0, 0, RDW_REPAINT_ALL)


def repaint_all(guest) -> None:
    """Repaint every visible top-level window, then wait for the guest to finish.

    Back to front: repainting a window's frame on Windows 98 draws its caption
    text over a window in front of it, so the front window goes last and has
    the final say. `sync` alone only waits for what is already invalid; pixels
    that were painted in the wrong order are not, so only this clears them.
    """
    for window in reversed(guest.windows()):  # the guest lists them front to back
        if window.visible:
            repaint_window(guest, window.hwnd)
    guest.sync()


def sys_command(guest, hwnd: int, command: int) -> None:
    guest.user32.PostMessageA(hwnd, WM_SYSCOMMAND, command, 0)


def close_window(guest, hwnd: int) -> None:
    guest.user32.PostMessageA(hwnd, WM_CLOSE, 0, 0)


def show_window(guest, hwnd: int, command: int) -> None:
    guest.user32.ShowWindow(hwnd, command)


def mouse_button(guest, button: str, down: bool) -> None:
    guest.user32.mouse_event(MOUSE_BUTTONS[button][0 if down else 1], 0, 0, 0, 0)


def menu_item_rect(
    guest, hwnd: int, menu: int, position: int
) -> tuple[int, int, int, int] | None:
    result = guest.user32.GetMenuItemRect(hwnd, menu, position, Out(16))
    return struct.unpack("<iiii", result.buffers[0]) if result else None
