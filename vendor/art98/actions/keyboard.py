"""Typing and key presses.

Text goes one of two ways. TypeWithCursor sends WM_CHAR to a known control and
needs no focus. Keys and TypeKeys press real keys on the guest's keyboard, so
they go to whatever has the focus.
"""

from __future__ import annotations

import time

from ..base import units
from ..base.animation import Animation
from ..base.rate_functions import linear
from ..errors import UsageError
from ..guest import guestfiles
from ..guest.win32 import VK_BACK, WM_CHAR, repaint_window
from .window.win import Win

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
# Every name a key chord may use, besides the QCODES keys: letters, digits, F1-F12,
# the QEMU names of QCODES, and the modifiers and special keys below.
KEY_NAMES = (
    set("abcdefghijklmnopqrstuvwxyz0123456789")
    | {f"f{n}" for n in range(1, 13)}
    | set(QCODES.values())
    | {
        "ctrl",
        "ctrl_r",
        "alt",
        "alt_r",
        "shift",
        "shift_r",
        "meta_l",
        "meta_r",
        "tab",
        "backspace",
        "ret",
        "esc",
        "spc",
        "delete",
        "insert",
        "home",
        "end",
        "pgup",
        "pgdn",
        "up",
        "down",
        "left",
        "right",
        "caps_lock",
        "num_lock",
        "scroll_lock",
        "print",
        "pause",
        "menu",
    }
)


def key_chord(combo: str) -> tuple[str, ...]:
    """Turn "ctrl+s" into the QEMU key names ("ctrl", "s"). UsageError for a name that isn't a key."""
    if combo == "+":
        return ("shift", "equal")
    names = combo.lower().split("+")
    for name in names:
        if name not in QCODES and name not in KEY_NAMES:
            raise UsageError(f"no key called {name!r} in {combo!r}")
    return tuple(QCODES.get(name, name) for name in names)


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
    raise UsageError(f"no key types {char!r}")


def _typed_codes(text: str) -> bytes:
    """The bytes WM_CHAR takes for `text`, with Windows line ends. UsageError for a
    character Windows 1252 can't hold, which would otherwise be typed as "?"."""
    text = text.replace("\r\n", "\n").replace("\n", "\r")
    if bad := guestfiles.not_cp1252(text):
        raise UsageError(f"can't type {bad!r}: Windows 1252 has no such characters")
    return text.encode("cp1252")


class _Presses(Animation):
    """Steps through `items` one at a time, spread evenly over run_time.

    A subclass says what one item does in `press`. The default run time gives
    each item `seconds_per_item`.
    """

    rate_func = staticmethod(linear)
    seconds_per_item = 0.25

    def __init__(self, target, items, **kwargs):
        super().__init__(target, **kwargs)
        self.items = items
        if kwargs.get("run_time") is None:
            self.run_time = len(items) * self.seconds_per_item
        self._done = 0

    def begin(self, scene) -> None:
        super().begin(scene)
        self._done = 0

    def interpolate(self, alpha: float) -> None:
        wanted = units.count(alpha, len(self.items))
        while self._done < wanted:
            self.press(self.items[self._done])
            self._done += 1

    def press(self, item) -> None:
        raise NotImplementedError


class TypeWithCursor(_Presses):
    """Type into a Notepad or Edit control, letter by letter.

    Each letter is a WM_CHAR, so the real caret moves and no focus is needed.
    The default run time gives ten letters a second.
    """

    seconds_per_item = 0.1

    def __init__(self, target: Win, text: str, **kwargs):
        super().__init__(target, _typed_codes(text), **kwargs)

    def press(self, code: int) -> None:
        self.scene.guest.user32.SendMessageA(self.target.edit_hwnd, WM_CHAR, code, 1)

    def interpolate(self, alpha: float) -> None:
        before = self._done
        super().interpolate(alpha)
        if self._done != before:
            # Typing before existing text makes the Edit shift the rest of the line by a
            # blit and mark nothing invalid, and that blit comes out garbled on screen.
            # SYNC only repaints what is invalid, so the Edit is invalidated here, frame
            # included: the shifted text also leaves a stray letter over its scroll bar.
            repaint_window(self.scene.guest, self.target.edit_hwnd)


class UntypeWithCursor(TypeWithCursor):
    """Delete letters with Backspace, from the caret."""

    def __init__(self, target: Win, count: int, **kwargs):
        if count < 1:
            raise UsageError(f"count is {count}; use 1 or more")
        super().__init__(target, chr(VK_BACK) * count, **kwargs)


class Keys(_Presses):
    """Press keys or chords on the guest's real keyboard, evenly over run_time.

    Each argument is one press, such as "ctrl+s", "alt+tab" or "enter". The
    keys go to whatever has the focus.
    """

    claims = ("keyboard",)
    seconds_per_item = 0.25

    def __init__(self, *combos: str, **kwargs):
        super().__init__(None, [key_chord(combo) for combo in combos], **kwargs)

    def press(self, chord: tuple[str, ...]) -> None:
        self.scene.vm.send_keys(*chord, hold_ms=KEY_HOLD_MS)
        time.sleep(KEY_SETTLE)


class TypeKeys(Keys):
    """Type text on the real keyboard, for windows that need real key presses.

    This covers what WM_CHAR can't: dialogs without a known Edit control,
    the MS-DOS Prompt and games. US layout, printable ASCII, \\n and \\t.
    """

    seconds_per_item = 0.15

    def __init__(self, text: str, **kwargs):
        # Not Keys.__init__, which takes key chords: this takes the characters' chords.
        _Presses.__init__(self, None, [char_chord(char) for char in text], **kwargs)
