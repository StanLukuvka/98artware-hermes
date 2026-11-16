"""Dialog: a real Windows message box, opened by the agent on a guest thread of its own."""

from __future__ import annotations

import struct
from typing import ClassVar

from ...errors import GuestError, UsageError
from ...guest import guestfiles, win32
from ...guest.bridge import wait_for
from ...guest.win32 import (
    BM_CLICK,
    DIALOG_CLASS,
    SS_ICON,
    SS_TYPEMASK,
    STM_SETICON,
    window_style,
)
from ..window.win import Win

BUTTON_W, BUTTON_H, BUTTON_GAP, BOTTOM_MARGIN = 75, 23, 6, 11
TEXT_X, RIGHT_MARGIN, LINE_H = 62, 16, 15
NO_ICON, NOT_ICON_FILE = (
    0,
    1,
)  # ExtractIconA's answers for "no icon" and "not an icon file"
DISABLED = 0  # EnableWindow's bEnable, to grey a button
REPAINT = 1  # MoveWindow's bRepaint
CLOSE_TIMEOUT, CLOSE_POLL = 5.0, 0.05  # seconds for a box to go once it is told to


class Dialog(Win):
    """A real message box, opened by the agent on a guest thread of its own.

    `button` gives a control to click. `answer` is the button pressed, once
    the box has closed. `icon` is None, a stock key of Dialog.ICONS, a key of
    Dialog.CUSTOM_ICONS or a `(dll, index)` pair naming an icon in a guest file.
    `buttons` is one of Dialog.BUTTONS. `size` is the outer size and must not be
    smaller than the stock box; the buttons and the text are laid out again to
    fit. `disabled` greys every button. `set_message` changes the text. `remove`
    closes the box whatever its buttons are.
    """

    window_class = DIALOG_CLASS
    ICONS: ClassVar[dict[str | None, int]] = {
        None: 0,
        "error": 0x10,
        "question": 0x20,
        "warning": 0x30,
        "info": 0x40,
    }
    # Each one seen in a real box (experiments/dialog-storm/README-p1.md, dev branch).
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
    # The button that answers for Cancel in a box that has none. WM_CLOSE is
    # ignored by those boxes, so `remove` presses this one (IDNO, IDABORT).
    DECLINE: ClassVar[dict[str, int]] = {"yesno": 7, "abortretryignore": 3}

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
        if buttons not in self.BUTTONS:
            raise UsageError(
                f"no button set {buttons!r}; Dialog has {', '.join(self.BUTTONS)}"
            )
        self._check_text("text", text)
        self._check_text("title", title)
        super().__init__(title, at=at, size=size)
        self.message = text
        self.disabled = disabled
        self._custom = None
        if isinstance(icon, (tuple, list)):
            if (
                len(icon) != 2
                or not isinstance(icon[0], str)
                or not isinstance(icon[1], int)
            ):
                raise UsageError(f"a custom icon is a (dll, index) pair, not {icon!r}")
            self._custom = tuple(icon)
        elif icon in self.CUSTOM_ICONS:
            self._custom = self.CUSTOM_ICONS[icon]
        elif icon not in self.ICONS:
            raise UsageError(f"unknown icon {icon!r}")
        # A custom icon reserves the information icon's slot, which is then replaced.
        base = self.ICONS["info"] if self._custom else self.ICONS[icon]
        self.style = base | self.BUTTONS[buttons]
        self._button_set = buttons
        self._call = None
        self._hicon = None
        self._text_hwnd = None
        self._seen: set[int] = set()

    @staticmethod
    def _check_text(label: str, value: str) -> None:
        if bad := guestfiles.not_cp1252(value):
            raise UsageError(
                f"the box's {label} has {bad!r}, which Windows 1252 can't hold:"
                " it would show as '?'"
            )

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
        try:
            super().attach(scene)
        except UsageError:
            # The box is open on the guest already, so close it before the error.
            if self.hwnd is not None:
                self.remove()
            raise

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

    def min_size(self) -> tuple[int, int] | None:
        """The stock box's size, read as it opens: a smaller one clips the text and buttons."""
        return self._sent[2:] if self._sent else None

    def ready(self) -> None:
        user32 = self.guest.user32
        statics = self.children("Static")
        styles = {w.hwnd: window_style(self.guest, w.hwnd) for w in statics}
        text = [w.hwnd for w in statics if styles[w.hwnd] & SS_TYPEMASK != SS_ICON]
        self._text_hwnd = text[0] if text else None
        if self._custom:
            slot = next(
                (h for h, s in styles.items() if s & SS_TYPEMASK == SS_ICON), None
            )
            if slot is None:
                raise GuestError("the message box has no icon control")
            self._set_icon(slot, *self._custom)
        buttons = [w.hwnd for w in self.children("Button")]
        if self._size:
            self._relayout(buttons)
        if self.disabled:
            for hwnd in buttons:
                user32.EnableWindow(hwnd, DISABLED)
        super().ready()

    def _set_icon(self, slot: int, dll: str, index: int) -> None:
        hicon = self.guest.shell32.ExtractIconA(0, dll, index).value
        if hicon in (NO_ICON, NOT_ICON_FILE):
            raise UsageError(f"{dll} has no icon {index}")
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
        """Lay the buttons out along the bottom and widen the text, to the size Win already gave the box."""
        user32 = self.guest.user32
        width, height = self.client_size()
        total = len(buttons) * BUTTON_W + (len(buttons) - 1) * BUTTON_GAP
        x = (width - total) // 2
        for hwnd in buttons:
            user32.MoveWindow(
                hwnd,
                x,
                height - BOTTOM_MARGIN - BUTTON_H,
                BUTTON_W,
                BUTTON_H,
                REPAINT,
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
                REPAINT,
            )
        # Moving the buttons can leave a stale copy of one over another (a Yes drawn on No).
        win32.repaint_window(self.guest, self.hwnd)

    def set_message(self, text: str) -> None:
        """Change the box's text. The text area grows to its lines; it must stay clear of the buttons."""
        self._check_text("text", text)
        guest = self._guest_or_usage()
        if self._text_hwnd is None:
            raise GuestError("this message box has no text control")
        text_rect = win32.window_rect(guest, self._text_hwnd)
        if text_rect is None:
            raise GuestError("the message box has closed")
        left, top, right, _ = text_rect
        height = LINE_H * (text.count("\n") + 1)
        buttons = [win32.window_rect(guest, w.hwnd) for w in self.children("Button")]
        if buttons and top + height > min(b[1] for b in buttons):
            raise UsageError(
                "the message is too tall for the box: give the Dialog a bigger size"
            )
        guest.user32.MoveWindow(
            self._text_hwnd,
            TEXT_X,
            top + self._origin()[1],
            right - left,
            height,
            REPAINT,
        )
        guest.user32.SetWindowTextA(self._text_hwnd, text)
        self.message = text
        guest.sync(self.hwnd)

    def button(self, label: str) -> Win:
        """A button of the box, by its text."""
        return self.control("Button", label)

    @property
    def answer(self) -> str:
        """The button pressed, once the box has closed: "ok", "cancel", "yes" and so on."""
        self._guest_or_usage()
        code = self._call.wait().value
        if code not in self.ANSWERS:
            raise GuestError(f"MessageBox returned {code}, which is not a button")
        return self.ANSWERS[code]

    def _guest_or_usage(self):
        """The guest, or the "not on stage" UsageError if the box is not on stage."""
        if self._call is None:
            raise UsageError("Dialog is not on stage: add it to the scene first")
        return self.guest

    def remove(self) -> None:
        """Close the box, then destroy its custom icon once the box has gone.

        A box with no Cancel button ignores WM_CLOSE, so its No or Abort
        button is pressed instead (see `DECLINE`).
        """
        user32 = self.guest.user32
        if self.hwnd is not None and user32.IsWindow(self.hwnd).value:
            if self._button_set in self.DECLINE:
                button = user32.GetDlgItem(
                    self.hwnd, self.DECLINE[self._button_set]
                ).value
                user32.SendMessageA(button, BM_CLICK, 0, 0)
            else:
                self.close()
            gone = wait_for(
                lambda: None if user32.IsWindow(self.hwnd).value else True,
                CLOSE_TIMEOUT,
                CLOSE_POLL,
            )
            if gone is None:
                raise GuestError("the message box did not close")
        if self._hicon:
            user32.DestroyIcon(self._hicon)
            self._hicon = None

    def forget(self) -> None:
        super().forget()
        self._hicon = None  # the guest went with the revert
