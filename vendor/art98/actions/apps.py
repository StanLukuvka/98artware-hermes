"""The programs a state file can open, by their name in the file.

Each class here is a `Win` subclass that a state can open, and provides the three
hooks `state.py` and `anim/setstate.py` use: `check_file(data, source)` for the
state's `open` file, `default_title(file)` for a window that names none, and the
inherited `from_state(window)`.
"""

from __future__ import annotations

from .programs.notepad import Notepad
from .programs.picture import Picture
from .window.win import Win

APPS: dict[str, type[Win]] = {"picture": Picture, "notepad": Notepad}
