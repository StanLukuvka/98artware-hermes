"""Typing and key presses.

Text goes one of two ways. TypeWithCursor sends WM_CHAR to a known control and
needs no focus. Keys and TypeKeys press real keys on the guest's keyboard, so
they go to whatever has the focus.
"""

from __future__ import annotations

import time

from ..base.animation import Animation
from ..base.rate_functions import linear
from ..win32 import repaint_window
from .window.win import Win

WM_CHAR = 0x0102
VK_BACK = 8
KEY_HOLD_MS = 40
KEY_SETTLE = 0.1  # time for a key press to reach the guest and be handled

QCODES = {
    "enter": "ret",
    "return": "ret",
    "escape": "esc",
    "space": "spc",
    "del": "delete",
    "win": "meta_l",
    "pageup": "pgup",
    "pagedown": "pgdn",
    "-": "minus",
    "=": "equal",
    ",": "comma",
    ".": "dot",
    "/": "slash",
    ";": "semicolon",
    "'": "apostrophe",
    "[": "bracket_left",
    "]": "bracket_right",
    "\\": "backslash",
    "`": "grave_accent",
}
UNSHIFTED = r"`1234567890-=[]\;',./"
SHIFTED = '~!@#$%^&*()_+{}|:"<>?'


def key_chord(combo: str) -> tuple[str, ...]:
    """Turn "ctrl+s" into the QEMU key names ("ctrl", "s")."""
    if combo == "+":
        return ("shift", "equal")
    return tuple(QCODES.get(name, name) for name in combo.lower().split("+"))


def char_chord(char: str) -> tuple[str, ...]:
    """The keys that type one character on the guest's US keyboard."""
    if char == "\n":
        return ("ret",)
    if char == "\t":
        return ("tab",)
    if char == " ":
        return ("spc",)
    if char in SHIFTED:
        return ("shift", QCODES.get(plain := UNSHIFTED[SHIFTED.index(char)], plain))
    if char.isascii() and char.isalpha() and char.isupper():
        return ("shift", char.lower())
    if char.isascii() and char.isalnum():
        return (char,)
    if char in UNSHIFTED:
        return (QCODES[char],)
    raise ValueError(f"no key types {char!r}")


class TypeWithCursor(Animation):
    """Type into a Notepad or Edit control, letter by letter.

    Each letter is a WM_CHAR, so the real caret moves and no focus is needed.
    The default run time gives ten letters a second.
    """

    rate_func = staticmethod(linear)
    seconds_per_letter = 0.1

    def __init__(self, target: Win, text: str, **kwargs):
        super().__init__(target, **kwargs)
        self.codes = text.replace("\n", "\r").encode("cp1252", errors="replace")
        if "run_time" not in kwargs or kwargs["run_time"] is None:
            self.run_time = len(self.codes) * self.seconds_per_letter
        self._typed = 0

    def begin(self, scene) -> None:
        super().begin(scene)
        self._typed = 0

    def interpolate(self, alpha: float) -> None:
        hwnd = self.target.edit_hwnd
        wanted = min(len(self.codes), round(alpha * len(self.codes)))
        before = self._typed
        while self._typed < wanted:
            self.scene.guest.user32.SendMessageA(
                hwnd, WM_CHAR, self.codes[self._typed], 1
            )
            self._typed += 1
        if self._typed != before:
            # Typing before existing text makes the Edit shift the rest of the line by a
            # blit and mark nothing invalid, and that blit comes out garbled on screen.
            # SYNC only repaints what is invalid, so the Edit is invalidated here, frame
            # included: the shifted text also leaves a stray letter over its scroll bar.
            repaint_window(self.scene.guest, hwnd)


class UntypeWithCursor(TypeWithCursor):
    """Delete letters with Backspace, from the caret."""

    def __init__(self, target: Win, count: int, **kwargs):
        super().__init__(target, chr(VK_BACK) * count, **kwargs)


class Keys(Animation):
    """Press keys or chords on the guest's real keyboard, evenly over run_time.

    Each argument is one press, such as "ctrl+s", "alt+tab" or "enter". The
    keys go to whatever has the focus.
    """

    rate_func = staticmethod(linear)
    claims = ("keyboard",)
    seconds_per_key = 0.25

    def __init__(self, *combos: str, **kwargs):
        super().__init__(None, **kwargs)
        self.chords = [key_chord(combo) for combo in combos]
        if kwargs.get("run_time") is None:
            self.run_time = len(self.chords) * self.seconds_per_key
        self._pressed = 0

    def begin(self, scene) -> None:
        super().begin(scene)
        self._pressed = 0

    def interpolate(self, alpha: float) -> None:
        wanted = min(len(self.chords), int(alpha * len(self.chords) + 1e-9))
        while self._pressed < wanted:
            self.scene.vm.send_keys(*self.chords[self._pressed], hold_ms=KEY_HOLD_MS)
            self._pressed += 1
            time.sleep(KEY_SETTLE)


class TypeKeys(Keys):
    """Type text on the real keyboard, for windows that need real key presses.

    This covers what WM_CHAR can't: dialogs without a known Edit control,
    the MS-DOS Prompt and games. US layout, printable ASCII, \\n and \\t.
    """

    seconds_per_key = 0.15

    def __init__(self, text: str, **kwargs):
        super().__init__(**kwargs)
        self.chords = [char_chord(char) for char in text]
        if kwargs.get("run_time") is None:
            self.run_time = len(self.chords) * self.seconds_per_key
