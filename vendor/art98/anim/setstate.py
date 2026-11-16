"""The guest side of a Set: applying a state, and checking the guest matches it.

A Set is revert, apply, check. `Scene.set_state` runs it: it finds and loads the
file, calls `Session.revert`, then `apply_state` here, then `check_state`. Apply
opens the state's windows back to front, so each new one lands on top, and
places the pointer last. Check reads back what the state describes and fails on
any difference. Anything the state leaves out isn't checked.
"""

from __future__ import annotations

from ..actions.apps import APPS
from ..base.units import clamp
from ..desktop import set_colour, set_wallpaper
from ..errors import GuestError
from ..guest.disc import wait_for_disc
from ..guest.guestfiles import check_bmp, read_bmp_header
from ..guest.win32 import (
    BITSPIXEL,
    COLOR_DESKTOP,
    SW_MAXIMIZE,
    SW_SHOWMINNOACTIVE,
    cursor_pos,
    repaint_window,
    rgb,
    screen_dc,
    screen_pixel,
    screen_size,
    set_display_mode,
    show_state,
    show_window,
)
from ..state import State, problem_text

DISC_NAME = "WALL0001.BMP"  # the one file name on the wallpaper disc
# Per channel, by screen depth in bits: a 16 bpp screen rounds 128 to 120 and 255 to
# 248, while 24 and 32 bpp keep each channel but for a little rounding. Any other
# depth (8 bpp is a palette) isn't compared.
# lazy: only 16, 24 and 32 bpp have a tolerance. Upgrade path: for a palette depth,
# look the expected colour up in the system palette and compare that.
PIXEL_TOLERANCE = {16: 12, 24: 2, 32: 2}
# The wallpaper sample sits at least this many pixels above `bottom`, the work area's
# bottom edge, so it is clear of the taskbar. Not a colour tolerance.
TASKBAR_MARGIN = 12
# The Win98 pointer is 32x32 pixels, with its hot spot near the top left corner.
CURSOR_SIZE = 32


class StateMismatch(GuestError):
    """The guest doesn't look like the state after applying it."""

    def __init__(self, path, problems: list[str]):
        self.path, self.problems = path, problems
        super().__init__(problem_text(path, problems))


def display_mode(guest) -> tuple[int, int, int]:
    width, height = screen_size(guest)
    with screen_dc(guest) as dc:
        depth = guest.gdi32.GetDeviceCaps(dc, BITSPIXEL).value
    return width, height, depth


def apply_state(scene, state: State) -> dict:
    """Make the reverted desktop match the state. Returns its props by window id."""
    guest = scene.guest
    if state.display and display_mode(guest) != state.display:
        set_display_mode(guest, *state.display, persist=False)
        scene.prepare_desktop()
    desk = state.desktop
    if desk and desk.fill_colour:
        set_colour(guest, desk.fill_colour)
    if desk and desk.wallpaper:
        scene.session.attach_disc({DISC_NAME: desk.wallpaper.data})
        set_wallpaper(guest, wait_for_disc(guest, DISC_NAME), desk.style)
    props = {}
    for window in state.windows:
        prop = APPS[window.app].from_state(window)
        scene.add(prop)
        props[window.id] = prop
        # Maximizing activates the window, so it's done before the next one
        # opens on top of it, and the order stays back to front.
        if window.show != "normal":
            command = SW_SHOWMINNOACTIVE if window.show == "minimized" else SW_MAXIMIZE
            show_window(guest, prop.hwnd, command)
            prop.refresh()
    # A window moving off another can leave desktop pixels on its frame, so
    # every window is repainted, back to front (design doc: "Stray pixels").
    for window in state.windows:
        repaint_window(guest, props[window.id].hwnd)
    if state.pointer:
        scene.pointer.place(state.pointer)
    guest.sync()
    return props


def bmp_pixel(data: bytes, x: int, y: int):
    """The (r, g, b) at column x, row y (from the top) of an uncompressed 24 or 8 bpp BMP, else None.

    None too for a point outside the image.

    lazy: RLE (compression != 0), OS/2 12-byte headers and other depths aren't decoded,
    so check_state skips the pixel check for them. Upgrade path: decode those here.
    """
    header = read_bmp_header(data)
    height = abs(header.height)
    if not (0 <= x < header.width and 0 <= y < height):
        return None
    if header.palette < 14 + 40 or header.compression != 0:
        return None
    row = (header.width * header.depth + 31) // 32 * 4
    line = height - 1 - y if header.height > 0 else y
    at = header.pixels + line * row
    try:
        if header.depth == 24:
            b, g, r = data[at + x * 3 :][:3]
        elif header.depth == 8:
            b, g, r = data[header.palette + data[at + x] * 4 :][:3]
        else:
            return None
    except (IndexError, ValueError):  # a short palette or pixel area
        return None
    return r, g, b


def wallpaper_sample(state: State, width: int, height: int, work_bottom: int):
    """A screen point no state window or the pointer covers, or None if they cover every candidate.

    `work_bottom` is the work area's bottom edge. The pointer is placed last, so
    the box it fills from `state.pointer` counts as covered.
    """
    shown = [w for w in state.windows if w.show != "minimized"]
    for x, y in (
        (width // 2, height // 2),
        (width // 4, height // 4),
        (3 * width // 4, height // 4),
        (width // 4, 3 * height // 4),
    ):
        y = min(y, work_bottom - TASKBAR_MARGIN)
        if state.pointer and (
            state.pointer[0] <= x < state.pointer[0] + CURSOR_SIZE
            and state.pointer[1] <= y < state.pointer[1] + CURSOR_SIZE
        ):
            continue
        if not any(
            w.show == "maximized"
            or (
                w.at[0] <= x < w.at[0] + w.size[0]
                and w.at[1] <= y < w.at[1] + w.size[1]
            )
            for w in shown
        ):
            return x, y
    return None


def expected_pixels(
    desk, screen: tuple[int, int], colour: tuple[int, int, int], x: int, y: int
):
    """The colours a wallpaper in `desk.style` may put at screen (x, y), best guess first, or None if it can't be told.

    Tile is exact. Stretch and center guess which BMP pixel lands on the point, and
    Windows may round differently, so the 3x3 pixels around the guess all count. A
    center point on or next to the image's edge may also show the fill colour.
    """
    data = desk.wallpaper.data
    width, height = check_bmp(data)
    if desk.style == "tile":
        guess = bmp_pixel(data, x % width, y % height)
        return None if guess is None else [guess]
    if desk.style == "stretch":
        gx, gy = x * width // screen[0], y * height // screen[1]
    else:
        gx, gy = x - (screen[0] - width) // 2, y - (screen[1] - height) // 2
    if desk.style == "center" and not (-1 <= gx <= width and -1 <= gy <= height):
        return [colour]
    wanted = []
    if desk.style == "center" and not (0 <= gx < width and 0 <= gy < height):
        wanted.append(colour)
    points = [(gx, gy)] + [
        (gx + dx, gy + dy) for dy in (-1, 0, 1) for dx in (-1, 0, 1) if dx or dy
    ]
    for px, py in points:
        if desk.style == "stretch":
            px, py = clamp(px, 0, width - 1), clamp(py, 0, height - 1)
        elif not (0 <= px < width and 0 <= py < height):
            if colour not in wanted:
                wanted.append(colour)
            continue
        pixel = bmp_pixel(data, px, py)
        if pixel is None:
            return None
        if pixel not in wanted:
            wanted.append(pixel)
    return wanted


def check_state(guest, space, state: State, props: dict) -> None:
    """Raise StateMismatch listing every way the guest differs from the state."""
    problems = display_problems(guest, state)
    problems += colour_problems(guest, state)
    desk = state.desktop
    if desk and desk.wallpaper:
        problem = wallpaper_problem(guest, state, desk, space.work_area[3])
        if problem:
            problems.append(problem)
    found = {w.hwnd: w for w in guest.windows()}
    problems += window_problems(guest, state, props, found)
    problems += stacking_problems(state, props, found)
    problems += pointer_problems(guest, state)
    if problems:
        raise StateMismatch(state.path, problems)


def display_problems(guest, state: State) -> list[str]:
    if not state.display:
        return []
    actual = display_mode(guest)
    if actual == state.display:
        return []
    return [f"display: {list(state.display)} wanted, the guest has {list(actual)}"]


def colour_problems(guest, state: State) -> list[str]:
    desk = state.desktop
    if not (desk and desk.fill_colour):
        return []
    actual = rgb(guest.user32.GetSysColor(COLOR_DESKTOP).value & 0xFFFFFF)
    if actual == tuple(desk.fill_colour):
        return []
    return [
        f"desktop.fill_colour: {list(desk.fill_colour)} wanted, the guest has {list(actual)}"
    ]


def window_problems(guest, state: State, props: dict, found: dict) -> list[str]:
    """Each state window's presence, title, show state and, if normal, position and size."""
    problems = []
    for index, window in enumerate(state.windows):
        where = f"windows[{index}] {window.id!r}"
        real = found.get(props[window.id].hwnd)
        if real is None or not real.visible:
            problems.append(f"{where}: the window isn't there")
            continue
        if real.title != window.title:
            problems.append(
                f"{where}: title {window.title!r} wanted, the guest has {real.title!r}"
            )
        show = show_state(guest, real.hwnd)
        if show != window.show:
            problems.append(f"{where}: {window.show} wanted, the window is {show}")
        elif show == "normal":
            left, top, right, bottom = real.rect
            if (left, top) != window.at:
                problems.append(
                    f"{where}: at {list(window.at)} wanted, the guest has {[left, top]}"
                )
            if (right - left, bottom - top) != window.size:
                problems.append(
                    f"{where}: size {list(window.size)} wanted, the guest has "
                    f"{[right - left, bottom - top]}"
                )
    return problems


def stacking_problems(state: State, props: dict, found: dict) -> list[str]:
    """Top-level windows come back front to back, and the state lists them
    back to front. Windows 98 sends a minimized window to the bottom, so only
    the others are compared."""
    stacked = [w for w in reversed(state.windows) if w.show != "minimized"]
    wanted = [props[w.id].hwnd for w in stacked]
    order = [hwnd for hwnd in found if hwnd in wanted]
    if len(order) != len(wanted) or order == wanted:
        return []
    ids = {props[w.id].hwnd: w.id for w in stacked}
    text = (
        f"windows: front to back should be {[ids[h] for h in wanted]}, "
        f"the guest has {[ids[h] for h in order]}"
    )
    return [text]


def pointer_problems(guest, state: State) -> list[str]:
    if not state.pointer:
        return []
    actual = cursor_pos(guest)
    if actual == state.pointer:
        return []
    return [f"pointer: {list(state.pointer)} wanted, the guest has {list(actual)}"]


def wallpaper_problem(guest, state: State, desk, work_bottom: int) -> str | None:
    """The wallpaper can't be read back (SPI_GETDESKWALLPAPER gives nothing), so one pixel is compared."""
    width, height, depth = display_mode(guest)
    tolerance = PIXEL_TOLERANCE.get(depth)
    if tolerance is None:
        return None
    screen = (width, height)
    point = wallpaper_sample(state, *screen, work_bottom)
    if point is None:
        return None
    colour = guest.user32.GetSysColor(COLOR_DESKTOP).value & 0xFFFFFF
    wanted = expected_pixels(desk, screen, rgb(colour), *point)
    if wanted is None:
        return None
    got = screen_pixel(guest, *point)
    if not any(
        all(abs(g - w) <= tolerance for g, w in zip(got, want)) for want in wanted
    ):
        return f"desktop.wallpaper: pixel {list(point)} should be about {list(wanted[0])}, the guest has {list(got)}"
    return None
