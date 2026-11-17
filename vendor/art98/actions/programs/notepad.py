"""Notepad: the stock Windows 98 text editor, as a prop."""

from __future__ import annotations

from pathlib import Path

from ...errors import GuestError, UsageError
from ...guest import guestfiles
from ...guest.win32 import EM_GETSEL, EM_SETMODIFY, EM_SETSEL
from ..window.win import Win


def notepad_title(file: Path | str | None) -> str:
    """The title bar text Notepad shows for a file, or for none.

    Checked on the real image: a trailing ".txt" (any case) is dropped, every
    other extension stays, and the case of the name is kept.
    """
    if not file:
        return "Untitled - Notepad"
    name = Path(file).name
    if name.lower().endswith(".txt"):
        name = name[:-4]
    return f"{name} - Notepad"


class Notepad(Win):
    """Notepad and its Edit control. Text goes to `edit_hwnd`.

    `open` is a text file on the host, or None for an empty "Untitled" Notepad.
    It is copied to the guest and passed on the command line, so the title bar
    reads "<name> - Notepad". `at` and `size` are applied when the prop is added;
    without them the window stays where Notepad opened it. A bare Notepad() has
    the title "" and so takes any Notepad window on the guest.
    """

    command = "notepad.exe"
    window_class = "Notepad"
    open_keyword = "open"
    state_fields = ("text", "selection")
    check_file = staticmethod(
        guestfiles.check_text
    )  # what a state's `open` file must pass

    def __init__(
        self,
        title: str | None = None,
        *,
        open: Path | str | None = None,
        at: tuple[int, int] | None = None,
        size: tuple[int, int] | None = None,
    ):
        self.open_file = Path(open) if open is not None else None
        self._file_data = None
        self._edit = None  # the Edit control, found in ready
        self._state_text = None  # set in ready, from a state's `text` file
        self._state_selection: tuple[int, int] | None = None  # a state's `selection`
        if self.open_file is not None:
            guestfiles.check_name(self.open_file.name)
            self._file_data = guestfiles.check_text(
                guestfiles.read_host_file(self.open_file), str(self.open_file)
            )
        if title is None:
            title = notepad_title(self.open_file) if self.open_file is not None else ""
        super().__init__(title, at=at, size=size)

    @staticmethod
    def default_title(file: Path | None) -> str:
        """The title a state gives a Notepad window that names none."""
        return notepad_title(file)

    @classmethod
    def from_state(cls, window) -> Notepad:
        """As `Win.from_state`, keeping a state's `text` and `selection` to restore once up."""
        pad = super().from_state(window)
        if window.text is not None:
            data = guestfiles.check_text(window.text.data, str(window.text.path))
            pad._state_text = data.decode("cp1252")
        if window.selection is not None:
            pad._state_selection = window.selection
        return pad

    def to_state(self, files: Path) -> dict:
        state = self._state_base()
        text = self.text
        if self.open_file is not None:
            state["open"] = self._state_file(
                files, self.open_file.name, self._file_data
            )
            changed = text != self._file_data.decode("cp1252")
        else:
            changed = bool(text)
        if changed:
            data = guestfiles.check_text(text.encode("cp1252", errors="replace"))
            taken = self.open_file.name if self.open_file else ""
            name = "text-edited.txt" if taken.lower() == "text.txt" else "text.txt"
            state["text"] = self._state_file(files, name, data)
        # Windows 98 Notepad's Edit caps text at 64K, so both offsets fit in 16 bits.
        r = self.guest.user32.SendMessageA(self.edit_hwnd, EM_GETSEL, 0, 0).value
        state["selection"] = [r & 0xFFFF, (r >> 16) & 0xFFFF]
        return state

    def attach(self, scene) -> None:
        if self._file_data is not None:
            path = guestfiles.ensure(scene.guest, self._file_data, self.open_file.name)
            self.command = f'notepad.exe "{path}"'
        super().attach(scene)

    def ready(self) -> None:
        edits = self.children("Edit")
        if not edits:
            raise GuestError("Notepad has no Edit control")
        self._edit = edits[0].hwnd
        super().ready()
        if self._state_text is not None:
            self.set_text(self._state_text)
        if self._state_selection is not None:
            start, end = self._state_selection
            self.guest.user32.SendMessageA(self.edit_hwnd, EM_SETSEL, start, end)

    @property
    def edit_hwnd(self) -> int:
        if self._edit is None:
            raise UsageError("Notepad is not on stage yet: add it to the scene first")
        return self._edit

    def close(self) -> None:
        """Close without the save prompt, by marking the text as saved."""
        self.guest.user32.SendMessageA(self.edit_hwnd, EM_SETMODIFY, 0, 0)
        super().close()

    def forget(self) -> None:
        super().forget()
        self._edit = None
