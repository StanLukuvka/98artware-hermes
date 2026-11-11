"""Applying a state to the guest, and checking the guest matches it.

Set is revert, apply, check. Revert is `Session.revert`. Apply opens the
state's windows back to front, so each new one lands on top, and places the
pointer last. Check reads back what the state describes and fails on any
difference. Anything the state leaves out isn't checked.
"""

from __future__ import annotations

import inspect
import struct
from pathlib import Path

from ..actions.pointer import Pointer
from ..actions.programs.notepad import Notepad
from ..actions.window.picture import Picture
from ..desktop import (
    COLOR_DESKTOP,
    blank,
    colourref,
    set_colour,
    set_wallpaper,
    wait_for_disc,
)
from ..guestfiles import check_bmp
from ..state import State, StateError, load_state
from ..win32 import (
    SW_MAXIMIZE,
    SW_SHOWMINNOACTIVE,
    cursor_pos,
    repaint_window,
    screen_size,
    show_window,
)

BITSPIXEL = 12
APPS = {"picture": Picture, "notepad": Notepad}
DISC_NAME = "WALL0001.BMP"  # the one file name on the wallpaper disc
PIXEL_TOLERANCE = 12  # per channel: a 16 bpp screen rounds 128 to 120 and 255 to 248


class StateMismatch(StateError):
    """The guest doesn't look like the state after applying it."""


def display_mode(guest) -> tuple[int, int, int]:
    width, height = screen_size(guest)
    dc = guest.user32.GetDC(0).value
    try:
        depth = guest.gdi32.GetDeviceCaps(dc, BITSPIXEL).value
    finally:
        guest.user32.ReleaseDC(0, dc)
    return width, height, depth


def apply_state(scene, state: State) -> dict:
    """Make the reverted desktop match the state. Returns its props by window id."""
    guest = scene.guest
    if state.display and display_mode(guest) != state.display:
        guest.set_display_mode(*state.display, persist=False)
        scene.prepare_desktop()
    desk = state.desktop
    if desk and desk.colour:
        set_colour(guest, desk.colour)
    if desk and desk.wallpaper:
        scene.session.attach_disc({DISC_NAME: desk.wallpaper.data})
        set_wallpaper(guest, wait_for_disc(guest, DISC_NAME), desk.style)
    props = {}
    for window in state.windows:
        prop = APPS[window.app].from_state(window)
        scene.add(prop)
        props[window.id] = prop
        prop._maximized = window.show == "maximized"
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

    lazy: RLE (compression != 0), OS/2 12-byte headers and other depths aren't decoded,
    so check_state skips the pixel check for them. Upgrade path: decode those here.
    """
    pixels, header = struct.unpack_from("<II", data, 10)
    if header < 40:
        return None
    width, height, _, depth, compression = struct.unpack_from("<iiHHI", data, 18)
    if compression != 0:
        return None
    row = (width * depth + 31) // 32 * 4
    line = abs(height) - 1 - y if height > 0 else y
    try:
        if depth == 24:
            b, g, r = data[pixels + line * row + x * 3 :][:3]
        elif depth == 8:
            b, g, r = data[14 + header + data[pixels + line * row + x] * 4 :][:3]
        else:
            return None
    except (IndexError, ValueError):  # a short palette or pixel area
        return None
    return r, g, b


def wallpaper_sample(state: State, width: int, height: int, bottom: int):
    """A screen point no state window covers, or None if windows cover every candidate."""
    shown = [w for w in state.windows if w.show != "minimized"]
    for x, y in (
        (width // 2, height // 2),
        (width // 4, height // 4),
        (3 * width // 4, height // 4),
        (width // 4, 3 * height // 4),
    ):
        y = min(y, bottom - 12)  # above the taskbar: `bottom` is the work area's
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


def expected_pixel(
    desk, screen: tuple[int, int], colour: tuple[int, int, int], x: int, y: int
):
    """What a wallpaper in `desk.style` puts at screen (x, y), or None if it can't be told."""
    data = desk.wallpaper.data
    width, height = check_bmp(data)
    if desk.style == "tile":
        return bmp_pixel(data, x % width, y % height)
    if desk.style == "stretch":
        return bmp_pixel(data, x * width // screen[0], y * height // screen[1])
    left, top = (screen[0] - width) // 2, (screen[1] - height) // 2
    if 0 <= x - left < width and 0 <= y - top < height:
        return bmp_pixel(data, x - left, y - top)
    return colour


def check_state(scene, state: State, props: dict) -> None:
    """Raise StateMismatch listing every way the guest differs from the state."""
    guest = scene.guest
    bottom = scene.space.work_area[3]
    problems = []
    if state.display:
        actual = display_mode(guest)
        if actual != state.display:
            problems.append(
                f"display: {list(state.display)} wanted, the guest has {list(actual)}"
            )
    desk = state.desktop
    if desk and desk.colour:
        actual = guest.user32.GetSysColor(COLOR_DESKTOP).value & 0xFFFFFF
        if actual != colourref(desk.colour):
            problems.append(
                f"desktop.colour: {colourref(desk.colour):06x} wanted, the guest has {actual:06x} (as BGR)"
            )
    if desk and desk.wallpaper:
        problem = wallpaper_problem(guest, state, desk, bottom)
        if problem:
            problems.append(problem)
    found = {w.hwnd: w for w in guest.windows()}
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
        hwnd = real.hwnd
        show = (
            "minimized"
            if guest.user32.IsIconic(hwnd).value
            else "maximized"
            if guest.user32.IsZoomed(hwnd).value
            else "normal"
        )
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
    # Top-level windows come back front to back, and the state lists them
    # back to front. Windows 98 sends a minimized window to the bottom, so only
    # the others are compared.
    stacked = [w for w in reversed(state.windows) if w.show != "minimized"]
    wanted = [props[w.id].hwnd for w in stacked]
    order = [hwnd for hwnd in found if hwnd in wanted]
    if len(order) == len(wanted) and order != wanted:
        ids = {props[w.id].hwnd: w.id for w in stacked}
        problems.append(
            f"windows: front to back should be {[ids[h] for h in wanted]}, "
            f"the guest has {[ids[h] for h in order]}"
        )
    if state.pointer:
        actual = cursor_pos(guest)
        if actual != state.pointer:
            problems.append(
                f"pointer: {list(state.pointer)} wanted, the guest has {list(actual)}"
            )
    if problems:
        raise StateMismatch(state.path, problems)


def wallpaper_problem(guest, state: State, desk, bottom: int) -> str | None:
    """The wallpaper can't be read back (SPI_GETDESKWALLPAPER gives nothing), so one pixel is compared."""
    screen = screen_size(guest)
    point = wallpaper_sample(state, *screen, bottom)
    if point is None:
        return None
    colour = guest.user32.GetSysColor(COLOR_DESKTOP).value & 0xFFFFFF
    want = expected_pixel(
        desk, screen, (colour & 255, colour >> 8 & 255, colour >> 16), *point
    )
    if want is None:
        return None
    dc = guest.user32.GetDC(0).value
    try:
        got = guest.gdi32.GetPixel(dc, *point).value & 0xFFFFFF
    finally:
        guest.user32.ReleaseDC(0, dc)
    got = (got & 255, got >> 8 & 255, got >> 16)
    if any(abs(g - w) > PIXEL_TOLERANCE for g, w in zip(got, want)):
        return f"desktop.wallpaper: pixel {list(point)} should be about {list(want)}, the guest has {list(got)}"
    return None


def set_state(scene, path) -> None:
    state = load_state(scene._find_state(Path(path)))
    if not scene.session.fresh:  # a scene's first Set follows run's revert
        scene.session.revert()
    scene.session.fresh = False
    for prop in scene.props:
        prop.forget()
    scene.props.clear()
    scene._state_props = {}
    scene._start_desktop()
    props = apply_state(scene, state)
    check_state(scene, state, props)
    scene._state_props = props


def find_state(scene, path: Path) -> Path:
    if path.is_absolute():
        return path
    try:
        beside = Path(inspect.getfile(type(scene))).resolve().parent / path
    except (TypeError, OSError):
        beside = None
    if beside is not None and beside.exists():
        return beside
    return Path.cwd() / path


def start_desktop(scene) -> None:
    scene.prepare_desktop()
    scene.pointer = Pointer()
    scene.pointer.attach(scene)


def prepare_desktop(scene) -> None:
    if scene.desktop == "blank":
        blank(scene.guest, pointer=scene.pointer_visible)
    scene.calibrate()
