"""The blank start: a black, empty screen on the restored desktop.

The `ready` snapshot is the stock Windows 98 desktop: a teal background,
icons, the taskbar and the arrow pointer. `blank` turns all of that off in the
running guest. Each step only changes memory, so it holds until the next
revert and the snapshot stays as it is. None of it needs Explorer restarted.

The steps, each tested on the real image (docs/design/blank-start.md):

- the desktop colour goes black, which shows because there's no wallpaper;
- the icons are hidden. Explorer shows them again on a desktop refresh, so
  scenes shouldn't send one (F5 on the desktop, or command 0x7103);
- the taskbar is hidden, along with the tray and Quick Launch, and disabled,
  so the Windows key and Ctrl+Esc no longer open the Start menu. It stays
  hidden through a settings change and the mouse at the bottom edge;
- the desktop window is disabled, so a right-click on the black opens no menu;
- the work area becomes the whole screen, so a maximized window covers it;
- the menu, combo box and list box animations, gradient captions and hot
  tracking are turned off, since their timing comes from the guest's clock;
- every system pointer shape is swapped for an empty one, unless the pointer
  is wanted. ShowCursor(FALSE) doesn't hide the pointer on Windows 98.

The pointer is hidden or not once per desktop, right after a revert. Turning
it on and off later isn't reliable: the first swap of the shapes shows at
once, but after that a new shape only shows once the pointer has moved to a
new place and stayed there. Moving it away and back doesn't help, even with a
pause. Tested on the `ready` image, November 2026.
"""

from __future__ import annotations

import struct
import time

from .protocol import Out
from .win32 import SPI_SETWORKAREA, SW_HIDE, repaint_all, screen_size, work_area

COLOR_DESKTOP = 1
BLACK = 0x000000
NAMED_COLOURS = {
    "teal": (0, 128, 128),
    "dark_red": (128, 0, 0),
    "red": (255, 0, 0),
    "white": (255, 255, 255),
    "black": (0, 0, 0),
}

HKCU, KEY_SET_VALUE, REG_SZ = 0x80000001, 2, 1
SPI_SETDESKWALLPAPER = 0x14
SPIF_UPDATEINIFILE, SPIF_SENDWININICHANGE = 1, 2
# (TileWallpaper, WallpaperStyle). The one place the styles are defined.
STYLES = {"center": ("0", "0"), "tile": ("1", "0"), "stretch": ("0", "2")}
DRIVE_CDROM, INVALID_FILE_ATTRIBUTES = 5, 0xFFFFFFFF

# The interface effects as (name, get action, set action). Each set action
# takes its on/off value in pvParam, so 0 turns it off.
UI_EFFECTS = (
    ("menu animation", 0x1002, 0x1003),
    ("combo box animation", 0x1004, 0x1005),
    ("list box smooth scrolling", 0x1006, 0x1007),
    ("gradient captions", 0x1008, 0x1009),
    ("hot tracking", 0x100E, 0x100F),
)

# Every pointer shape Windows 98 draws by itself: arrow, I-beam, wait, cross,
# up arrow, the four resize arrows, move, no, hand and arrow-with-hourglass.
POINTER_SHAPES = (
    32512,
    32513,
    32514,
    32515,
    32516,
    32642,
    32643,
    32644,
    32645,
    32646,
    32648,
    32649,
    32650,
)
POINTER_SIZE = 32


class DesktopError(RuntimeError):
    """The guest didn't end up blank."""

    def __init__(self, problems: list[str], lead: str = "the desktop isn't blank:"):
        self.problems = problems
        super().__init__(lead + "\n" + "\n".join(f"  {p}" for p in problems))


def shell_windows(guest) -> tuple[int, int, int]:
    """The taskbar, the desktop and its icon list, as window handles (0 if missing)."""
    user32 = guest.user32
    taskbar = user32.FindWindowA("Shell_TrayWnd", 0).value
    desktop = user32.FindWindowA("Progman", 0).value
    view = (
        user32.FindWindowExA(desktop, 0, "SHELLDLL_DefView", 0).value if desktop else 0
    )
    icons = user32.FindWindowExA(view, 0, "SysListView32", 0).value if view else 0
    return taskbar, desktop, icons


def resolve_colour(colour) -> tuple[int, int, int]:
    """A colour name from NAMED_COLOURS or an (r, g, b) of ints 0..255, as a tuple. Raises ValueError."""
    if isinstance(colour, str):
        if colour not in NAMED_COLOURS:
            raise ValueError(
                f"unknown colour {colour!r}; use one of {sorted(NAMED_COLOURS)} or (r, g, b)"
            )
        return NAMED_COLOURS[colour]
    try:
        rgb = tuple(colour)
    except TypeError:
        raise ValueError(
            f"colour must be a name or (r, g, b), not {colour!r}"
        ) from None
    if len(rgb) != 3 or not all(type(c) is int and 0 <= c <= 255 for c in rgb):
        raise ValueError(f"colour must be three ints from 0 to 255, not {colour!r}")
    return rgb


def colourref(rgb) -> int:
    """The Win32 COLORREF: red in the low byte."""
    return rgb[0] | rgb[1] << 8 | rgb[2] << 16


def set_colour(guest, rgb) -> None:
    """Set the desktop colour. On a blank desktop the change shows the icons again, so hide them again.

    The broadcast makes every window repaint itself on its own thread, in no
    set order, and a frame repainted late can draw over the window in front of
    it (or leave pixels behind). So once every thread has taken the message,
    the whole screen is repainted back to front: the frame comes out as a
    fresh paint would draw it, on every run.
    """
    user32 = guest.user32
    icons = shell_windows(guest)[2]
    was_hidden = bool(icons) and not user32.IsWindowVisible(icons).value
    user32.SetSysColors(
        1, struct.pack("<I", COLOR_DESKTOP), struct.pack("<I", colourref(rgb))
    )
    if was_hidden:
        user32.ShowWindow(shell_windows(guest)[2], SW_HIDE)
    guest.sync()  # every window's thread has handled WM_SYSCOLORCHANGE
    repaint_all(guest)


def set_wallpaper(guest, path: str, style: str) -> None:
    """Show the BMP at `path` on the desktop. Icons are not re-hidden: the call doesn't bring them back."""
    tile, mode = STYLES[style]
    key = guest.advapi32.RegOpenKeyExA(
        HKCU, r"Control Panel\Desktop", 0, KEY_SET_VALUE, Out(4)
    )
    hkey = struct.unpack("<I", key.buffers[0])[0]
    try:
        for name, value in (("TileWallpaper", tile), ("WallpaperStyle", mode)):
            data = value.encode() + b"\0"
            if (
                guest.advapi32.RegSetValueExA(
                    hkey, name, 0, REG_SZ, data, len(data)
                ).value
                != 0
            ):
                raise DesktopError(
                    [f"could not set {name} in the registry"], "the wallpaper failed:"
                )
    finally:
        guest.advapi32.RegCloseKey(hkey)
    guest.user32.SystemParametersInfoA(
        SPI_SETDESKWALLPAPER, 0, path, SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE
    )
    guest.sync()


def cd_drive(guest) -> str | None:
    """The CD drive as "D:\\", or None if the guest has none."""
    mask = guest.kernel32.GetLogicalDrives().value
    for i in range(26):
        letter = f"{chr(65 + i)}:\\"
        if mask >> i & 1 and guest.kernel32.GetDriveTypeA(letter).value == DRIVE_CDROM:
            return letter
    return None


def wait_for_disc(guest, name: str, timeout: float = 2.0) -> str:
    """Poll every 50 ms until `name` can be read on the CD; return its full path."""
    deadline = time.monotonic() + timeout
    while True:
        letter = cd_drive(guest)
        if (
            letter
            and guest.kernel32.GetFileAttributesA(letter + name).value
            != INVALID_FILE_ATTRIBUTES
        ):
            return letter + name
        if time.monotonic() >= deadline:
            raise DesktopError(
                [f"the CD with {name} did not show up in {timeout:g} s"],
                "the wallpaper disc failed:",
            )
        time.sleep(0.05)


def blank(guest, pointer: bool = False) -> None:
    """Turn the restored desktop into a black, empty screen, then check it.

    pointer=True keeps the pointer visible. Raises DesktopError listing
    anything that didn't take.
    """
    user32 = guest.user32
    taskbar, desk, icons = shell_windows(guest)
    set_colour(guest, (0, 0, 0))
    if icons:
        user32.ShowWindow(icons, SW_HIDE)
    if taskbar:
        user32.ShowWindow(taskbar, SW_HIDE)
        user32.EnableWindow(taskbar, 0)
    if desk:
        user32.EnableWindow(desk, 0)
    width, height = screen_size(guest)
    user32.SystemParametersInfoA(
        SPI_SETWORKAREA, 0, struct.pack("<4i", 0, 0, width, height), 0
    )
    for _name, _get, set_action in UI_EFFECTS:
        user32.SystemParametersInfoA(set_action, 0, 0, 0)
    if not pointer:
        hide_pointer(guest)
    guest.sync()
    problems = check_blank(guest)
    if problems:
        raise DesktopError(problems)


def check_blank(guest) -> list[str]:
    """Every way the guest differs from a blank desktop. The pointer isn't checked."""
    user32 = guest.user32
    problems = []
    taskbar, desk, icons = shell_windows(guest)
    if not icons:
        problems.append("the desktop icon list wasn't found")
    elif user32.IsWindowVisible(icons).value:
        problems.append("the desktop icons are showing")
    if not taskbar:
        problems.append("the taskbar wasn't found")
    elif user32.IsWindowVisible(taskbar).value:
        problems.append("the taskbar is showing")
    elif user32.IsWindowEnabled(taskbar).value:
        problems.append(
            "the taskbar takes input, so the Windows key opens the Start menu"
        )
    if desk and user32.IsWindowEnabled(desk).value:
        problems.append("the desktop takes input, so a right-click opens its menu")
    colour = user32.GetSysColor(COLOR_DESKTOP).value & 0xFFFFFF
    if colour != BLACK:
        problems.append(f"the desktop colour is {colour:06x}, not black")
    width, height = screen_size(guest)
    area = work_area(guest)
    if area != (0, 0, width, height):
        problems.append(
            f"the work area is {list(area)}, not the whole {width}x{height} screen"
        )
    for name, get_action, _set in UI_EFFECTS:
        result = user32.SystemParametersInfoA(get_action, 0, Out(4), 0)
        if struct.unpack("<I", result.buffers[0])[0]:
            problems.append(f"{name} is on")
    return problems


def hide_pointer(guest) -> None:
    """Swap every system pointer shape for an empty one.

    The pointer still moves and clicks; it just draws nothing. A program that
    sets its own pointer shape (Paint's tools, say) still shows that one. Call
    it once after a revert: see the module notes.
    """
    user32 = guest.user32
    mask = POINTER_SIZE * POINTER_SIZE // 8
    empty = user32.CreateCursor(
        0, 0, 0, POINTER_SIZE, POINTER_SIZE, b"\xff" * mask, b"\x00" * mask
    ).value
    try:
        for shape in POINTER_SHAPES:
            # SetSystemCursor takes over the handle it's given, so each gets a copy.
            user32.SetSystemCursor(user32.CopyIcon(empty).value, shape)
    finally:
        user32.DestroyCursor(empty)
