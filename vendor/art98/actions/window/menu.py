"""Menu: open a window's menu with real clicks and pick an item."""

from __future__ import annotations

from ...base.animation import Succession
from ...base.props import Spot
from ...errors import NotFound, UsageError
from ...guest import win32
from ...guest.protocol import Out
from ..pointer import Click
from .win import Win

MENU_LABEL_MAX = 64  # the longest label read; a longer one is cut


class Menu(Succession):
    """Open a window's menu with real clicks, and pick an item.

    `Menu(pad, "File", "Save As...")` clicks each label in turn. Labels are
    found in the live menu, and `&` shortcut marks and case are ignored. Each
    click is aimed when its turn comes, because Windows only knows where a
    menu item is once its menu is open.
    """

    def __init__(self, window: Win, *labels: str, step: float = 0.45):
        if not labels:
            raise UsageError("Menu needs at least one label")
        clicks = [
            Click(Spot(_item_finder(window, labels[: depth + 1])), run_time=step)
            for depth in range(len(labels))
        ]
        super().__init__(*clicks)
        self.target = window


def _item_finder(window: Win, labels: tuple[str, ...]):
    def label_of(menu: int, position: int) -> str:
        result = window.guest.user32.GetMenuStringA(
            menu, position, Out(MENU_LABEL_MAX), MENU_LABEL_MAX, win32.MF_BYPOSITION
        )
        return (
            result.buffers[0][: result.value]
            .decode("cp1252", errors="replace")
            .split("\t")[0]
            .replace("&", "")
        )

    def position_of(menu: int, label: str) -> int:
        count = window.guest.user32.GetMenuItemCount(menu).signed
        if count < 0:
            raise NotFound(f"window {window.hwnd:#x} has no menu {menu:#x}")
        for position in range(count):
            if label_of(menu, position).lower() == label.lower():
                return position
        names = [label_of(menu, position) for position in range(count)]
        raise NotFound(f"no menu item {label!r}: found {[n for n in names if n]}")

    def submenu_of(menu: int, label: str) -> int:
        submenu = window.guest.user32.GetSubMenu(menu, position_of(menu, label)).value
        if not submenu:
            raise NotFound(f"{label!r} has no submenu")
        return submenu

    def locate():
        user32 = window.guest.user32
        menu = user32.GetMenu(window.hwnd).value
        if not menu:
            raise NotFound(f"window {window.hwnd:#x} has no menu")
        for label in labels[:-1]:
            menu = submenu_of(menu, label)
        rect = win32.menu_item_rect(
            window.guest, window.hwnd, menu, position_of(menu, labels[-1])
        )
        if rect is None:
            raise NotFound(f"menu {' > '.join(labels)} is not open")
        left, top, right, bottom = rect
        return (left + right) / 2, (top + bottom) / 2

    return locate
