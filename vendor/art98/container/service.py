"""The long-lived VM: start it in a container, stop it, ask how it is, and attach
a Session to it for one render. compose.yaml says how the container runs (see
art98.container.compose); the work folder, ports and the image come from Settings."""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from ..errors import Art98Error
from ..guest.bridge import Bridge, BridgeError
from ..guest.qemu import QemuError
from ..session import SNAPSHOT, ImageMissing, Session
from ..settings import CONTAINER_VNC_PORT, Settings, load
from . import compose
from .compose import socket_dir
from .freshness import container_warning, inspect_created
from .host import (
    START_TIMEOUT,
    ChannelBusy,
    ContainerError,
    Engine,
    RemoteVM,
    open_channel,
    transport_name,
)
from .serve import keepalive, nodelay, read_line

ATTACH_TIMEOUT = 3.0  # attach: a dead socket is NotRunning in ~3 s, not 120
BUSY_POLL = 0.5
OLD_IMAGE = "the container image is older than the code; run `art98 up --rebuild`"


class ServiceError(Art98Error, RuntimeError):
    pass


class NotRunning(ServiceError):
    pass


class Unhealthy(ServiceError):
    pass


class Busy(ServiceError):
    pass


# ---- where things are ----


def _address(settings: Settings) -> tuple[str, int] | str:
    if transport_name(settings.transport) == "unix":
        return str(socket_dir(settings) / "serve.sock")
    return (settings.connect_host, settings.control)


def _where(address) -> str:
    return address if isinstance(address, str) else f"{address[0]}:{address[1]}"


def _token(settings: Settings) -> str | None:
    """The client's token: Settings.token (a host program passing it in), else ART98_TOKEN,
    else the token file."""
    given = settings.token or os.environ.get("ART98_TOKEN")
    if given:
        return given.strip() or None
    try:
        return settings.token_path.read_text().strip() or None
    except OSError:
        return None


def _read_status(address, token: str, timeout: float = ATTACH_TIMEOUT) -> dict:
    """The status channel: after the handshake ONE JSON line, no `ok` before it."""
    where = _where(address)
    sock = None
    try:
        if isinstance(address, str):
            sock = socket.socket(socket.AF_UNIX)
            sock.settimeout(timeout)
            sock.connect(address)
        else:
            sock = keepalive(nodelay(socket.create_connection(address, timeout)))
        sock.settimeout(timeout)
        sock.sendall(f"{token}\nstatus\n".encode())
        line = read_line(sock).decode(errors="replace")
    except (OSError, ConnectionError):
        raise NotRunning(
            f"the VM is not running (nothing answers on {where}); run `art98 vm start`"
        ) from None
    finally:
        if sock:
            sock.close()
    if line.startswith("error unknown channel"):
        raise Unhealthy(OLD_IMAGE)
    try:
        reply = json.loads(line)
    except ValueError:
        reply = None
    if not isinstance(reply, dict) or not reply.get("ok"):
        raise Unhealthy(
            f"the VM refused the status request: {line.removeprefix('error ')}"
        )
    return reply


# ---- start / stop / status ----


def _container(engine, settings: Settings, everything: bool = False) -> str | None:
    """The id of this work dir's VM container, running or (everything) not."""
    flag = "-aq" if everything else "-q"
    out = compose.run(engine, settings, "ps", flag, "vm", check=False).stdout.split()
    return out[0].decode() if out else None


def _down(engine, settings: Settings) -> None:
    """Stop and remove this project's containers. SIGTERM reaches serve through
    init, so its run copy is deleted on the way out."""
    compose.run(engine, settings, "down", "--remove-orphans")
    shutil.rmtree(socket_dir(settings), ignore_errors=True)


def start(settings: Settings | None = None, *, engine=None, log=print) -> None:
    settings = settings or load()
    work = settings.work_dir
    if not (work / "image.json").exists():
        raise ImageMissing(f"{work / 'image.json'} not found; run `art98 up`")
    engine = engine or Engine()
    if not engine.has_image(settings.image):
        raise ContainerError(f"no image {settings.image}; run `art98 up` to build it")

    if _container(engine, settings):
        try:
            _read_status(_address(settings), _token(settings) or "")
            log("already running")
            return
        except ServiceError:
            pass  # a stale or sick container: replace it
    _down(engine, settings)
    # No container of this work dir's project is left, so no run copy is in use.
    for stale in (work / "runs").glob("vm-*"):
        shutil.rmtree(stale, ignore_errors=True)

    token_path = settings.token_path
    token_path.parent.mkdir(parents=True, exist_ok=True)
    if not token_path.exists():
        fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_hex(32))
    if transport_name(settings.transport) == "unix":
        socket_dir(settings).mkdir(parents=True, mode=0o700)

    def fail(error: ServiceError) -> ServiceError:
        logs = compose.run(engine, settings, "logs", "--tail", "20", "vm", check=False)
        tail = (logs.stdout + logs.stderr).decode(errors="replace").strip()
        _down(engine, settings)
        if tail:
            error = type(error)(f"{error}\n{tail}")
        return error

    # The healthcheck in compose.yaml passes once serve listens, after the guest agent answers.
    wait = str(int(START_TIMEOUT))
    started = compose.run(
        engine,
        settings,
        "up",
        "-d",
        "--wait",
        "--wait-timeout",
        wait,
        "vm",
        check=False,
    )
    if started.returncode:
        detail = started.stderr.decode(errors="replace").strip().splitlines()
        raise fail(
            ServiceError(f"the VM did not start: {detail[-1] if detail else ''}")
        )
    try:
        reply = _read_status(_address(settings), token_path.read_text().strip())
    except ServiceError as error:
        raise fail(error) from None
    if SNAPSHOT not in reply.get("snapshots", []):
        raise fail(
            Unhealthy(f"the VM has no `{SNAPSHOT}` snapshot; run `art98 up` ...")
        )

    # Probe: control and agent channels, the protocol version, then release (the VM ends paused).
    try:
        session = _connect(settings)
    except ServiceError as error:
        raise fail(error) from None
    try:
        session.bridge.wait_ready(30)
    except BridgeError as error:
        session.close()
        raise fail(Unhealthy(f"the guest agent did not answer: {error}")) from None
    session.close()
    log("the VM is up and paused; render with `art98 render`")


def stop(settings: Settings | None = None, *, engine=None, log=print) -> None:
    settings = settings or load()
    engine = engine or Engine()
    cid = _container(engine, settings, everything=True)
    _down(engine, settings)  # vm.token stays
    log(f"stopped {cid[:12]}" if cid else "nothing to stop")


def status(settings: Settings | None = None, *, engine=None) -> dict:
    """Takes no lock. NotRunning is not raised: it is `running: False`."""
    settings = settings or load()
    out = {
        "running": False, "busy": False, "paused": False, "snapshots": [],
        "started_at": None, "vnc": None, "image_stale": False, "container": None,
    }  # fmt: skip
    try:
        reply = _read_status(_address(settings), _token(settings) or "")
    except NotRunning:
        return out
    out.update(
        running=True,
        **{k: reply.get(k) for k in ("busy", "paused", "snapshots", "started_at")},
    )
    try:
        engine = engine or Engine()
        cid = _container(engine, settings)
        out["container"] = cid
        if cid:
            try:
                host, port = engine.port(cid, CONTAINER_VNC_PORT)
                out["vnc"] = f"{host}:{port}"
            except ContainerError:
                pass  # no VNC port in compose.override.yaml
        created = inspect_created(engine, settings.image)[1]
        out["image_stale"] = bool(container_warning(created, settings.image))
    except (ContainerError, OSError):
        pass  # no engine: the facts from the socket stand alone
    return out


# ---- attach ----


def _connect(settings: Settings) -> Session:
    """Steps 1-4: token, status, control (the lock), agent. Nothing touches the guest."""
    token = _token(settings)
    address = _address(settings)
    if token is None or (isinstance(address, str) and not Path(address).exists()):
        where = _where(address)
        raise NotRunning(
            f"the VM is not running (nothing answers on {where}); run `art98 vm start`"
        )
    reply = _read_status(address, token)
    if SNAPSHOT not in reply.get("snapshots", []):
        raise Unhealthy(f"the VM has no `{SNAPSHOT}` snapshot; run `art98 up` ...")
    if reply.get("busy"):
        raise Busy("the VM is busy: another render holds it")
    try:
        control = open_channel(address, token, "control", ATTACH_TIMEOUT)
    except TimeoutError:
        raise NotRunning(
            f"the VM is not running (nothing answers on {_where(address)}); run `art98 vm start`"
        ) from None
    except ChannelBusy:
        raise Busy("the VM is busy: another render holds it") from None
    except ContainerError as error:
        raise Unhealthy(str(error)) from None
    shared = socket_dir(settings) if isinstance(address, str) else None
    vm = RemoteVM(control, shared_dir=shared)
    try:
        agent = open_channel(address, token, "agent", ATTACH_TIMEOUT)
    except (TimeoutError, ContainerError) as error:
        vm.close()
        raise Unhealthy(f"the VM's agent channel failed: {error}") from None
    return Session(vm, Bridge(agent))


@contextmanager
def attach(
    settings: Settings | None = None, *, revert=True, wait=0.0
) -> Iterator[Session]:
    settings = settings or load()
    deadline = time.monotonic() + wait
    while True:
        try:
            session = _connect(settings)
            break
        except Busy:  # the only error worth waiting for
            if time.monotonic() >= deadline:
                raise
            time.sleep(min(BUSY_POLL, max(0, deadline - time.monotonic())))
    try:
        session.vm.resume()  # the VM was left paused; resume BEFORE loadvm
        try:
            if revert:
                session.revert()  # revert first, ping after: the revert fixes a hung guest
            else:
                session.bridge.ping()
        except (BridgeError, QemuError) as error:
            step = "after the revert" if revert else "to the ping"
            raise Unhealthy(
                f"the guest agent did not answer {step}; "
                f"run `art98 vm stop` then `art98 vm start` ({error})"
            ) from None
        yield session
    finally:
        session.close()  # release: the server ejects, drops the relay, pauses, frees the lock
