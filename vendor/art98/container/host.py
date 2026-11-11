"""The host end of the VM service: Engine, channels, and RemoteVM.

The container (see serve.py) owns QEMU and the run copy of the disk. The host
keeps the Bridge, which talks to the agent through the container's relay, and
a RemoteVM, which asks the container for screenshots, keys and reverts.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from art98.qemu import QemuError
from art98.settings import load

from .serve import keepalive, nodelay, read_line

ENGINE = "docker"
TOKEN_TARGET = "art98_token"
CONNECT_TIMEOUT = 5.0
TRANSPORTS = ("unix", "tcp")
SOCKET_DIR = "/run/art98"
RELEASE_TIMEOUT = 15.0  # pause and eject are QMP calls; seconds under load
# A stage boots in under a second on an idle host; a busy one, or the first
# run copy off btrfs and XFS, takes longer.
START_TIMEOUT = 120.0


class ContainerError(RuntimeError):
    pass


def image_name() -> str:
    return load().image


def transport_name(transport: str | None = None, default: str | None = None) -> str:
    """How the host reaches serve: a Unix socket in a mounted folder on Linux,
    which skips the rootless port forward, and loopback TCP everywhere else.
    ART98_TRANSPORT overrides `default` (the [vm] transport setting)."""
    name = (
        transport
        or os.environ.get("ART98_TRANSPORT")
        or default
        or ("unix" if sys.platform == "linux" else "tcp")
    )
    if name not in TRANSPORTS:
        raise ValueError(
            f"unknown transport {name!r}; choose from {', '.join(TRANSPORTS)}"
        )
    return name


class Engine:
    """The `docker` command, or the one ART98_ENGINE names.

    art98 speaks only Docker's command line. Podman serves it through its
    podman-docker package. ART98_ENGINE only names the binary: nothing else
    changes with it.
    """

    def __init__(self, name: str | None = None):
        wanted = name or os.environ.get("ART98_ENGINE") or ENGINE
        path = shutil.which(wanted)
        if not path:
            raise ContainerError(
                f"{wanted} not found; install Docker, or podman-docker on a Podman host"
            )
        self.name = Path(wanted).name
        self.path = path

    def __call__(
        self,
        *args: str,
        check: bool = True,
        input: bytes | None = None,
        capture: bool = True,
    ) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                [self.path, *args],
                check=check,
                input=input,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.PIPE if capture else None,
            )
        except subprocess.CalledProcessError as error:
            detail = (error.stderr or b"").decode(errors="replace").strip()
            raise ContainerError(f"{self.name} {args[0]} failed: {detail}") from None

    def has_image(self, image: str) -> bool:
        return self("image", "inspect", image, check=False).returncode == 0

    def port(self, container: str, port: int) -> tuple[str, int]:
        output = self("port", container, f"{port}/tcp").stdout.decode().split()
        if not output:
            raise ContainerError(f"{container} publishes no port {port}")
        host, _, number = output[0].rpartition(":")
        return host.strip("[]") or "127.0.0.1", int(number)

    def copy_out(self, image: str, source: str, dest: Path) -> None:
        """Copy a folder out of an image, without running it."""
        created = self("create", image).stdout.decode().strip()
        try:
            dest.mkdir(parents=True, exist_ok=True)
            self("cp", f"{created}:{source}/.", str(dest))
        finally:
            self("rm", created, check=False)

    # secrets

    def secret_args(self, data: bytes, target: str) -> tuple[list[str], callable]:
        """Arguments that put data at /run/secrets/<target>, and a cleanup.

        `docker run` has no secrets outside swarm, so the data goes in a private
        file, mode 0600 in a folder of our own, mounted read-only. The cleanup can
        run once the container exists: its mount keeps the file.
        """
        folder = Path(
            tempfile.mkdtemp(
                prefix="art98-secret-", dir=os.environ.get("XDG_RUNTIME_DIR")
            )
        )
        path = folder / target
        path.touch(mode=0o600)
        path.write_bytes(data)
        return ["-v", f"{path}:/run/secrets/{target}:ro"], (
            lambda: shutil.rmtree(folder, ignore_errors=True)
        )


def mount_args(work: Path) -> list[str]:
    # label=disable lets the container use work/ without relabelling the files in it.
    return [
        "--security-opt",
        "label=disable",
        "-v",
        f"{Path(work).resolve()}:/work",
        *owner_args(),
    ]


def owner_args() -> list[str]:
    """Our uid and gid, for the container to drop to on a rootful engine, so what
    it writes in work/ is ours. See art98.container.entry."""
    if not hasattr(os, "getuid"):
        return []
    return [
        "-e",
        f"ART98_HOST_UID={os.getuid()}",
        "-e",
        f"ART98_HOST_GID={os.getgid()}",
    ]


def open_channel(
    address: tuple[str, int] | str,
    token: str,
    channel: str,
    timeout: float = START_TIMEOUT,
    check=None,
) -> socket.socket:
    """Connect and pass the handshake, retrying while the guest boots.

    Until serve listens, a rootless port forward accepts and then hangs up, so
    a connection only counts once serve has answered. address is (host, port)
    or the path of a Unix socket. check, if given, is called before each try
    and raises if the stage has gone.
    """
    deadline = time.monotonic() + timeout
    while True:
        if check:
            check()
        sock = None
        try:
            if isinstance(address, (str, Path)):
                sock = socket.socket(socket.AF_UNIX)
                sock.settimeout(CONNECT_TIMEOUT)
                sock.connect(str(address))
            else:
                sock = keepalive(
                    nodelay(socket.create_connection(address, CONNECT_TIMEOUT))
                )
            sock.settimeout(CONNECT_TIMEOUT)
            sock.sendall(f"{token}\n{channel}\n".encode())
            answer = read_line(sock).decode(errors="replace")
        except (OSError, ConnectionError):
            if sock:
                sock.close()
            if time.monotonic() > deadline:
                raise TimeoutError(f"nothing answered on {address}") from None
            time.sleep(0.1)
            continue
        if answer != "ok":
            sock.close()
            raise ContainerError(
                f"the stage refused the {channel} channel: "
                f"{answer.removeprefix('error ')}"
            )
        sock.settimeout(None)
        return sock


class RemoteVM:
    """The VM methods the scene layer uses, carried out by the container."""

    def __init__(
        self,
        sock: socket.socket,
        *,
        shared_dir: Path | None = None,
        running=None,
    ):
        # The VM belongs to the long-lived container (art98.service): close()
        # only releases, and `running` is an optional callable.
        self._shared_dir = shared_dir
        self._running = running
        self._sock = sock
        self._reader = sock.makefile("rb")
        self._lock = threading.Lock()
        self._closed = False

    @property
    def running(self) -> bool:
        if self._closed:
            return False
        return self._running() if self._running else True

    def _request(
        self, op: str, payload: bytes = b"", **arguments
    ) -> tuple[dict, bytes]:
        # A request with a payload carries `size` and the raw bytes after the JSON line.
        header = (
            {"op": op, "size": len(payload), **arguments}
            if payload
            else {"op": op, **arguments}
        )
        message = json.dumps(header).encode() + b"\n" + payload
        with self._lock:
            if self._closed:
                raise QemuError("the stage is closed")
            try:
                self._sock.sendall(message)
                line = self._reader.readline()
                if not line:
                    raise QemuError("the stage closed the control connection")
                reply = json.loads(line)
                payload = (
                    self._read_exact(reply.get("size", 0)) if reply.get("ok") else b""
                )
            except OSError as error:
                raise QemuError(
                    f"the stage control connection failed: {error}"
                ) from None
        if not reply.get("ok"):
            raise QemuError(f"{op}: {reply.get('error')}")
        return reply, payload

    def _read_exact(self, size: int) -> bytes:
        data = self._reader.read(size)
        if len(data) != size:
            raise QemuError("the stage closed the connection inside a screenshot")
        return data

    def screenshot(self, path: Path | str, format: str = "png") -> Path:
        shared = self._shared_dir
        reply, data = self._request(
            "screenshot", format=format, shared=shared is not None
        )
        if "file" in reply:
            # Written by QEMU into the folder we share with the container.
            shutil.copyfile(shared / reply["file"], path)
        else:
            Path(path).write_bytes(data)
        return Path(path)

    def send_keys(self, *qcodes: str, hold_ms: int = 80) -> None:
        self._request("send_keys", keys=list(qcodes), hold_ms=hold_ms)

    def pause(self) -> None:
        self._request("pause")

    def resume(self) -> None:
        self._request("resume")

    def load_snapshot(self, name: str) -> None:
        self._request("revert", snapshot=name)

    def insert_disc(self, iso: bytes) -> None:
        self._request("insert_disc", iso)

    def eject_disc(self) -> None:
        self._request("eject_disc")

    def close(self) -> None:
        """Close the control connection and wait for the container to finish
        cleaning up (relay, eject, pause, free slot); it closes its end last."""
        if self._closed:
            return
        self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_WR)
            self._sock.settimeout(RELEASE_TIMEOUT)
            while self._reader.read(65536):  # stray reply bytes are discarded
                pass
        except OSError:  # includes timeout; just close
            pass
        finally:
            self._reader.close()
            self._sock.close()
