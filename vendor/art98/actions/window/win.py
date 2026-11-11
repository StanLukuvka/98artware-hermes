"""Win: a real top-level window, or a real control inside one, as a prop."""

from __future__ import annotations

import struct
import time

from ... import win32
from ...base.props import Prop, Spot
from ...base.vectors import DR, Vec
from ...bridge import BridgeTimeout, Window
from ...protocol import Out
from ...win32 import (
    GWL_STYLE,
    SWP_NOACTIVATE,
    SWP_NOMOVE,
    SWP_NOSIZE,
    SWP_NOZORDER,
    WM_GETTEXT,
    WM_GETTEXTLENGTH,
    WM_SETTEXT,
    WS_THICKFRAME,
    window_rect,
)

FIND_WINDOW_TIMEOUT = 15.0


class Win(Prop):
    """A real top-level window, or a real control inside one.

    Give it a `command` to start a program, or `title`, `class_name` or `hwnd`
    to take hold of a window that exists. `at` is the top left corner and
    `size` the outer size. Both are applied when the prop is added.
    """

    command: str | None = None
    window_class: str | None = None

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
        self._at, self._size = at, size
        self._sent: tuple[int, int, int, int] | None = None
        self._top_level = True  # control() sets False: no size checks on children
        self._sizable = False  # WS_THICKFRAME, read at attach
        self._maximized = False
        self._verify = False  # settle is waiting for the next apply
        self._warned: set[str] = set()

    def attach(self, scene) -> None:
        super().attach(scene)
        if self.hwnd is None:
            pid = self.guest.spawn(self.command) if self.command else None
            self.hwnd = self._find(pid).hwnd
        if self._top_level:
            style = self.guest.user32.GetWindowLongA(self.hwnd, GWL_STYLE & 0xFFFFFFFF)
            self._sizable = bool(style.value & WS_THICKFRAME)
        self.refresh()
        if self._size:
            self.width, self.height = self._size
        if self._at:
            self.x, self.y = self._at
        self.apply()
        self.settle()
        self.ready()

    def _find(self, pid: int | None) -> Window:
        deadline = time.monotonic() + FIND_WINDOW_TIMEOUT
        while True:
            window = self._look(pid)
            if window is not None:
                return window
            if time.monotonic() >= deadline:
                raise BridgeTimeout(self._not_found(pid))
            time.sleep(0.05)

    def _look(self, pid: int | None) -> Window | None:
        """One look at the guest's windows: the one wanted, or None. A subclass
        can raise here to fail early."""
        for window in self.guest.windows():
            if (
                window.visible
                and (pid is None or window.pid == pid)
                and (
                    self.title_text is None
                    or self.title_text.lower() in window.title.lower()
                )
                and (
                    self.class_name is None
                    or self.class_name.lower() == window.class_name.lower()
                )
            ):
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

    def refresh(self) -> None:
        """Read the real place back, after sending any change still waiting."""
        if self.hwnd is None or self.scene is None:
            return
        if self._sent is not None:
            self.apply()
        rect = window_rect(self.guest, self.hwnd)
        if rect is None:
            return  # the window has gone, so the last known place stands
        left, top, right, bottom = rect
        self.set_rect(left, top, right - left, bottom - top)
        self._sent = self.rect[0], self.rect[1], *self.size

    def repaint_if_moved(self) -> None:
        """Read the place back and repaint if the guest moved the window itself (a Drag)."""
        before = (*self.rect[:2], *self.size)
        self.refresh()
        if self.hwnd is not None and (*self.rect[:2], *self.size) != before:
            # Dragging leaves stale icon-label pixels in the frame: experiments/host-checks/README.md (M7b)
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
        wanted = (*self.rect[:2], *self.size)
        if wanted == self._sent or self.hwnd is None:
            return
        if not self._maximized:  # a maximised rect overhangs the screen by the frame
            size = self.space.check_rect(
                self._name(),
                self.rect,
                self.size,
                self._sizable and self._top_level,
                self._warned,
            )
            if size != self.size:
                self.width, self.height = size
                wanted = (*self.rect[:2], *self.size)
                if wanted == self._sent:
                    return
        x, y, width, height = wanted
        flags = SWP_NOZORDER | SWP_NOACTIVATE
        if self._sent and (width, height) == self._sent[2:]:
            flags |= SWP_NOSIZE
        if self._sent and (x, y) == self._sent[:2]:
            flags |= SWP_NOMOVE
        # Moves leave stale pixels on the frame, so it is repainted (Bridge.move_window).
        win32.place_window(self.guest, self.hwnd, x, y, width, height, flags)
        self._sent = wanted

    def _name(self) -> str:
        return f"{type(self).__name__}({self.title_text or self.class_name or hex(self.hwnd or 0)})"

    def settle(self) -> None:
        """Read the window back once and report a place the guest did not give.

        Run at the end of an animation, not per frame. A change still waiting
        (the finish hook runs before the frame's apply) is read back right after
        it is sent, so the frame costs no extra SetWindowPos.
        """
        if self.hwnd is None or self.scene is None or self._maximized:
            return
        if (*self.rect[:2], *self.size) != self._sent:
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

    # ---- places to aim at ----

    @property
    def title_bar(self) -> Spot:
        """The middle of the title bar, which is where a window is dragged from."""

        def locate():
            self.refresh()
            return self.x + self.width / 2, self.y + self.space.title_bar_offset

        spot = Spot(locate)
        spot.window = self  # a Drag from here knows which window it moves
        return spot

    def corner(self, direction=DR, inset: int = 2) -> Spot:
        """A corner or edge of the frame, which is where a window is resized from."""

        def locate():
            self.refresh()
            return self.get_corner(direction) - Vec(direction[0], direction[1]) * inset

        spot = Spot(locate)
        spot.window = self  # a Drag from here resizes this window, so it is read back while Windows does it
        return spot

    def client(self, x: int, y: int) -> Spot:
        """A point given in the window's client coordinates."""

        def locate():
            point = struct.pack("<ii", x, y)
            return struct.unpack(
                "<ii", self.guest.user32.ClientToScreen(self.hwnd, point).buffers[0]
            )

        return Spot(locate)

    # ---- the live window ----

    def client_size(self) -> tuple[int, int]:
        rect = self.guest.user32.GetClientRect(self.hwnd, Out(16)).buffers[0]
        return struct.unpack("<iiii", rect)[2:]

    def children(
        self, class_name: str | None = None, title: str | None = None
    ) -> list[Window]:
        """Every descendant window, optionally by class and title."""
        return [
            window
            for window in self.guest.windows(self.hwnd)
            if (class_name is None or class_name.lower() == window.class_name.lower())
            and (title is None or title.lower() in window.title.lower())
        ]

    def control(self, class_name: str | None = None, title: str | None = None) -> Win:
        """A child window as a prop, for clicking or moving."""
        found = self.children(class_name, title)
        if not found:
            raise LookupError(
                f"no {class_name or 'child'} {title or ''} in window {self.hwnd:#x}"
            )
        child = Win(hwnd=found[0].hwnd)
        child._top_level = False
        child.attach(self.scene)
        return child

    @property
    def text(self) -> str:
        """The window's text: a title bar, or an Edit control's contents."""
        return self._read_text(self.hwnd)

    def _read_text(self, hwnd: int) -> str:
        length = self.guest.user32.SendMessageA(hwnd, WM_GETTEXTLENGTH, 0, 0).value
        result = self.guest.user32.SendMessageA(
            hwnd, WM_GETTEXT, length + 1, Out(length + 1)
        )
        return result.buffers[0][: result.value].decode("cp1252", errors="replace")

    def set_text(self, text: str) -> None:
        self.guest.user32.SendMessageA(self.hwnd, WM_SETTEXT, 0, text)

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
            raise RuntimeError(
                f"could not activate window {self.hwnd:#x}: is it covered?"
            )

    def bring_to_front(self) -> None:
        """Raise the window without activating it."""
        self.guest.user32.SetWindowPos(
            self.hwnd, 0, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
        )

    def close(self) -> None:
        """Ask the window to close, as its X button does."""
        win32.close_window(self.guest, self.hwnd)

    def remove(self) -> None:
        self.close()

    def forget(self) -> None:
        # The window went with the revert. Adding the prop again opens a new one.
        super().forget()
        self.hwnd, self._sent, self._maximized = None, None, False
