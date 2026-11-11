"""Menu: open a window's menu with real clicks and pick an item."""

from __future__ import annotations

from ...base.animation import Succession
from ...base.props import Spot
from ...protocol import Out
from ...win32 import MF_BYPOSITION, menu_item_rect
from ..pointer import Click
from .win import Win


class Menu(Succession):
    """Open a window's menu with real clicks, and pick an item.

    `Menu(pad, "File", "Save As...")` clicks each label in turn. Labels are
    found in the live menu, and `&` shortcut marks and case are ignored. Each
    click is aimed when its turn comes, because Windows only knows where a
    menu item is once its menu is open.
    """

    def __init__(self, window: Win, *labels: str, step: float = 0.45):
        if not labels:
            raise ValueError("Menu needs at least one label")
        clicks = [
            Click(Spot(_item_finder(window, labels[: depth + 1])), run_time=step)
            for depth in range(len(labels))
        ]
        super().__init__(*clicks)
        self.target = window


def _item_finder(window: Win, labels: tuple[str, ...]):
    def label_of(menu: int, position: int) -> str:
        result = window.guest.user32.GetMenuStringA(
            menu, position, Out(64), 64, MF_BYPOSITION
        )
        return (
            result.buffers[0][: result.value]
            .decode("cp1252")
            .split("\t")[0]
            .replace("&", "")
        )

    def position_of(menu: int, label: str) -> int:
        count = window.guest.user32.GetMenuItemCount(menu).value
        names = [label_of(menu, position) for position in range(count)]
        for position, name in enumerate(names):
            if name.lower() == label.lower():
                return position
        raise LookupError(f"no menu item {label!r}: found {[n for n in names if n]}")

    def locate():
        user32 = window.guest.user32
        menu = user32.GetMenu(window.hwnd).value
        for label in labels[:-1]:
            menu = user32.GetSubMenu(menu, position_of(menu, label)).value
        rect = menu_item_rect(
            window.guest, window.hwnd, menu, position_of(menu, labels[-1])
        )
        if rect is None:
            raise LookupError(f"menu {' > '.join(labels)} is not open")
        left, top, right, bottom = rect
        return (left + right) / 2, (top + bottom) / 2

    return locate
