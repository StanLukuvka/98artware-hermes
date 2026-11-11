"""The host end of the link to the guest agent."""

from __future__ import annotations

import socket
import struct
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from itertools import count
from pathlib import Path
from typing import Self

from .protocol import (
    PROTOCOL_VERSION,
    FrameError,
    Out,
    encode_request,
    parse_values,
    unframe,
)
from .win32 import RDW_REPAINT_ALL, SWP_NOSIZE, SWP_NOZORDER

DEFAULT_TIMEOUT = 10.0
CHUNK_SIZE = 4096

ENUM_SETTINGS_END_OF_LIST = 0
CDS_UPDATEREGISTRY = 1
DEVMODE_SIZE = 156
DM_BITSPERPEL = 0x00040000
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000


class BridgeError(RuntimeError):
    """The guest refused a request, or the link went away."""


class BridgeTimeout(BridgeError):
    pass


class GuestCrashed(BridgeError):
    """The agent crashed while a request was open. It restarts by itself."""


@dataclass
class CallResult:
    value: int
    last_error: int
    buffers: list[bytes]

    @property
    def signed(self) -> int:
        return self.value - (1 << 32) if self.value & 0x80000000 else self.value

    def __bool__(self) -> bool:
        return self.value != 0

    def __int__(self) -> int:
        return self.value

    def __index__(self) -> int:
        return self.value


@dataclass
class Window:
    hwnd: int
    pid: int
    visible: bool
    rect: tuple[int, int, int, int]
    class_name: str
    title: str

    @classmethod
    def from_row(cls, row: list) -> Window:
        hwnd, pid, visible, left, top, right, bottom, class_name, title = row
        rect = tuple(
            v - (1 << 32) if v & 0x80000000 else v for v in (left, top, right, bottom)
        )
        return cls(hwnd, pid, bool(visible), rect, class_name, title)


@dataclass
class _Pending:
    rows: list[list] = field(default_factory=list)
    values: list | None = None
    error: BridgeError | None = None


class AsyncCall:
    """A guest call that runs on its own thread, such as a modal dialog."""

    def __init__(self, bridge: Bridge, request_id: int):
        self._bridge = bridge
        self.id = request_id

    @property
    def done(self) -> bool:
        return self._bridge._peek_done(self.id)

    def wait(self, timeout: float | None = None) -> CallResult:
        return self._bridge._wait_done(self.id, timeout)


class _Dll:
    """Lets `bridge.user32.SetCursorPos(10, 20)` read like a local call."""

    def __init__(self, bridge: Bridge, name: str):
        self._bridge = bridge
        self._name = name

    def __getattr__(self, function: str):
        def call(*args, timeout: float | None = None) -> CallResult:
            return self._bridge.call(self._name, function, *args, timeout=timeout)

        call.__name__ = function
        return call


class Bridge:
    """Requests to the agent. Safe to use from several threads."""

    def __init__(self, sock: socket.socket, timeout: float = DEFAULT_TIMEOUT):
        self._sock = sock
        self.timeout = timeout
        self._ids = count(1)
        self._cond = threading.Condition()
        self._write_lock = threading.Lock()
        self._pending: dict[int, _Pending] = {}
        self._events: deque[tuple[str, list]] = deque(maxlen=256)
        self._done: dict[int, CallResult] = {}
        self._async_open: set[int] = set()
        self._abandoned: dict[int, BridgeError] = {}
        self._closed: BridgeError | None = None
        self.kernel32 = _Dll(self, "kernel32")
        self.user32 = _Dll(self, "user32")
        self.gdi32 = _Dll(self, "gdi32")
        self.shell32 = _Dll(self, "shell32")
        self.advapi32 = _Dll(self, "advapi32")
        self.winmm = _Dll(self, "winmm")
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    @classmethod
    def connect(cls, path: Path | str, timeout: float = DEFAULT_TIMEOUT) -> Bridge:
        sock = socket.socket(socket.AF_UNIX)
        sock.connect(str(path))
        return cls(sock, timeout)

    def dll(self, name: str) -> _Dll:
        return _Dll(self, name)

    def close(self) -> None:
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self._sock.close()
        self._reader.join(2)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- incoming ----

    def _read_loop(self) -> None:
        buffer = b""
        try:
            while chunk := self._sock.recv(65536):
                buffer += chunk
                *lines, buffer = buffer.split(b"\n")
                for line in lines:
                    self._handle_line(line)
        except OSError:
            pass
        self._fail_all(BridgeError("link to the guest closed"), closing=True)

    def _handle_line(self, line: bytes) -> None:
        try:
            payload = unframe(line)
        except FrameError:
            return  # the open request will time out and report it
        head, _, rest = payload.partition(b" ")
        if head == b"!":
            name, _, rest = rest.partition(b" ")
            self._handle_event(name.decode("ascii"), parse_values(rest))
            return
        tag, _, rest = rest.partition(b" ")
        request_id = int(head)
        with self._cond:
            pending = self._pending.get(request_id)
            if pending is None:
                return
            if tag == b"+":
                pending.rows.append(parse_values(rest))
            elif tag == b"OK":
                pending.values = parse_values(rest)
            else:
                code, _, text = rest.partition(b" ")
                detail = parse_values(text)
                pending.error = BridgeError(
                    f"{code.decode()}: {detail[0] if detail else ''}"
                )
            self._cond.notify_all()

    def _handle_event(self, name: str, values: list) -> None:
        if name == "DONE":
            request_id, value, last_error, *buffers = values
            with self._cond:
                if request_id in self._async_open:
                    self._done[request_id] = CallResult(value, last_error, buffers)
                    self._cond.notify_all()
            return
        if name == "CRASH":
            code, address = values
            self._fail_all(
                GuestCrashed(f"agent crashed: code {code:#x} at {address:#x}")
            )
        with self._cond:
            self._events.append((name, values))
            self._cond.notify_all()

    def _fail_all(self, error: BridgeError, closing: bool = False) -> None:
        with self._cond:
            for pending in self._pending.values():
                if pending.values is None and pending.error is None:
                    pending.error = error
            if closing:
                self._closed = error
            self._cond.notify_all()

    def wait_event(self, name: str, timeout: float | None = None) -> list:
        """Take the oldest unread event called name, waiting for one if needed."""

        def take():
            for item in self._events:
                if item[0] == name:
                    self._events.remove(item)
                    return item
            return None

        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        with self._cond:
            while True:
                item = take()
                if item:
                    return item[1]
                if self._closed:
                    raise self._closed
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BridgeTimeout(f"no {name} event from the guest")
                self._cond.wait(remaining)

    def clear_events(self) -> None:
        with self._cond:
            self._events.clear()

    def abandon(self, reason: str) -> None:
        """Fail every open request and async call now, and drop unread events.

        For when the guest has been reverted: whatever was running there no
        longer exists, so nothing should wait for its timeout.
        """
        error = BridgeError(reason)
        self._fail_all(error)
        with self._cond:
            for request_id in self._async_open - self._done.keys():
                self._abandoned[request_id] = error
            self._async_open -= self._abandoned.keys()
            self._events.clear()
            self._cond.notify_all()

    def _peek_done(self, request_id: int) -> bool:
        with self._cond:
            return request_id in self._done or request_id in self._abandoned

    def _wait_done(self, request_id: int, timeout: float | None) -> CallResult:
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
        with self._cond:
            while request_id not in self._done:
                if request_id in self._abandoned:
                    raise self._abandoned.pop(request_id)
                if self._closed:
                    raise self._closed
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BridgeTimeout(f"async call {request_id} did not finish")
                self._cond.wait(remaining)
            self._async_open.discard(request_id)
            return self._done.pop(request_id)

    # ---- outgoing ----

    def request(
        self, verb: str, *args, timeout: float | None = None
    ) -> tuple[list, list[list]]:
        """Send one request and return its final values and any rows."""
        return self._send(next(self._ids), verb, *args, timeout=timeout)

    def _send(
        self, request_id: int, verb: str, *args, timeout: float | None = None
    ) -> tuple[list, list[list]]:
        pending = _Pending()
        with self._cond:
            if self._closed:
                raise self._closed
            self._pending[request_id] = pending
        try:
            with self._write_lock:
                try:
                    self._sock.sendall(encode_request(request_id, verb, args))
                except OSError as error:
                    raise BridgeError(f"link to the guest closed: {error}") from None
            deadline = time.monotonic() + (self.timeout if timeout is None else timeout)
            with self._cond:
                while pending.values is None and pending.error is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise BridgeTimeout(f"no reply to {verb}")
                    self._cond.wait(remaining)
        finally:
            with self._cond:
                self._pending.pop(request_id, None)
        if pending.error:
            raise pending.error
        return pending.values, pending.rows

    def call(
        self, dll: str, function: str, *args, timeout: float | None = None
    ) -> CallResult:
        """Call a Win32 function on the guest's main thread."""
        values, _ = self.request("CALL", dll, function, *args, timeout=timeout)
        value, last_error, *buffers = values
        return CallResult(value, last_error, buffers)

    def acall(self, dll: str, function: str, *args) -> AsyncCall:
        """Call on a new guest thread. Use for anything that may block."""
        with self._cond:
            # Registered before the request goes out, so its DONE can't arrive first.
            request_id = next(self._ids)
            self._async_open.add(request_id)
        try:
            self._send(request_id, "ACALL", dll, function, *args)
        except BaseException:
            with self._cond:
                self._async_open.discard(request_id)
            raise
        return AsyncCall(self, request_id)

    def ping(self, timeout: float | None = None) -> int:
        """Return the guest's tick count."""
        return self.request("PING", timeout=timeout)[0][0]

    def wait_ready(self, timeout: float = 60.0) -> None:
        deadline = time.monotonic() + timeout
        while True:
            try:
                values, _ = self.request(
                    "PING", timeout=min(2.0, max(0.1, deadline - time.monotonic()))
                )
            except BridgeTimeout:
                if time.monotonic() >= deadline:
                    raise
                continue
            if values[1] != PROTOCOL_VERSION:
                raise BridgeError(
                    f"guest speaks protocol {values[1]}, host {PROTOCOL_VERSION}"
                )
            return

    def windows(self, parent: int = 0) -> list[Window]:
        return [Window.from_row(row) for row in self.request("WINDOWS", parent)[1]]

    def find_window(
        self,
        title: str | None = None,
        class_name: str | None = None,
        parent: int = 0,
        timeout: float = 10.0,
    ) -> Window:
        """Wait for a visible window. Titles match on a substring."""
        deadline = time.monotonic() + timeout
        while True:
            for window in self.windows(parent):
                if (
                    window.visible
                    and (title is None or title.lower() in window.title.lower())
                    and (
                        class_name is None
                        or class_name.lower() == window.class_name.lower()
                    )
                ):
                    return window
            if time.monotonic() >= deadline:
                raise BridgeTimeout(f"no window matching {title=} {class_name=}")
            time.sleep(0.1)

    def processes(self) -> list[tuple[int, str]]:
        return [(row[0], row[1]) for row in self.request("PROCS")[1]]

    def spawn(self, command: str, cwd: str | None = None) -> int:
        """Start a program and return its process id."""
        args = (command,) if cwd is None else (command, cwd)
        return self.request("SPAWN", *args)[0][0]

    def sync(self, hwnd: int = 0, timeout_ms: int = 500) -> int:
        """Wait until windows have repainted. Returns the guest's tick count."""
        return self.request("SYNC", hwnd, timeout_ms)[0][0]

    def put_file(self, path: str, data: bytes) -> None:
        """Write a file on the guest, at about 100 to 190 KB/s on a quiet host.

        Fails if the guest wrote less than it was sent, as it does when its
        disk is full.
        """
        for offset in range(0, max(len(data), 1), CHUNK_SIZE):
            chunk = data[offset : offset + CHUNK_SIZE]
            written = self.request("PUT", path, offset, chunk)[0][0]
            if written != len(chunk):
                raise BridgeError(
                    f"guest wrote {written} of {len(chunk)} bytes to {path} at offset {offset}"
                )

    def get_file(self, path: str) -> bytes:
        data = bytearray()
        while True:
            (size, chunk), _ = self.request("GET", path, len(data), CHUNK_SIZE)
            data += chunk
            if not chunk or len(data) >= size:
                return bytes(data)

    def move_window(self, hwnd: int, x: int, y: int) -> None:
        """Move a window, keeping its size and stacking order.

        Moves on Windows 98 leave stale pixels along the window frame, and
        SWP_NOCOPYBITS and SWP_FRAMECHANGED don't clear them, so it is repainted.
        """
        self.user32.SetWindowPos(hwnd, 0, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER)
        self.user32.RedrawWindow(hwnd, 0, 0, RDW_REPAINT_ALL)

    def set_display_mode(
        self, width: int, height: int, depth: int, persist: bool = True
    ) -> None:
        """Switch resolution, using a mode the video driver itself reports."""
        mode = 0
        while True:
            blank = bytearray(DEVMODE_SIZE)
            struct.pack_into("<H", blank, 36, DEVMODE_SIZE)
            result = self.user32.EnumDisplaySettingsA(None, mode, blank)
            if result.value == ENUM_SETTINGS_END_OF_LIST:
                raise BridgeError(f"guest has no {width}x{height}x{depth} display mode")
            devmode = bytearray(result.buffers[0])
            bpp, w, h = struct.unpack_from("<III", devmode, 104)
            if (w, h, bpp) == (width, height, depth):
                break
            mode += 1
        struct.pack_into(
            "<I", devmode, 40, DM_BITSPERPEL | DM_PELSWIDTH | DM_PELSHEIGHT
        )
        flags = CDS_UPDATEREGISTRY if persist else 0
        result = self.user32.ChangeDisplaySettingsA(devmode, flags)
        if result.signed < 0:
            raise BridgeError(f"ChangeDisplaySettings failed with {result.signed}")


__all__ = [
    "AsyncCall",
    "Bridge",
    "BridgeError",
    "BridgeTimeout",
    "CallResult",
    "GuestCrashed",
    "Out",
    "Window",
]
