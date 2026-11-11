"""The container end of a stage: QEMU, its run copy, and the link to the host.

`art98 serve` runs inside the container. It boots the `ready` snapshot on a
copy-on-write copy of the disk, then listens on one TCP port, or on a Unix
socket in a folder the host mounts, which is faster on Linux. Every connection
starts with two lines, the token and the channel name, and gets `ok` back:

- `agent` relays bytes both ways between the connection and the guest's COM1,
  untouched, so the host's Bridge speaks the agent protocol as it always has.
  It needs a control connection to be open.
- `control` takes one JSON request per line and answers each with one JSON
  line, followed by raw bytes for a screenshot. `insert_disc` is sent with a
  `size` and that many raw bytes after its line (an ISO, at most 64 MiB);
  `eject_disc` takes none. Other ops: send_keys, revert (ejects the disc
  first), pause, resume. On a Unix socket a screenshot can instead be written
  into the mounted folder, so no frame crosses the socket.
- `status` answers one JSON line and closes. It takes no lock and asks QEMU
  nothing: it reports server state only.

One control connection at a time owns the stage; others are refused as busy.
When the owner goes away the stage is cleaned up (relay dropped, disc ejected,
VM paused) and only then offered to the next one. `serve` keeps running until
it is stopped or QEMU dies.
"""

from __future__ import annotations

import hmac
import json
import os
import signal
import socket
import tempfile
import threading
import time
from pathlib import Path

from art98.settings import CONTAINER_CONTROL_PORT, CONTAINER_VNC_PORT

PORT = CONTAINER_CONTROL_PORT
VNC_PORT = CONTAINER_VNC_PORT
TOKEN_FILE = Path("/run/secrets/art98_token")
HANDSHAKE_TIMEOUT = 10.0
MAX_LINE = 4096
SCREENSHOT_FORMATS = ("ppm", "png")
MAX_DISC = 64 * 1024 * 1024


def nodelay(sock: socket.socket) -> socket.socket:
    """Send small messages at once: every sync and key press is a round trip."""
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    return sock


def keepalive(sock: socket.socket) -> socket.socket:
    """Make a vanished TCP peer error out in about a minute (20 + 3 x 10 s), so
    serve frees the lock a dead client held. Each knob only where the platform has it."""
    if sock.family in (socket.AF_INET, socket.AF_INET6):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
        for name, value in (("IDLE", 20), ("INTVL", 10), ("CNT", 3)):
            option = getattr(socket, f"TCP_KEEP{name}", None)
            if option is not None:
                sock.setsockopt(socket.IPPROTO_TCP, option, value)
    return sock


def read_line(sock: socket.socket, limit: int = MAX_LINE) -> bytes:
    """One line without its newline, read a byte at a time so nothing after it is taken."""
    data = bytearray()
    while len(data) < limit:
        byte = sock.recv(1)
        if not byte:
            raise ConnectionError("connection closed during the handshake")
        if byte == b"\n":
            return bytes(data)
        data += byte
    raise ConnectionError("handshake line too long")


class ControlError(RuntimeError):
    pass


class DesyncError(ControlError):
    """The request's payload can't be read, so the stream is out of step: reply, then hang up."""


class Server:
    """Answers the host on one listening socket until stopped or the VM dies.

    vm is anything with the VM methods used here (a real art98.qemu.VM in the
    container, a fake in tests); serial_path is the guest's COM1 socket.
    """

    def __init__(
        self,
        vm,
        serial_path: Path | str,
        token: str,
        listener: socket.socket,
        log=print,
        snapshots=(),
        started_at: str | None = None,
    ):
        self.vm = vm
        self._snapshots = list(snapshots)
        self._started_at = started_at
        self.serial_path = str(serial_path)
        self._token = token.encode()
        self._listener = listener
        self._log = log
        self._stop = threading.Event()
        self._owner_lock = threading.Lock()
        self._owner = False
        self._agent_ok = (
            False  # guarded by _relay_lock: true only while an owner is live
        )
        self._paused = False  # guarded by _vm_lock
        self._relay_lock = threading.Lock()
        self._relay: tuple[socket.socket, socket.socket] | None = None
        self._vm_lock = threading.Lock()
        self._shots = Path(
            tempfile.mkdtemp(
                prefix="art98-shots-",
                dir="/dev/shm" if os.path.isdir("/dev/shm") else None,
            )
        )
        # The folder holding a Unix socket is shared with the host.
        self._shared = (
            Path(listener.getsockname()).parent
            if listener.family == socket.AF_UNIX
            else None
        )

    @classmethod
    def listen(
        cls,
        vm,
        serial_path,
        token: str,
        host: str = "0.0.0.0",
        port: int = PORT,
        unix: Path | str | None = None,
        log=print,
        snapshots=(),
        started_at: str | None = None,
    ) -> Server:
        """Listen on TCP, or on the Unix socket `unix` if given."""
        if unix:
            Path(unix).unlink(missing_ok=True)
            listener = socket.socket(socket.AF_UNIX)
            listener.bind(str(unix))
            # The folder is the host's private one; the token guards the rest.
            os.chmod(unix, 0o666)
        else:
            listener = socket.socket(socket.AF_INET)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((host, port))
        listener.listen(8)
        return cls(vm, serial_path, token, listener, log, snapshots, started_at)

    @property
    def address(self) -> tuple[str, int] | str:
        name = self._listener.getsockname()
        return name if isinstance(name, str) else name[:2]

    def stop(self) -> None:
        self._stop.set()
        try:
            self._listener.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    def serve(self) -> None:
        """Accept connections until stop() or until the VM dies."""
        self._listener.settimeout(0.5)
        try:
            while not self._stop.is_set():
                if not self.vm.running:
                    self._log("the VM is gone; quitting")
                    break
                try:
                    conn, _ = self._listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                threading.Thread(
                    target=self._handshake, args=(conn,), daemon=True
                ).start()
        finally:
            if self._shared:
                Path(self._listener.getsockname()).unlink(missing_ok=True)
                for path in self._shared.glob("frame.*"):
                    path.unlink(missing_ok=True)
            self._listener.close()
            self._drop_relay()
            for path in self._shots.iterdir():
                path.unlink(missing_ok=True)
            self._shots.rmdir()

    # ---- connections ----

    def _handshake(self, conn: socket.socket) -> None:
        keepalive(nodelay(conn))
        conn.settimeout(HANDSHAKE_TIMEOUT)
        try:
            token = read_line(conn)
            channel = read_line(conn).decode("ascii", "replace")
        except (OSError, ConnectionError):
            conn.close()
            return
        if not hmac.compare_digest(token, self._token):
            self._log("refused a connection with a wrong token")
            self._refuse(conn, "wrong token")
            return
        conn.settimeout(None)
        if channel == "agent":
            self._start_relay(conn)
        elif channel == "status":
            self._status(conn)
        elif channel == "control":
            with self._owner_lock:
                if self._owner:
                    busy = True
                else:
                    busy = False
                    self._owner = True
            if busy:
                self._refuse(
                    conn, "busy: the control channel is held by another client"
                )
                return
            with self._relay_lock:
                self._agent_ok = True
            try:
                conn.sendall(b"ok\n")
                self._control(conn)
            finally:
                try:
                    self._release()
                finally:
                    conn.close()  # EOF tells the client the slot is free
        else:
            self._refuse(conn, f"unknown channel {channel!r}")

    def _status(self, conn: socket.socket) -> None:
        """One JSON line of server state, then close. No lock, no QMP."""
        reply = {
            "ok": True,
            "busy": self._owner,
            "paused": self._paused,
            "snapshots": self._snapshots,
            "started_at": self._started_at,
        }
        try:
            conn.sendall(json.dumps(reply).encode() + b"\n")
        except OSError:
            pass
        conn.close()

    def _release(self) -> None:
        """Clean up after the owner. The slot is freed last, always, so a waiter
        never has its relay dropped or its disc ejected by the previous owner."""
        try:
            with self._relay_lock:
                self._agent_ok = False
            with self._vm_lock:
                self._drop_relay()
                try:
                    self.vm.eject_disc()
                except Exception as error:  # noqa: BLE001
                    self._log(f"eject on release failed: {error}")
                try:
                    self.vm.pause()
                    self._paused = True
                except Exception as error:  # noqa: BLE001
                    self._log(f"pause on release failed: {error}")
            self._log("the control connection was released")
        finally:
            with self._owner_lock:
                self._owner = False

    @staticmethod
    def _refuse(conn: socket.socket, reason: str) -> None:
        try:
            conn.sendall(f"error {reason}\n".encode())
        except OSError:
            pass
        conn.close()

    # agent: a byte relay to COM1, one client at a time

    def _start_relay(self, conn: socket.socket) -> None:
        if not self._agent_ok:
            self._refuse(conn, "no control connection")
            return
        self._drop_relay()  # QEMU's serial socket takes one client; the newest wins
        serial = socket.socket(socket.AF_UNIX)
        deadline = time.monotonic() + 5
        while True:
            try:
                serial.connect(self.serial_path)
                break
            except (ConnectionRefusedError, FileNotFoundError):
                if time.monotonic() > deadline:
                    self._refuse(conn, "the guest's serial port is not reachable")
                    serial.close()
                    return
                time.sleep(0.05)
        with self._relay_lock:
            if not self._agent_ok:  # the owner left while we connected
                self._refuse(conn, "no control connection")
                serial.close()
                return
            self._relay = (conn, serial)
        conn.sendall(b"ok\n")
        threading.Thread(target=self._pump, args=(conn, serial), daemon=True).start()
        threading.Thread(target=self._pump, args=(serial, conn), daemon=True).start()

    def _pump(self, source: socket.socket, sink: socket.socket) -> None:
        try:
            while chunk := source.recv(65536):
                sink.sendall(chunk)
        except OSError:
            pass
        for sock in (source, sink):
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        with self._relay_lock:
            if self._relay and source in self._relay:
                self._relay = None
        source.close()

    def _drop_relay(self) -> None:
        with self._relay_lock:
            relay, self._relay = self._relay, None
        if relay:
            for sock in relay:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    # control: JSON lines

    def _control(self, conn: socket.socket) -> None:
        reader = conn.makefile("rb")
        try:
            while not self._stop.is_set():
                line = reader.readline(MAX_LINE)
                if not line:
                    self._log("the control connection closed")
                    break
                try:
                    request = json.loads(line)
                    data = b""
                    if isinstance(request, dict) and request.get("op") == "insert_disc":
                        size = request.get("size")
                        if (
                            not isinstance(size, int)
                            or isinstance(size, bool)
                            or not 0 < size <= MAX_DISC
                        ):
                            raise DesyncError(
                                f"insert_disc size must be 1 to {MAX_DISC} bytes"
                            )
                        data = reader.read(size)
                        if len(data) != size:
                            raise ConnectionError(
                                "the connection closed inside a disc image"
                            )
                    reply, payload = self.handle(request, data)
                except DesyncError as error:
                    conn.sendall(
                        json.dumps({"ok": False, "error": str(error)}).encode() + b"\n"
                    )
                    self._log(f"{error}; closing the control connection")
                    break
                except ConnectionError as error:
                    self._log(str(error))
                    break
                except (ValueError, TypeError, KeyError, ControlError) as error:
                    reply, payload = {"ok": False, "error": str(error)}, b""
                except Exception as error:  # noqa: BLE001  (QEMU errors and the like go back to the host)
                    reply, payload = (
                        {"ok": False, "error": f"{type(error).__name__}: {error}"},
                        b"",
                    )
                conn.sendall(json.dumps(reply).encode() + b"\n" + payload)
        except OSError:
            self._log("the control connection failed")
        finally:
            reader.close()  # conn stays open: the handshake closes it after release

    def handle(self, request: dict, data: bytes = b"") -> tuple[dict, bytes]:
        if not isinstance(request, dict):
            raise ControlError("a request is a JSON object")
        op = request.get("op")
        with self._vm_lock:
            if op == "screenshot":
                fmt = request.get("format", "ppm")
                if fmt not in SCREENSHOT_FORMATS:
                    raise ControlError(
                        f"format must be one of {', '.join(SCREENSHOT_FORMATS)}"
                    )
                if request.get("shared") and self._shared:
                    name = f"frame.{fmt}"
                    self.vm.screenshot(self._shared / name, format=fmt)
                    return {"ok": True, "file": name}, b""
                path = self._shots / f"frame.{fmt}"
                self.vm.screenshot(path, format=fmt)
                data = path.read_bytes()
                return {"ok": True, "size": len(data)}, data
            if op == "send_keys":
                keys = request["keys"]
                if not isinstance(keys, list) or not all(
                    isinstance(k, str) for k in keys
                ):
                    raise ControlError("keys is a list of QEMU key names")
                self.vm.send_keys(*keys, hold_ms=int(request.get("hold_ms", 80)))
            elif op == "revert":
                self.vm.eject_disc()  # the CD medium is host state; loadvm does not restore it
                self.vm.load_snapshot(str(request.get("snapshot", "ready")))
            elif op == "insert_disc":
                self.vm.insert_disc(data)
            elif op == "eject_disc":
                self.vm.eject_disc()
            elif op == "save_snapshot":
                # prep saves `ready` on its own VM; a long-lived VM must not overwrite it
                raise ControlError("save_snapshot is not served")
            elif op == "pause":
                self.vm.pause()
                self._paused = True
            elif op == "resume":
                self.vm.resume()
                self._paused = False
            else:
                raise ControlError(f"unknown op {op!r}")
        return {"ok": True}, b""


def read_token(path: Path | str | None = None) -> str:
    path = Path(path or os.environ.get("ART98_TOKEN_FILE") or TOKEN_FILE)
    try:
        token = path.read_text().strip()
    except FileNotFoundError:
        raise SystemExit(
            f"error: no token at {path}; mount one at /run/secrets/art98_token"
        ) from None
    if not token:
        raise SystemExit(f"error: the token in {path} is empty")
    return token


def run(
    work: Path | str = "/work",
    port: int = PORT,
    token_file: Path | str | None = None,
    vnc: bool = False,
    ready_timeout: float = 60.0,
    unix: Path | str | None = None,
) -> int:
    """Boot the stage and serve it. Used by `art98 serve` inside the container."""
    from art98.bridge import Bridge
    from art98.qemu import VM, VMConfig
    from art98.session import IMAGE_FILE, SNAPSHOT, private_stage

    token = read_token(token_file)

    def log(message: str) -> None:
        print(f"[serve] {message}", flush=True)

    def on_term(signum, _frame):
        raise SystemExit(128 + signum)

    signal.signal(signal.SIGTERM, on_term)
    with private_stage(work, prefix="vm-") as stage:
        config = VMConfig.from_json(json.loads((stage / IMAGE_FILE).read_text()))
        config.loadvm = SNAPSHOT
        config.display = f"vnc=0.0.0.0:{VNC_PORT - 5900}" if vnc else "none"
        started = time.monotonic()
        with VM(config, log=Path(work) / "qemu.log") as vm:
            probe = Bridge.connect(vm.serial_path)
            try:
                probe.wait_ready(ready_timeout)
            finally:
                probe.close()
            server = Server.listen(
                vm,
                vm.serial_path,
                token,
                port=port,
                unix=unix,
                log=log,
                snapshots=vm.snapshots(),
                started_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            )
            where = unix or f"port {port}"
            log(
                f"guest ready in {time.monotonic() - started:.2f}s; listening on {where}"
            )
            server.serve()
    log("stopped")
    return 0
