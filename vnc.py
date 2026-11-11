"""A view-only VNC reader: keeps the guest screen in memory for the dashboard's live view.

`art98 vm start --vnc` shows the VM's screen over VNC on the host's loopback (pasta forwards it
into this container). This module speaks just enough RFB 3.8 to watch it: no password (art98's
VNC has none, so it must stay on loopback), raw pixels, and DesktopSize for mode changes. It
never sends a key or a pointer event, so watching can't change what the VM does; it only asks
for screen updates. Unlike a screenshot through the control API, it needs no VM lock, so it
keeps working while a render runs.

One background thread per process connects on first use, keeps the latest frame, and drops
the connection after IDLE_S without anyone asking for a frame.

No imports from the rest of the plugin, so dashboard/plugin_api.py can load it by path.
"""

from __future__ import annotations

import os
import socket
import struct
import threading
import time
import zlib

ADDRESS = os.environ.get("ART98_VNC", "127.0.0.1:5900")
IDLE_S = 30.0          # stop watching when nobody has asked for a frame this long
MAX_FPS = 15           # how often to ask the server for an update
CONNECT_TIMEOUT = 3.0
RETRY_S = 5.0          # after a failed connect, wait this long before trying again
ENC_RAW, ENC_DESKTOP_SIZE = 0, -223


class VNCError(RuntimeError):
    pass


def _recv(sock: socket.socket, n: int) -> bytes:
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise VNCError("the VNC server closed the connection")
        buf += chunk
    return bytes(buf)


class Watcher:
    def __init__(self, address: str = ADDRESS):
        host, _, port = address.rpartition(":")
        self.address = (host or "127.0.0.1", int(port))
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._wanted = 0.0          # last time someone asked for a frame
        self._failed_at = 0.0
        self.error: str | None = None
        self.width = self.height = 0
        self._fb = bytearray()      # RGB, row-major
        self.version = 0            # bumps on every screen change
        self._png: tuple[int, bytes] | None = None

    # -- public --------------------------------------------------------------------
    def frame(self, since: int | None = None) -> tuple[int, bytes | None] | None:
        """(version, PNG) of the current screen; PNG is None if it hasn't changed since `since`.
        None when VNC isn't available (not started with --vnc, or no frame yet)."""
        self._wanted = time.monotonic()
        self._ensure_running()
        with self._lock:
            if not self.version:
                return None
            version = self.version
            if since is not None and since == version:
                return version, None
            if self._png and self._png[0] == version:
                return self._png
            w, h, fb = self.width, self.height, bytes(self._fb)
        png = _png(w, h, fb)
        with self._lock:
            self._png = (version, png)
        return version, png

    # -- the thread ----------------------------------------------------------------
    def _ensure_running(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            if time.monotonic() - self._failed_at < RETRY_S:
                return
            self._thread = threading.Thread(target=self._run, name="art98-vnc", daemon=True)
            self._thread.start()

    def _run(self) -> None:
        try:
            with socket.create_connection(self.address, timeout=CONNECT_TIMEOUT) as sock:
                sock.settimeout(10.0)
                self._handshake(sock)
                self.error = None
                self._loop(sock)
        except (OSError, VNCError, struct.error) as e:
            self.error = str(e) or type(e).__name__
            self._failed_at = time.monotonic()
        with self._lock:
            self.version = 0  # no stale frame once the connection is gone
            self._png = None

    def _handshake(self, sock: socket.socket) -> None:
        if not _recv(sock, 12).startswith(b"RFB 003."):
            raise VNCError("not a VNC server")
        sock.sendall(b"RFB 003.008\n")
        count = _recv(sock, 1)[0]
        if count == 0:
            (n,) = struct.unpack(">I", _recv(sock, 4))
            raise VNCError(_recv(sock, n).decode(errors="replace"))
        if 1 not in _recv(sock, count):
            raise VNCError("the VNC server wants a password; art98's VNC should have none")
        sock.sendall(b"\x01")  # security type None
        if struct.unpack(">I", _recv(sock, 4))[0] != 0:
            raise VNCError("the VNC server refused the connection")
        sock.sendall(b"\x01")  # ClientInit: shared, so other viewers stay connected
        w, h = struct.unpack(">HH", _recv(sock, 4))
        _recv(sock, 16)        # the server's pixel format; ours replaces it
        (name_len,) = struct.unpack(">I", _recv(sock, 4))
        _recv(sock, name_len)
        self._resize(w, h)
        # 32 bpp, little-endian, true colour, red at bit 16: bytes arrive as B, G, R, X.
        sock.sendall(struct.pack(">BxxxBBBBHHHBBBxxx", 0, 32, 24, 0, 1, 255, 255, 255, 16, 8, 0))
        sock.sendall(struct.pack(">BxH", 2, 2) + struct.pack(">ii", ENC_RAW, ENC_DESKTOP_SIZE))
        sock.sendall(struct.pack(">BBHHHH", 3, 0, 0, 0, w, h))  # one full frame first

    def _loop(self, sock: socket.socket) -> None:
        while time.monotonic() - self._wanted < IDLE_S:
            kind = _recv(sock, 1)[0]
            if kind == 0:                       # FramebufferUpdate
                _, rects = struct.unpack(">BH", _recv(sock, 3))
                for _ in range(rects):
                    x, y, w, h, enc = struct.unpack(">HHHHi", _recv(sock, 12))
                    if enc == ENC_RAW:
                        self._blit(x, y, w, h, _recv(sock, w * h * 4))
                    elif enc == ENC_DESKTOP_SIZE:
                        self._resize(w, h)
                    else:
                        raise VNCError(f"unexpected encoding {enc}")
                time.sleep(1 / MAX_FPS)
                sock.sendall(struct.pack(">BBHHHH", 3, 1, 0, 0, self.width, self.height))
            elif kind == 1:                     # SetColourMapEntries (not used in true colour)
                _, _, n = struct.unpack(">BHH", _recv(sock, 5))
                _recv(sock, n * 6)
            elif kind == 2:                     # Bell
                pass
            elif kind == 3:                     # ServerCutText
                _recv(sock, 3)
                (n,) = struct.unpack(">I", _recv(sock, 4))
                _recv(sock, n)
            else:
                raise VNCError(f"unexpected server message {kind}")

    def _resize(self, w: int, h: int) -> None:
        with self._lock:
            self.width, self.height = w, h
            self._fb = bytearray(w * h * 3)
            self.version += 1

    def _blit(self, x: int, y: int, w: int, h: int, bgrx: bytes) -> None:
        row = bytearray(w * 3)
        with self._lock:
            stride = self.width * 3
            for r in range(h):
                if y + r >= self.height:
                    break
                src = bgrx[r * w * 4:(r + 1) * w * 4]
                row[0::3], row[1::3], row[2::3] = src[2::4], src[1::4], src[0::4]
                at = (y + r) * stride + x * 3
                self._fb[at:at + w * 3] = row
            self.version += 1


def _png(w: int, h: int, rgb: bytes) -> bytes:
    raw = b"".join(b"\x00" + rgb[y * w * 3:(y + 1) * w * 3] for y in range(h))
    chunk = lambda tag, data: (struct.pack(">I", len(data)) + tag + data
                               + struct.pack(">I", zlib.crc32(tag + data)))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 1)) + chunk(b"IEND", b""))


try:
    from plugins.plugin_utils import lazy_singleton
except ImportError:  # outside Hermes (python3 vnc.py): a plain cache is enough
    from functools import lru_cache as _cache

    def lazy_singleton(factory):
        return _cache(maxsize=None)(factory)


@lazy_singleton
def watcher() -> Watcher:
    """The one Watcher for this process; safe when several requests ask at once."""
    return Watcher()


if __name__ == "__main__":  # python3 vnc.py out.png: one frame, to check the connection
    import sys
    w = watcher()
    for _ in range(50):
        got = w.frame()
        if got:
            open(sys.argv[1] if len(sys.argv) > 1 else "vnc.png", "wb").write(got[1])
            print("frame", w.width, "x", w.height, "version", got[0])
            break
        time.sleep(0.1)
    else:
        print("no frame:", w.error)
