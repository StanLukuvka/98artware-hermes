"""The long-lived VM: start it in a container, stop it, ask how it is, and attach
a Session to it for one render. Names, ports and the image come from Settings."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import socket
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .bridge import Bridge, BridgeError
from .container.host import (
    SOCKET_DIR,
    ContainerError,
    Engine,
    RemoteVM,
    mount_args,
    open_channel,
    transport_name,
)
from .container.serve import keepalive, nodelay, read_line
from .freshness import container_warning, inspect_created
from .qemu import QemuError
from .session import SNAPSHOT, ImageMissing, Session
from .settings import CONTAINER_CONTROL_PORT, CONTAINER_VNC_PORT, Settings, load

CONNECT_TIMEOUT = 3.0  # attach: a dead socket is NotRunning in ~3 s, not 120
START_TIMEOUT = 120.0
BUSY_POLL = 0.5
OLD_IMAGE = "the container image is older than the code; run `art98 up --rebuild`"
STOP_GRACE = "20"


class ServiceError(RuntimeError):
    pass


class NotRunning(ServiceError):
    pass


class Unhealthy(ServiceError):
    pass


class Busy(ServiceError):
    pass


# ---- where things are ----


def socket_dir(settings: Settings) -> Path:
    """Deterministic per container name and work path, so two checkouts never share one."""
    tag = hashlib.sha256(str(settings.work_dir).encode()).hexdigest()[:8]
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = (
        Path(runtime) / "art98"
        if runtime
        else Path(tempfile.gettempdir())
        / f"art98-{os.getuid() if hasattr(os, 'getuid') else 0}"
    )
    return base / f"{settings.container}-{tag}"


def _address(settings: Settings) -> tuple[str, int] | str:
    if transport_name(default=settings.transport) == "unix":
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


def _read_status(address, token: str, timeout: float = CONNECT_TIMEOUT) -> dict:
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


def _labelled(engine, work: Path, everything: bool = True) -> list[str]:
    flag = "-aq" if everything else "-q"
    out = engine("ps", flag, "--filter", f"label=art98.work={work}").stdout
    return out.decode().split()


def _remove(engine, cid: str, work: Path, log=print) -> None:
    """Stop and remove one container, only when it is labelled for this work dir."""
    done = engine(
        "inspect", "-f", '{{index .Config.Labels "art98.work"}}', cid, check=False
    )
    if done.returncode == 0 and done.stdout.decode().strip() != str(work):
        raise ServiceError(f"refusing to stop {cid}: it is not labelled for {work}")
    if done.returncode != 0:
        return  # already gone
    # SIGTERM reaches serve through --init, so its run copy is deleted on the way out.
    stopped = engine("stop", "-t", STOP_GRACE, cid, check=False)
    if stopped.returncode == 0:
        engine("rm", cid)
    else:
        engine("rm", "-f", cid)
    log(f"stopped {cid[:12]}")


def _clean_state(settings: Settings) -> None:
    (settings.work_dir / "vm.cid").unlink(missing_ok=True)
    shutil.rmtree(socket_dir(settings), ignore_errors=True)


def start(
    settings: Settings | None = None, *, vnc=False, engine=None, log=print
) -> None:
    settings = settings or load()
    work = settings.work_dir
    if not (work / "image.json").exists():
        raise ImageMissing(f"{work / 'image.json'} not found; run `art98 up`")
    engine = engine or Engine()
    if not engine.has_image(settings.image):
        raise ContainerError(f"no image {settings.image}; run `art98 up` to build it")

    running = _labelled(engine, work, everything=False)
    if running:
        try:
            _read_status(_address(settings), _token(settings) or "")
            log("already running")
            return
        except ServiceError:
            pass  # a stale or sick container: replace it
    for cid in _labelled(engine, work):
        _remove(engine, cid, work, log)
    # No container labelled for this work dir is left, so no run copy is in use.
    for stale in (work / "runs").glob("vm-*"):
        shutil.rmtree(stale, ignore_errors=True)

    token_path = settings.token_path
    token_path.parent.mkdir(parents=True, exist_ok=True)
    if not token_path.exists():
        fd = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(secrets.token_hex(32))
    (work / "vm.cid").unlink(missing_ok=True)  # docker refuses an existing cidfile

    unix = transport_name(default=settings.transport) == "unix"
    if unix:
        shutil.rmtree(socket_dir(settings), ignore_errors=True)
        socket_dir(settings).mkdir(parents=True, mode=0o700)
        listen = ["-v", f"{socket_dir(settings)}:{SOCKET_DIR}"]
        serve = ["--unix", f"{SOCKET_DIR}/serve.sock"]
    else:
        listen = ["-p", f"{settings.bind}:{settings.control}:{CONTAINER_CONTROL_PORT}"]
        serve = []
    if vnc:
        listen += ["-p", f"{settings.bind}:{settings.vnc}:{CONTAINER_VNC_PORT}"]
    token_mount = []  # a token outside work/ is mounted at serve's default secret path
    token_target = "/work/vm.token"
    if token_path != work / "vm.token":
        token_target = "/run/secrets/art98_token"
        token_mount = ["-v", f"{token_path}:{token_target}:ro"]
    engine(
        "run", "-d", "--init",
        "--name", settings.container,
        "--label", f"art98.work={work}",
        "--cidfile", str(work / "vm.cid"),
        *mount_args(work),
        *listen,
        *token_mount,
        settings.image,
        "serve", "--work", "/work", "--token-file", token_target,
        *serve,
        *(["--vnc"] if vnc else []),
    )  # fmt: skip
    cid = (work / "vm.cid").read_text().strip()

    def fail(error: ServiceError) -> ServiceError:
        logs = engine("logs", "--tail", "20", cid, check=False)
        tail = (logs.stdout + logs.stderr).decode(errors="replace").strip()
        _remove(engine, cid, work, log)
        _clean_state(settings)
        if tail:
            error = type(error)(f"{error}\n{tail}")
        return error

    token = token_path.read_text().strip()
    deadline = time.monotonic() + START_TIMEOUT
    while True:
        try:
            reply = _read_status(_address(settings), token)
        except NotRunning as error:
            if time.monotonic() > deadline:
                raise fail(error) from None
            alive = engine("ps", "-q", "--filter", f"id={cid}", check=False)
            if not alive.stdout.strip():
                raise fail(
                    ServiceError("the VM container exited while starting")
                ) from None
            time.sleep(0.2)
            continue
        except Unhealthy as error:
            raise fail(error) from None
        break
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
    work = settings.work_dir
    engine = engine or Engine()
    try:
        ids = [(work / "vm.cid").read_text().strip()]
        ids = [i for i in ids if i] or _labelled(engine, work)
    except OSError:
        ids = _labelled(engine, work)
    if len(ids) > 1:
        raise ServiceError(f"more than one container is labelled for {work}: {ids}")
    for cid in ids:
        _remove(engine, cid, work, log)
    if not ids:
        log("nothing to stop")
    _clean_state(settings)  # vm.token stays


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
        cid = (settings.work_dir / "vm.cid").read_text().strip()
        out["container"] = cid
        try:
            host, port = engine.port(cid, CONTAINER_VNC_PORT)
            out["vnc"] = f"{host}:{port}"
        except ContainerError:
            pass  # VNC was not asked for
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
        control = open_channel(address, token, "control", CONNECT_TIMEOUT)
    except TimeoutError:
        raise NotRunning(
            f"the VM is not running (nothing answers on {_where(address)}); run `art98 vm start`"
        ) from None
    except ContainerError as error:
        if "channel: busy" in str(error):
            raise Busy("the VM is busy: another render holds it") from None
        raise Unhealthy(str(error)) from None
    shared = socket_dir(settings) if isinstance(address, str) else None
    vm = RemoteVM(control, shared_dir=shared)
    try:
        agent = open_channel(address, token, "agent", CONNECT_TIMEOUT)
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
