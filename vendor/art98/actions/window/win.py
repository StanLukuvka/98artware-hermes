"""Win: a real top-level window, or a real control inside one, as a prop."""

from __future__ import annotations

import hashlib
from pathlib import Path

from ...base.props import Prop, Spot
from ...base.vectors import DR, Vec
from ...errors import GuestError, NotFound, UsageError
from ...guest import win32
from ...guest.bridge import Window, wait_for
from ...guest.protocol import Out
from ...guest.win32 import window_rect

FIND_WINDOW_TIMEOUT = 15.0  # seconds a started program has to show its window
FIND_POLL = 0.05  # seconds between looks at the guest's windows
DEFAULT_INSET = 2  # pixels a corner spot sits inside the frame, so it hits the frame


class Win(Prop):
    """A real top-level window, or a real control inside one.

    Give it a `command` to start a program, or `title`, `class_name` or `hwnd`
    to take hold of a window that exists. `at` is the top left corner and
    `size` the outer size. Both are applied when the prop is added.

    A program a state can open (see `actions/apps.py`) also sets `open_keyword`,
    `check_file` and `default_title`, and inherits `from_state`.
    """

    command: str | None = None
    window_class: str | None = None
    # The constructor keyword a state's `open` file is passed as.
    open_keyword: str | None = None
    # Extra state window fields this program accepts (see `state.py`).
    state_fields: tuple[str, ...] = ()

    @staticmethod
    def check_file(data: bytes, source: str) -> None:
        """Raise UsageError if a state's `open` file can't be opened by this program."""
        raise NotImplementedError("a program a state can open sets check_file")

    @staticmethod
    def default_title(file: Path | None) -> str:
        """The title a state gives this program's window when it names none."""
        raise NotImplementedError("a program a state can open sets default_title")

    @classmethod
    def from_state(cls, window) -> Win:
        """The prop for one window of a state file. The file's `show` is applied
        by setstate once the prop is added, and read back by `refresh`."""
        opened = {} if window.open is None else {cls.open_keyword: window.open.path}
        return cls(window.title, at=window.at, size=window.size, **opened)

    def to_state(self, files: Path) -> dict:
        """The state file's JSON for this window. Files it needs are written under
        `files/windows/<id>/`, and named relative to `files.parent`, the state file's folder."""
        raise UsageError(f"{type(self).__name__} can't be captured yet")

    def _state_base(self) -> dict:
        """What every `to_state` has: app, live title, normal place and show state."""
        # apps.py imports the programs, which import this module
        from ..apps import APPS

        app = next((n for n, cls in APPS.items() if isinstance(self, cls)), None)
        row = next((w for w in self.guest.windows() if w.hwnd == self.hwnd), None)
        if app is None or row is None:
            raise UsageError(f"{self._name()} can't be captured: no window to read")
        left, top, right, bottom = win32.normal_rect(self.guest, self.hwnd)
        return {
            "app": app,
            "title": row.title,
            "at": [left, top],
            "size": [right - left, bottom - top],
            "show": win32.show_state(self.guest, self.hwnd),
        }

    def _state_file(self, files: Path, name: str, data: bytes) -> dict:
        """Write `data` as `files/windows/<id>/<name>`; the state's file reference to it."""
        path = files / "windows" / self.state_id / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {
            "file": path.relative_to(files.parent).as_posix(),
            "sha256": hashlib.sha256(data).hexdigest(),
        }

    def __init__(
        self,
        title: str | None = None,
        *,
        class_name: str | None = None,
        hwnd: int | None = None,
        command: str | None = None,
        at: tuple[int, int] | None = None,
        size: tuple[int, int] | None = None,
    ):
        super().__init__()
        self.title_text = title
        self.class_name = class_name or self.window_class
        self.hwnd = hwnd
        if command is not None:
            self.command = command
        if (
            self.command is None
            and hwnd is None
            and title is None
            and self.class_name is None
        ):
            raise UsageError(
                f"{type(self).__name__} needs a command to start, or a title,"
                " class_name or hwnd to find a window"
            )
        self._by_hwnd = (
            hwnd is not None
        )  # a window given by handle can't be found again
        self._at, self._size = at, size
        # Flags of the cycle refresh -> apply -> settle, see the comment above `refresh`.
        self._sent: tuple[int, int, int, int] | None = None
        self._verify = False  # settle is waiting for the next apply
        self.show = "normal"  # "normal", "minimized" or "maximized", as last read
        self._top_level = True  # a control is not sized or settled: see `_child`
        self._sizable = False  # WS_THICKFRAME, read at attach
        self._warned: set[str] = set()
        # Set by Close while it ends the window, so a window on its way out is not "gone".
        self.closing = False
        # (pid, class) of the top-level row read at attach, to tell a reused hwnd
        # from this window. None for a handle that isn't a top-level window.
        self._identity: tuple[int, str] | None = None

    def attach(self, scene) -> None:
        super().attach(scene)
        if self.hwnd is None:
            if self._by_hwnd:
                raise NotFound(
                    f"{type(self).__name__} was made from a window handle, and that"
                    " window is gone: make a new one"
                )
            pid = self.guest.spawn(self.command) if self.command else None
            self.hwnd = self._find(pid).hwnd
        elif window_rect(self.guest, self.hwnd) is None:
            raise NotFound(f"no window {self.hwnd:#x}")
        if self._top_level:
            for other in scene.props:
                if (
                    isinstance(other, Win)
                    and other is not self
                    and other.hwnd == self.hwnd
                ):
                    raise UsageError(
                        f"{self._name()} is the window {other._name()} already owns"
                        f" ({self.hwnd:#x}): one prop per window"
                    )
            self._identity = self._read_identity()
            self._sizable = bool(
                win32.window_style(self.guest, self.hwnd) & win32.WS_THICKFRAME
            )
        self.refresh()
        if self._size:
            self._check_min_size()
            self.width, self.height = self._size
        if self._at:
            self.x, self.y = self._at
        self.apply()
        if self._top_level:
            self.settle()
            self.ready()

    def _read_identity(self) -> tuple[int, str] | None:
        for row in self.guest.windows():
            if row.hwnd == self.hwnd:
                return row.pid, row.class_name.lower()
        return None

    def is_gone(self, rows: dict[int, Window]) -> bool:
        """Whether the window has left `rows` (the guest's top-level windows by
        handle), or the handle now belongs to another window: Windows reuses them."""
        if self._identity is None:
            return False
        row = rows.get(self.hwnd)
        return row is None or (row.pid, row.class_name.lower()) != self._identity

    def min_size(self) -> tuple[int, int] | None:
        """The least outer size this window can take, or None for no least.

        Read once the window is on the desktop, before `size` is applied. A
        subclass whose window has a stock size returns that.
        """
        return None

    def _check_min_size(self) -> None:
        minimum = self.min_size()
        if minimum and (self._size[0] < minimum[0] or self._size[1] < minimum[1]):
            raise UsageError(
                f"size {self._size} is smaller than the least a {type(self).__name__}"
                f" takes, {minimum}: its contents would be clipped"
            )

    def _find(self, pid: int | None) -> Window:
        found = wait_for(lambda: self._look(pid), FIND_WINDOW_TIMEOUT, FIND_POLL)
        if found is None:
            raise NotFound(f"{self._not_found(pid)} (waited {FIND_WINDOW_TIMEOUT:g} s)")
        return found

    def _look(self, pid: int | None) -> Window | None:
        """One look at the guest's windows: the one wanted, or None. A subclass
        can raise here to fail early."""
        for window in self.guest.windows():
            if window.visible and window.matches(self.title_text, self.class_name, pid):
                return window
        return None

    def _not_found(self, pid: int | None) -> str:
        return (
            f"no window for {type(self).__name__}: "
            f"title={self.title_text!r} class={self.class_name!r} pid={pid}"
        )

    def ready(self) -> None:
        """Called once the window exists and has been placed."""
        self.guest.sync(self.hwnd)

    # The place and show state, and what is still to send, are kept in step by a
    # cycle: `refresh` reads the show state, sends any change still waiting (`apply`),
    # then reads the place back. `settle` at the end of an animation sets `_verify`,
    # so the next `apply` reads back once more and reports a place the guest did not
    # give. `apply` clears `_verify` on every way out.

    def refresh(self) -> None:
        """Read the real show state and place back, after sending any change still waiting."""
        if self.hwnd is None or self.scene is None:
            return
        self.show = win32.show_state(self.guest, self.hwnd)
        if self._sent is not None:
            self.apply()
        if self.show == "minimized":
            return  # Windows parks an iconic window off the desktop: the last place stands
        rect = window_rect(self.guest, self.hwnd)
        if rect is None:
            return  # the window has gone, so the last known place stands
        left, top, right, bottom = rect
        self.set_rect(left, top, right - left, bottom - top)
        self._sent = self._placement()

    def _placement(self) -> tuple[int, int, int, int]:
        """The place as the guest takes it: x, y, width and height."""
        return (*self.rect[:2], *self.size)

    def repaint_if_moved(self) -> None:
        """Read the place back and repaint if the guest moved the window itself (a Drag)."""
        before = self._placement()
        self.refresh()
        if self.hwnd is not None and self._placement() != before:
            # Dragging leaves stale icon-label pixels in the frame: experiments/host-checks/README.md (dev branch) (M7b)
            win32.repaint_window(self.guest, self.hwnd)

    def point(self) -> Vec:
        self.refresh()
        return self.get_center()

    def apply(self) -> None:
        verify, self._verify = self._verify, False  # consumed on every way out
        self._send()
        if verify and self.hwnd is not None:
            self._read_back()

    def _send(self) -> None:
        wanted = self._placement()
        if wanted == self._sent or self.hwnd is None or self.show == "minimized":
            return  # an iconic window keeps its change until it is restored
        if self.show == "normal":  # a maximised rect overhangs the screen by the frame
            size = self.space.check_rect(
                self._name(),
                self.rect,
                self.size,
                self._sizable and self._top_level,
                self._warned,
            )
            if size != self.size:
                self.width, self.height = size
                wanted = self._placement()
                if wanted == self._sent:
                    return
        x, y, width, height = wanted
        flags = win32.SWP_NOZORDER | win32.SWP_NOACTIVATE
        if self._sent and (width, height) == self._sent[2:]:
            flags |= win32.SWP_NOSIZE
        if self._sent and (x, y) == self._sent[:2]:
            flags |= win32.SWP_NOMOVE
        # Moves leave stale pixels on the frame, so place_window repaints it.
        win32.place_window(self.guest, self.hwnd, x, y, width, height, flags)
        self._sent = wanted

    def _name(self) -> str:
        return f"{type(self).__name__}({self.title_text or self.class_name or hex(self.hwnd or 0)})"

    def settle(self) -> None:
        """Read the window back once and report a place the guest did not give.

        Run at the end of an animation, not per frame. A change still waiting
        (the finish hook runs before the frame's apply) is read back right after
        it is sent, so the frame costs no extra SetWindowPos. A window that is
        maximized or minimized has no place to check.
        """
        if self.hwnd is None or self.scene is None or self.show != "normal":
            return
        if self._placement() != self._sent:
            self._verify = True  # `apply` reads back once it has sent
        else:
            self._read_back()

    def _read_back(self) -> None:
        """What the host asked for is taken before the read, so a clamp only
        Windows knew about (an app's own minimum size) shows as requested
        against given, and the prop then holds what the guest has."""
        wanted = self.rect
        self.refresh()
        if self.rect != wanted:
            self.space.report(
                self._name(),
                "settle",
                f"asked for {wanted}, the guest gave {self.rect}",
                self._warned,
            )

    # ---- window commands ----

    def maximize(self) -> None:
        """Fill the desktop with the window, as its maximize button does."""
        self._system_command(win32.SC_MAXIMIZE)

    def minimize(self) -> None:
        """Send the window to the taskbar."""
        self._system_command(win32.SC_MINIMIZE)

    def restore(self) -> None:
        """Bring a minimized or maximized window back to its normal size."""
        self._system_command(win32.SC_RESTORE)

    def _system_command(self, command: int) -> None:
        win32.sys_command(self.guest, self.hwnd, command)
        self.guest.sync(self.hwnd)
        self.refresh()  # Windows moved and sized the window itself

    # ---- places to aim at ----

    @property
    def title_bar(self) -> Spot:
        """The middle of the title bar, which is where a window is dragged from."""

        def locate():
            self.refresh()
            return self.x + self.width / 2, self.y + self.space.title_bar_offset

        return Spot(locate, window=self)  # a Drag from here moves this window

    def corner(self, direction=DR, inset: int = DEFAULT_INSET) -> Spot:
        """A corner or edge of the frame, which is where a window is resized from.

        `inset` is how many pixels inside the frame the spot sits, so the press
        lands on the frame's grab area rather than just outside the window.
        """

        def locate():
            self.refresh()
            return self.get_corner(direction) - Vec(direction[0], direction[1]) * inset

        # A Drag from here resizes this window, so it is read back while Windows does it.
        return Spot(locate, window=self)

    def client(self, x: int, y: int) -> Spot:
        """A point given in the window's client coordinates."""
        return Spot(lambda: win32.client_to_screen(self.guest, self.hwnd, x, y))

    # ---- the live window ----

    def client_size(self) -> tuple[int, int]:
        return win32.client_size(self.guest, self.hwnd)

    def children(
        self, class_name: str | None = None, title: str | None = None
    ) -> list[Window]:
        """Every descendant window, optionally by class and title."""
        return [
            window
            for window in self.guest.windows(self.hwnd)
            if window.matches(title, class_name)
        ]

    def control(self, class_name: str | None = None, title: str | None = None) -> Win:
        """A child window as a prop, for clicking or moving.

        A control is not on the scene's list, so a revert does not reach it:
        ask for it again after one.
        """
        found = self.children(class_name, title)
        if not found:
            what = " ".join(filter(None, (class_name or "child", title)))
            raise NotFound(f"no {what} in window {self.hwnd:#x}")
        child = Win._child(found[0].hwnd)
        child.attach(self.scene)
        return child

    @staticmethod
    def _child(hwnd: int) -> Win:
        child = Win(hwnd=hwnd)
        child._top_level = False  # no size checks, settle or ready for a control
        return child

    @property
    def text(self) -> str:
        """The window's text: a title bar, or an Edit control's contents (see `edit_hwnd`)."""
        return self._read_text(self.edit_hwnd)

    def _read_text(self, hwnd: int) -> str:
        length = self.guest.user32.SendMessageA(
            hwnd, win32.WM_GETTEXTLENGTH, 0, 0
        ).value
        result = self.guest.user32.SendMessageA(
            hwnd, win32.WM_GETTEXT, length + 1, Out(length + 1)
        )
        return result.buffers[0][: result.value].decode("cp1252", errors="replace")

    def set_text(self, text: str) -> None:
        self.guest.user32.SendMessageA(self.edit_hwnd, win32.WM_SETTEXT, 0, text)

    @property
    def edit_hwnd(self) -> int:
        """Where typing goes. Apps with an inner editor override this."""
        return self.hwnd

    def activate(self) -> None:
        """Make this the foreground window, so the real keyboard reaches it.

        SetForegroundWindow fails when the agent calls it, so this clicks the
        title bar, between frames, and puts the pointer back.
        """
        user32 = self.guest.user32
        if user32.GetForegroundWindow().value == self.hwnd:
            return
        pointer = self.scene.pointer
        pointer.refresh()
        home = pointer.get_center()
        pointer.place(self.title_bar)
        pointer.press()
        pointer.release()
        self.guest.sync()
        pointer.place(home)
        if user32.GetForegroundWindow().value != self.hwnd:
            raise GuestError(
                f"could not activate window {self.hwnd:#x}: is it covered?"
            )

    def bring_to_front(self) -> None:
        """Raise the window without activating it."""
        self.guest.user32.SetWindowPos(
            self.hwnd,
            win32.HWND_TOP,
            0,
            0,
            0,
            0,
            win32.SWP_NOMOVE | win32.SWP_NOSIZE | win32.SWP_NOACTIVATE,
        )

    def close(self) -> None:
        """Ask the window to close, as its X button does."""
        win32.close_window(self.guest, self.hwnd)

    def remove(self) -> None:
        self.close()

    def forget(self) -> None:
        # The window went with the revert. Adding the prop again opens a new one.
        super().forget()
        self.hwnd, self._sent, self.show = None, None, "normal"
        self.closing, self._identity = False, None
