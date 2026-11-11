"""Notepad: the stock Windows 98 text editor, as a prop."""

from __future__ import annotations

from pathlib import Path

from ... import guestfiles
from ...win32 import WM_SETTEXT
from ..window.win import Win

EM_SETMODIFY = 0x00B9


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
        if self.open_file is not None:
            guestfiles.check_name(self.open_file.name)
            self._file_data = guestfiles.check_text(
                self.open_file.read_bytes(), str(self.open_file)
            )
        if title is None:
            title = notepad_title(self.open_file) if self.open_file is not None else ""
        super().__init__(title, at=at, size=size)

    @classmethod
    def from_state(cls, window) -> Notepad:
        file = window.open.path if window.open else None
        return cls(window.title, open=file, at=window.at, size=window.size)

    def attach(self, scene) -> None:
        if self._file_data is not None:
            path = guestfiles.ensure(scene.guest, self._file_data, self.open_file.name)
            self.command = f'notepad.exe "{path}"'
        super().attach(scene)

    def ready(self) -> None:
        self._edit = self.children("Edit")[0].hwnd
        super().ready()

    @property
    def edit_hwnd(self) -> int:
        return self._edit

    @property
    def text(self) -> str:
        return self._read_text(self._edit)

    def set_text(self, text: str) -> None:
        self.guest.user32.SendMessageA(self._edit, WM_SETTEXT, 0, text)

    def close(self) -> None:
        """Close without the save prompt, by marking the text as saved."""
        self.guest.user32.SendMessageA(self._edit, EM_SETMODIFY, 0, 0)
        super().close()
