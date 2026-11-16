"""States: hand-written JSON files describing a static starting desktop.

A state is checked completely when it is loaded, before the guest is
touched, so a mistake in the file never leaves a half-made desktop.

    {
      "version": 1,
      "display": [800, 600, 16],
      "desktop": {"fill_colour": [0, 128, 128],
                  "wallpaper": {"file": "assets/wall.bmp", "sha256": "..."},
                  "style": "center" | "tile" | "stretch"},
      "windows": [
        {"id": "flower", "app": "picture" | "notepad", "title": "Flower",
         "at": [60, 60], "size": [300, 220], "show": "normal",
         "open": {"file": "assets/flower.bmp", "sha256": "..."}}
      ],
      "pointer": [400, 500]
    }

Windows are listed back to front, so the last one is on top and active.
Anything a state leaves out stays as it is on the baseline. Files are found
relative to the state file, and their hash must match. Unknown fields fail.

`desktop` is optional, and so is each part of it. `fill_colour` is the plain desktop colour
behind the icons and around a centred wallpaper (Windows calls it "Desktop" in Display
Properties, Appearance), not the taskbar or selection colour. `style` needs a wallpaper
(default "center"). The wallpaper reaches the guest on a CD, so a state without
one never touches the drive. Desktop icons and the tray come later.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .actions.apps import APPS
from .desktop import STYLES, resolve_colour
from .errors import Art98Error, NotFound, UsageError
from .guest.guestfiles import check_bmp, name_problem, title_problem
from .guest.win32 import DISPLAY_SIZES

VERSION = 1
SHOWS = ("normal", "minimized", "maximized")
DEPTHS = (8, 16, 24, 32)
TOP_FIELDS = {"version", "display", "desktop", "windows", "pointer"}
DESKTOP_FIELDS = {"fill_colour", "wallpaper", "style"}
WINDOW_FIELDS = {"id", "app", "title", "at", "size", "show", "open"}
FILE_FIELDS = {"file", "sha256"}
LATER = {"focus": "order the windows instead: the last one is active"}
LATER_DESKTOP = {
    "icons": "desktop icons come after this milestone",
    "tray": "the tray comes after this milestone",
}
# Emptied at each release, like `RETIRED` in scene.py.
RENAMED_DESKTOP = {"colour": "fill_colour"}
RENAMED_NONE: dict = {}


def problem_text(path: Path | str, problems: list[str]) -> str:
    """The message of an error about a state file: its path, then one line per problem."""
    return f"{path}:\n" + "\n".join(f"  {p}" for p in problems)


class StateError(Art98Error, ValueError):
    """A state file that can't be used, with every problem found in it."""

    def __init__(self, path: Path | str, problems: list[str]):
        self.path, self.problems = Path(path), problems
        super().__init__(problem_text(path, problems))


@dataclass(frozen=True)
class FileRef:
    path: Path
    sha256: str
    data: bytes


@dataclass(frozen=True)
class WindowState:
    id: str
    app: str
    title: str
    at: tuple[int, int]
    size: tuple[int, int]
    show: str = "normal"
    open: FileRef | None = None


@dataclass(frozen=True)
class DesktopState:
    fill_colour: tuple[int, int, int] | None = None
    wallpaper: FileRef | None = None
    style: str = "center"


@dataclass(frozen=True)
class State:
    path: Path
    display: tuple[int, int, int] | None
    windows: tuple[WindowState, ...]
    pointer: tuple[int, int] | None
    desktop: DesktopState | None = None

    def window(self, id: str) -> WindowState:
        for window in self.windows:
            if window.id == id:
                return window
        raise NotFound(f"{self.path} has no window {id!r}")


def _whole_numbers(value, count: int) -> bool:
    return (
        isinstance(value, list)
        and len(value) == count
        and all(isinstance(v, int) and not isinstance(v, bool) for v in value)
    )


class _Reader:
    def __init__(self, path: Path):
        self.path, self.base, self.problems = path, path.parent, []

    def fail(self, where: str, message: str) -> None:
        self.problems.append(f"{where}: {message}")

    def fields(
        self,
        value,
        allowed: set[str],
        where: str,
        later: dict = LATER,
        renamed: dict = RENAMED_NONE,
    ) -> bool:
        if not isinstance(value, dict):
            self.fail(where, "should be an object")
            return False
        for key in sorted(set(value) - allowed):
            reason = later.get(key)
            if key in renamed:
                message = f"renamed to {renamed[key]}"
            elif reason:
                message = f"not supported yet: {reason}"
            else:
                message = "unknown field"
            self.fail(f"{where}.{key}" if where != "state" else key, message)
        return True

    def numbers(self, value, count: int, where: str, positive: bool = False):
        if not _whole_numbers(value, count):
            self.fail(where, f"needs {count} whole numbers")
            return None
        if positive and any(v <= 0 for v in value):
            self.fail(where, "needs numbers above 0")
            return None
        return tuple(value)

    def file(self, value, where: str) -> FileRef | None:
        if not self.fields(value, FILE_FIELDS, where):
            return None
        name, digest = value.get("file"), value.get("sha256")
        if not isinstance(name, str) or not name:
            self.fail(f"{where}.file", "needs a path, relative to the state file")
            return None
        path = (self.base / name).resolve()
        if not path.is_file():
            self.fail(f"{where}.file", f"{path} doesn't exist")
            return None
        try:
            data = path.read_bytes()
        except OSError as error:
            self.fail(f"{where}.file", f"can't read {path}: {error.strerror}")
            return None
        actual = hashlib.sha256(data).hexdigest()
        if isinstance(digest, str):
            digest = digest.lower()
        if digest != actual:
            self.fail(
                f"{where}.sha256",
                f"should be {actual} for {name}"
                + ("" if digest is None else f", not {digest}"),
            )
            return None
        return FileRef(path, actual, data)

    def desktop(self, value, where: str) -> DesktopState | None:
        if not self.fields(
            value, DESKTOP_FIELDS, where, LATER_DESKTOP, RENAMED_DESKTOP
        ):
            return None
        problems = len(self.problems)
        fill_colour = wallpaper = None
        if "fill_colour" in value:
            fill_colour = self.numbers(value["fill_colour"], 3, f"{where}.fill_colour")
            if fill_colour:
                try:
                    resolve_colour(fill_colour)
                except UsageError as error:
                    self.fail(f"{where}.fill_colour", str(error))
        if "wallpaper" in value:
            wallpaper = self.file(value["wallpaper"], f"{where}.wallpaper")
            if wallpaper is not None:
                try:
                    check_bmp(wallpaper.data, str(wallpaper.path))
                except UsageError as error:
                    self.fail(f"{where}.wallpaper", str(error))
        style = value.get("style", "center")
        if "style" in value and "wallpaper" not in value:
            self.fail(f"{where}.style", "needs a wallpaper")
        elif not isinstance(style, str) or style not in STYLES:
            self.fail(
                f"{where}.style", f"should be one of {', '.join(STYLES)}, not {style!r}"
            )
        if len(self.problems) > problems:
            return None
        return DesktopState(fill_colour, wallpaper, style)

    def window(self, value, where: str) -> WindowState | None:
        if not self.fields(value, WINDOW_FIELDS, where):
            return None
        problems = len(self.problems)
        id_ = value.get("id")
        if not isinstance(id_, str) or not id_:
            self.fail(f"{where}.id", "needs a name")
        app = value.get("app")
        known = isinstance(app, str) and app in APPS
        if not known:
            self.fail(
                f"{where}.app", f"should be one of {', '.join(APPS)}, not {app!r}"
            )
        title = value.get("title")
        if title is not None and not isinstance(title, str):
            self.fail(f"{where}.title", "needs text")
        elif title is not None and title_problem(title):
            self.fail(f"{where}.title", f"{title!r} {title_problem(title)}")
        at = self.numbers(value.get("at"), 2, f"{where}.at")
        size = self.numbers(value.get("size"), 2, f"{where}.size", positive=True)
        show = value.get("show", "normal")
        if show not in SHOWS:
            self.fail(
                f"{where}.show", f"should be one of {', '.join(SHOWS)}, not {show!r}"
            )
        opened = None
        if "open" in value:
            opened = self.file(value["open"], f"{where}.open")
            if opened is not None and name_problem(opened.path.name):
                self.fail(
                    f"{where}.open.file",
                    f"{opened.path.name!r} can't be a file name on the"
                    f" guest: it {name_problem(opened.path.name)}",
                )
            elif opened is not None and known:
                try:
                    APPS[app].check_file(opened.data, str(opened.path))
                except UsageError as error:
                    self.fail(f"{where}.open", str(error))
        if len(self.problems) > problems:
            return None
        if title is None:
            title = APPS[app].default_title(opened.path if opened else None)
        return WindowState(id_, app, title, at, size, show, opened)

    def state(self, value) -> State | None:
        if not self.fields(value, TOP_FIELDS, "state"):
            return None
        if value.get("version") != VERSION:
            self.fail("version", f"should be {VERSION}")
        display = None
        if "display" in value:
            display = self.numbers(value["display"], 3, "display", positive=True)
            if display and display[:2] not in DISPLAY_SIZES:
                sizes = ", ".join(f"{w}x{h}" for w, h in DISPLAY_SIZES)
                self.fail("display", f"size should be one of {sizes}")
            if display and display[2] not in DEPTHS:
                self.fail("display", f"colour depth should be one of {DEPTHS}")
        desktop = (
            self.desktop(value["desktop"], "desktop") if "desktop" in value else None
        )
        windows, seen = [], set()
        raw = value.get("windows", [])
        if not isinstance(raw, list):
            self.fail("windows", "should be a list, back to front")
            raw = []
        for index, item in enumerate(raw):
            window = self.window(item, f"windows[{index}]")
            if window is None:
                continue
            if window.id in seen:
                self.fail(f"windows[{index}].id", f"{window.id!r} is used twice")
            seen.add(window.id)
            windows.append(window)
        pointer = None
        if "pointer" in value:
            pointer = self.numbers(value["pointer"], 2, "pointer")
            if pointer and min(pointer) < 0:
                self.fail("pointer", "can't be negative")
            elif (
                pointer
                and display
                and not (pointer[0] < display[0] and pointer[1] < display[1])
            ):
                self.fail(
                    "pointer", f"should be on the {display[0]}x{display[1]} screen"
                )
        if self.problems:
            return None
        return State(self.path, display, tuple(windows), pointer, desktop)


def load_state(path: Path | str) -> State:
    """Read and check a state file, or raise StateError listing every problem."""
    path = Path(path).resolve()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise StateError(path, ["the file doesn't exist"]) from None
    except OSError as error:
        raise StateError(path, [f"can't read the file: {error.strerror}"]) from None
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise StateError(path, [f"not valid JSON: {error}"]) from None
    reader = _Reader(path)
    state = reader.state(value)
    if state is None:
        raise StateError(path, reader.problems)
    return state
