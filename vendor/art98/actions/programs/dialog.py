"""Dialog: a real Windows message box, opened by the agent on a guest thread of its own."""

from __future__ import annotations

import struct
from typing import ClassVar

from ... import win32
from ...win32 import GWL_STYLE
from ..window.win import Win

STM_SETICON, SS_ICON = 0x0170, 3
BASE_ICON = 0x40  # MB_ICONINFORMATION: reserves the icon slot, which is then replaced
BUTTON_W, BUTTON_H, BUTTON_GAP, BOTTOM_MARGIN = 75, 23, 6, 11
TEXT_X, RIGHT_MARGIN, LINE_H = 62, 16, 15


class Dialog(Win):
    """A real message box, opened by the agent on a guest thread of its own.

    `button` gives a control to click. `answer` is the button pressed, once
    the box has closed. `icon` is None, a stock key of Dialog.ICONS, a key of
    Dialog.CUSTOM_ICONS or a `(dll, index)` pair naming an icon in a guest file.
    `buttons` is one of Dialog.BUTTONS. `size` is the outer size and must not be
    smaller than the stock box; the buttons and the text are laid out again to
    fit. `disabled` greys every button. `set_message` changes the text.
    """

    window_class = "#32770"
    ICONS: ClassVar[dict[str | None, int]] = {
        None: 0,
        "error": 0x10,
        "question": 0x20,
        "warning": 0x30,
        "info": 0x40,
    }
    # Each one seen in a real box (experiments/dialog-storm/README-p1.md).
    CUSTOM_ICONS: ClassVar[dict[str, tuple[str, int]]] = {
        "computer": (r"C:\WINDOWS\SYSTEM\shell32.dll", 15),
        "printer": (r"C:\WINDOWS\SYSTEM\shell32.dll", 16),
        "mouse": (r"C:\WINDOWS\SYSTEM\main.cpl", 0),
        "telephone": (r"C:\WINDOWS\SYSTEM\modem.cpl", 0),
        "cd": (r"C:\WINDOWS\SYSTEM\shell32.dll", 11),
        "globe": (r"C:\WINDOWS\SYSTEM\shell32.dll", 13),
        "flag": (r"C:\WINDOWS\SYSTEM\user.exe", 0),
    }
    BUTTONS: ClassVar[dict[str, int]] = {
        "ok": 0,
        "okcancel": 1,
        "abortretryignore": 2,
        "yesnocancel": 3,
        "yesno": 4,
        "retrycancel": 5,
    }
    ANSWERS: ClassVar[dict[int, str]] = {
        1: "ok",
        2: "cancel",
        3: "abort",
        4: "retry",
        5: "ignore",
        6: "yes",
        7: "no",
    }

    def __init__(
        self,
        text: str,
        title: str = "art98",
        *,
        icon: str | tuple[str, int] | None = None,
        buttons: str = "ok",
        at: tuple[int, int] | None = None,
        size: tuple[int, int] | None = None,
        disabled: bool = False,
    ):
        super().__init__(title, at=at, size=size)
        self.message = text
        self.disabled = disabled
        self._custom = None
        if isinstance(icon, tuple):
            self._custom = icon
        elif icon in self.CUSTOM_ICONS:
            self._custom = self.CUSTOM_ICONS[icon]
        elif icon not in self.ICONS:
            raise ValueError(f"unknown icon {icon!r}")
        self.style = (BASE_ICON if self._custom else self.ICONS[icon]) | self.BUTTONS[
            buttons
        ]
        self._call = None
        self._hicon = None
        self._text_hwnd = None
        self._size_checked = False
        self._seen: set[int] = set()

    def attach(self, scene) -> None:
        # Every box comes from the one agent process and may share a title: only the unseen one is ours.
        self._seen = {
            w.hwnd
            for w in scene.guest.windows()
            if w.class_name.lower() == self.window_class
        }
        self._call = scene.guest.acall(
            "user32", "MessageBoxA", 0, self.message, self.title_text, self.style
        )
        super().attach(scene)

    def _look(self, pid):
        title = self.title_text.lower()
        for window in self.guest.windows():
            if (
                window.visible
                and window.hwnd not in self._seen
                and window.class_name.lower() == self.window_class
                and title in window.title.lower()
            ):
                return window
        return None

    def apply(self) -> None:
        # The first apply comes right after refresh() read the stock size into _sent.
        if self._size and not self._size_checked and self._sent:
            self._size_checked = True
            if self._size[0] < self._sent[2] or self._size[1] < self._sent[3]:
                # lazy: the box stays open on the guest until the next revert.
                raise ValueError(
                    f"size {self._size} is smaller than the stock box "
                    f"{self._sent[2:]}: the text and buttons would be clipped"
                )
        super().apply()

    def ready(self) -> None:
        user32 = self.guest.user32
        statics = self.children("Static")
        styles = {
            w.hwnd: user32.GetWindowLongA(w.hwnd, GWL_STYLE & 0xFFFFFFFF).value
            for w in statics
        }
        text = [w.hwnd for w in statics if styles[w.hwnd] & 0x1F != SS_ICON]
        self._text_hwnd = text[0] if text else None
        if self._custom:
            slot = next(h for h, s in styles.items() if s & 0x1F == SS_ICON)
            self._set_icon(slot, *self._custom)
        buttons = [w.hwnd for w in self.children("Button")]
        if self._size:
            self._relayout(buttons)
        if self.disabled:
            for hwnd in buttons:
                user32.EnableWindow(hwnd, 0)
        super().ready()

    def _set_icon(self, slot: int, dll: str, index: int) -> None:
        hicon = self.guest.shell32.ExtractIconA(0, dll, index).value
        if hicon in (0, 1):
            raise ValueError(f"{dll} has no icon {index}")
        self.guest.user32.SendMessageA(slot, STM_SETICON, hicon, 0)
        # The static does not copy the icon, so it is destroyed only in remove().
        self._hicon = hicon
        win32.repaint_window(self.guest, slot)

    def _origin(self) -> tuple[int, int]:
        """Where the client area starts, as the (negative) screen-to-client offset."""
        point = struct.pack("<ii", 0, 0)
        return struct.unpack(
            "<ii", self.guest.user32.ScreenToClient(self.hwnd, point).buffers[0]
        )

    def _relayout(self, buttons: list[int]) -> None:
        user32 = self.guest.user32
        self.space.check_rect(
            self._name(), self.rect, self._size, sizable=False, warned=self._warned
        )
        win32.place_window(
            self.guest, self.hwnd, 0, 0, *self._size, 0x2 | 0x4
        )  # NOMOVE | NOZORDER
        width, height = self.client_size()
        total = len(buttons) * BUTTON_W + (len(buttons) - 1) * BUTTON_GAP
        x = (width - total) // 2
        for hwnd in buttons:
            user32.MoveWindow(
                hwnd, x, height - BOTTOM_MARGIN - BUTTON_H, BUTTON_W, BUTTON_H, 1
            )
            x += BUTTON_W + BUTTON_GAP
        if self._text_hwnd is not None:
            rect = win32.window_rect(self.guest, self._text_hwnd)
            user32.MoveWindow(
                self._text_hwnd,
                TEXT_X,
                rect[1] + self._origin()[1],
                width - TEXT_X - RIGHT_MARGIN,
                rect[3] - rect[1],
                1,
            )
        # Moving the buttons can leave a stale copy of one over another (a Yes drawn on No).
        win32.repaint_window(self.guest, self.hwnd)

    def set_message(self, text: str) -> None:
        """Change the box's text. The text area grows to its lines; it must stay clear of the buttons."""
        user32 = self.guest.user32
        left, top, right, _ = win32.window_rect(self.guest, self._text_hwnd)
        height = LINE_H * (text.count("\n") + 1)
        buttons = [
            win32.window_rect(self.guest, w.hwnd) for w in self.children("Button")
        ]
        if buttons and top + height > min(b[1] for b in buttons):
            raise ValueError(
                "the message is too tall for the box: give the Dialog a bigger size"
            )
        user32.MoveWindow(
            self._text_hwnd, TEXT_X, top + self._origin()[1], right - left, height, 1
        )
        user32.SetWindowTextA(self._text_hwnd, text)
        self.message = text
        self.guest.sync(self.hwnd)

    def button(self, label: str) -> Win:
        """A button of the box, by its text."""
        return self.control("Button", label)

    @property
    def answer(self) -> str:
        return self.ANSWERS.get(self._call.wait().value, "unknown")

    def remove(self) -> None:
        super().remove()
        if self._hicon:
            self.guest.user32.DestroyIcon(self._hicon)
            self._hicon = None

    def forget(self) -> None:
        super().forget()
        self._hicon = None  # the guest went with the revert
