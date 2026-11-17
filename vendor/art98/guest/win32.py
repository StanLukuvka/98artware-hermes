"""Win32 constants and small named helpers for calls the guest makes often.

Raw `guest.user32.X(...)` stays fine anywhere; these only name the repeated ones.
"""

from __future__ import annotations

import struct
from contextlib import contextmanager

from ..base.space import DesktopSpace, Metrics
from ..errors import GuestError, UsageError
from .bridge import BridgeError
from .protocol import Out

# messages / commands
WM_SETTEXT, WM_GETTEXT, WM_GETTEXTLENGTH, WM_CLOSE = 0x000C, 0x000D, 0x000E, 0x0010
WM_SYSCOMMAND = 0x0112
WM_CHAR = 0x0102
BM_CLICK = 0x00F5  # a button's press-and-release, as its own click would send
BM_SETSTATE = 0x00F3  # wParam 1 draws a button pressed, 0 draws it up again
VK_BACK = 8
SC_MINIMIZE, SC_MAXIMIZE, SC_RESTORE = 0xF020, 0xF030, 0xF120
# ShowWindow
SW_HIDE, SW_SHOWNA = 0, 8
SW_SHOWMINNOACTIVE, SW_MAXIMIZE = 7, 3
# SetWindowPos
HWND_TOP = 0
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
SPI_SETDESKWALLPAPER = 0x14
# interface effects: (get, set) pairs, each set taking its on/off value in pvParam
SPI_GETMENUANIMATION, SPI_SETMENUANIMATION = 0x1002, 0x1003
SPI_GETCOMBOBOXANIMATION, SPI_SETCOMBOBOXANIMATION = 0x1004, 0x1005
SPI_GETLISTBOXSMOOTHSCROLLING, SPI_SETLISTBOXSMOOTHSCROLLING = 0x1006, 0x1007
SPI_GETGRADIENTCAPTIONS, SPI_SETGRADIENTCAPTIONS = 0x1008, 0x1009
SPI_GETHOTTRACKING, SPI_SETHOTTRACKING = 0x100E, 0x100F
SPIF_UPDATEINIFILE, SPIF_SENDWININICHANGE = 1, 2
# the system pointer shapes SetSystemCursor replaces
OCR_NORMAL, OCR_IBEAM, OCR_WAIT, OCR_CROSS, OCR_UP = 32512, 32513, 32514, 32515, 32516
OCR_SIZENWSE, OCR_SIZENESW, OCR_SIZEWE, OCR_SIZENS = 32642, 32643, 32644, 32645
OCR_SIZEALL, OCR_NO, OCR_HAND, OCR_APPSTARTING = 32646, 32648, 32649, 32650
COLOR_DESKTOP = 1  # GetSysColor / SetSysColors
BITSPIXEL = 12  # GetDeviceCaps
# controls
EM_SETMODIFY = 0x00B9
EM_GETSEL = 0x00B0  # get Edit selection: start in the low word, end in the high word
EM_SETSEL = 0x00B1  # set Edit selection: wParam start, lParam end
STM_SETICON, SS_ICON, SS_TYPEMASK = (
    0x0170,
    3,
    0x1F,
)  # SS_TYPEMASK: the bits of a static's type
DIALOG_CLASS = "#32770"  # the window class of every message box and common dialog
MENU_CLASS = "#32768"  # the window class of a popup menu
TOOLTIP_CLASS = "tooltips_class32"  # the window class of a tooltip
# registry
HKCU, HKLM = 0x80000001, 0x80000002
KEY_SET_VALUE = 2
REG_SZ, REG_BINARY = 1, 3
# BITMAPINFOHEADER biCompression
BI_RGB, BI_RLE8, BI_RLE4, BI_BITFIELDS, BI_JPEG, BI_PNG = 0, 1, 2, 3, 4, 5
# files, drives, shutdown
DRIVE_CDROM = 5
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
EWX_REBOOT, EWX_FORCE = 2, 4
# mouse_event flags: (down, up)
MOUSE_BUTTONS = {
    "left": (0x0002, 0x0004),
    "right": (0x0008, 0x0010),
    "middle": (0x0020, 0x0040),
}
MF_BYPOSITION = 0x0400
# display modes: EnumDisplaySettingsA / ChangeDisplaySettingsA
ENUM_SETTINGS_END_OF_LIST = 0
CDS_UPDATEREGISTRY = 1
DISPLAY_SIZES = ((640, 480), (800, 600), (1024, 768), (1280, 1024))  # the Cirrus modes
DEVMODE_SIZE = 156
DM_BITSPERPEL, DM_PELSWIDTH, DM_PELSHEIGHT = 0x00040000, 0x00080000, 0x00100000


def colourref(rgb) -> int:
    """The Win32 COLORREF: red in the low byte."""
    return rgb[0] | rgb[1] << 8 | rgb[2] << 16


def rgb(colourref: int) -> tuple[int, int, int]:
    """The (r, g, b) in a Win32 COLORREF: the reverse of `colourref`."""
    return colourref & 255, colourref >> 8 & 255, colourref >> 16 & 255


@contextmanager
def screen_dc(guest):
    """The screen's device context, released on exit. Keep one open across a loop of reads."""
    dc = guest.user32.GetDC(0).value
    try:
        yield dc
    finally:
        guest.user32.ReleaseDC(0, dc)


def screen_pixel(guest, x: int, y: int) -> tuple[int, int, int]:
    """The (r, g, b) on screen at (x, y). One DC per call: use `screen_dc` for many reads."""
    with screen_dc(guest) as dc:
        return rgb(guest.gdi32.GetPixel(dc, x, y).value & 0xFFFFFF)


def window_rect(guest, hwnd: int) -> tuple[int, int, int, int] | None:
    """A window's (left, top, right, bottom) on the screen, or None if it has gone."""
    result = guest.user32.GetWindowRect(hwnd, Out(16))
    if not result:
        return None
    return struct.unpack("<iiii", result.buffers[0])


def normal_rect(guest, hwnd: int) -> tuple[int, int, int, int]:
    """A window's normal (restored) (left, top, right, bottom).

    Windows keeps it while the window is maximized or minimized. Measured on the
    art98 image, it equals GetWindowRect for a normal window.
    """
    # WINDOWPLACEMENT.length must be 44: a zeroed buffer makes the call fail.
    result = guest.user32.GetWindowPlacement(hwnd, struct.pack("<I", 44) + bytes(40))
    if result.value == 0:
        raise GuestError(f"GetWindowPlacement failed for window {hwnd}")
    return tuple(struct.unpack("<11i", result.buffers[0])[7:])


def window_style(guest, hwnd: int) -> int:
    """A window's WS_* and control style bits (GWL_STYLE, read as unsigned)."""
    return guest.user32.GetWindowLongA(hwnd, GWL_STYLE & 0xFFFFFFFF).value


def client_size(guest, hwnd: int) -> tuple[int, int]:
    """A window's client area size: width and height."""
    rect = guest.user32.GetClientRect(hwnd, Out(16)).buffers[0]
    return struct.unpack("<iiii", rect)[2:]


def client_to_screen(guest, hwnd: int, x: int, y: int) -> tuple[int, int]:
    """A point in a window's client coordinates, as screen coordinates."""
    point = struct.pack("<ii", x, y)
    return struct.unpack("<ii", guest.user32.ClientToScreen(hwnd, point).buffers[0])


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


def set_display_mode(
    guest, width: int, height: int, depth: int, persist: bool = True
) -> None:
    """Switch resolution, using a mode the video driver itself reports.

    `persist` also writes the mode to the registry. Raises `BridgeError` if the
    driver has no such mode or refuses the change.
    """
    mode = 0
    while True:
        blank = bytearray(DEVMODE_SIZE)
        struct.pack_into("<H", blank, 36, DEVMODE_SIZE)
        result = guest.user32.EnumDisplaySettingsA(None, mode, blank)
        if result.value == ENUM_SETTINGS_END_OF_LIST:
            raise BridgeError(f"guest has no {width}x{height}x{depth} display mode")
        devmode = bytearray(result.buffers[0])
        bpp, w, h = struct.unpack_from("<III", devmode, 104)
        if (w, h, bpp) == (width, height, depth):
            break
        mode += 1
    struct.pack_into("<I", devmode, 40, DM_BITSPERPEL | DM_PELSWIDTH | DM_PELSHEIGHT)
    flags = CDS_UPDATEREGISTRY if persist else 0
    result = guest.user32.ChangeDisplaySettingsA(devmode, flags)
    if result.signed < 0:
        raise BridgeError(f"ChangeDisplaySettings failed with {result.signed}")


def sys_command(guest, hwnd: int, command: int) -> None:
    guest.user32.PostMessageA(hwnd, WM_SYSCOMMAND, command, 0)


CLOSE_TIMEOUT, CLOSE_POLL = 5.0, 0.05  # seconds for a window to go once it is told to


def close_window(guest, hwnd: int) -> None:
    guest.user32.PostMessageA(hwnd, WM_CLOSE, 0, 0)


def show_window(guest, hwnd: int, command: int) -> None:
    guest.user32.ShowWindow(hwnd, command)


def show_state(guest, hwnd: int) -> str:
    """ "minimized", "maximized" or "normal"."""
    if guest.user32.IsIconic(hwnd).value:
        return "minimized"
    if guest.user32.IsZoomed(hwnd).value:
        return "maximized"
    return "normal"


def check_mouse_button(button: str) -> None:
    """Raise UsageError unless `button` is one of MOUSE_BUTTONS."""
    if button not in MOUSE_BUTTONS:
        raise UsageError(f"no mouse button {button!r}; use left, right or middle")


def mouse_button(guest, button: str, down: bool) -> None:
    check_mouse_button(button)
    guest.user32.mouse_event(MOUSE_BUTTONS[button][0 if down else 1], 0, 0, 0, 0)


def menu_item_rect(
    guest, hwnd: int, menu: int, position: int
) -> tuple[int, int, int, int] | None:
    result = guest.user32.GetMenuItemRect(hwnd, menu, position, Out(16))
    return struct.unpack("<iiii", result.buffers[0]) if result else None
