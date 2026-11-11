"""Command line entry point."""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import traceback
from pathlib import Path

from . import freshness, service
from .bridge import BridgeError
from .container.host import ContainerError
from .desktop import DesktopError
from .guestfiles import GuestBuildMissing, guest_build
from .qemu import QemuError
from .recorder import GIF_COLORS, GIF_FPS, RecorderError
from .service import ServiceError
from .session import ImageMissing
from .settings import CONTAINER_CONTROL_PORT, SettingsError, load
from .state import StateError

KEY_SECRET_FILE = Path("/run/secrets/art98_key")


CONTAINER_NEEDS = {
    "ffmpeg"
}  # what a host needs when QEMU and setup run in the container


def cmd_doctor(_args) -> int:
    import shutil

    from art98_setup import builder

    rows = builder.doctor()
    name = os.environ.get("ART98_ENGINE") or "docker"
    engine = shutil.which(name)
    missing = [tool for tool, path, _ in rows if not path]
    local_only = [tool for tool in missing if tool not in CONTAINER_NEEDS]
    local_ok = not missing
    container_ok = bool(engine) and not (CONTAINER_NEEDS & set(missing))
    for tool, path, _ in rows:
        note = (
            " (only `art98 setup` on the host needs it)"
            if not path and tool in local_only and container_ok
            else ""
        )
        print(f"{tool:24} {path or 'missing'}{note}")
    print(f"{'/dev/kvm':24} not used: Windows 98 runs under software emulation")
    print(
        f"{name:24} {engine or 'missing'} (for `art98 up` and `art98 vm`"
        f"{'' if engine else '; on a Podman host, install podman-docker'})"
    )
    if local_ok and container_ok:
        print("\nEverything is usable, here and in the container.")
    elif local_ok:
        print(
            "\n`art98 setup` is usable here. For `art98 up` and `art98 vm` install docker, "
            "or podman-docker on a Podman host."
        )
    elif container_ok:
        print(
            "\nThe VM is usable (run `art98 up` first). "
            f"`art98 setup` on the host needs: {', '.join(local_only)}"
        )
    else:
        blockers = sorted(CONTAINER_NEEDS & set(missing)) + ([] if engine else [name])
        print(
            f"\nNothing is usable yet. Rendering needs ffmpeg and {name}; "
            f"`art98 setup` on the host needs {', '.join(local_only) or 'nothing else'}."
        )
        print(f"Missing for `art98 vm`: {', '.join(blockers)}")
        print("Install the missing tools with one of:")
        for system, command, packages in builder.install_hints(missing):
            print(f"  {system:15} {command} {' '.join(packages)}")
    return 0 if (local_ok or container_ok) else 1


def _product_key(args) -> str | None:
    if args.key or os.environ.get("ART98_PRODUCT_KEY"):
        return args.key or os.environ.get("ART98_PRODUCT_KEY")
    if KEY_SECRET_FILE.exists():  # mounted in by `art98 up`
        return KEY_SECRET_FILE.read_text().strip() or None
    return None


def cmd_setup(args) -> int:
    from art98_setup.builder import Setup, SetupError

    key = _product_key(args)
    work = load(work=args.work).work_dir
    tool = Setup(
        work=work,
        guest_build=guest_build(),
        iso=args.iso,
        product_key=key,
        disk_mb=args.disk_mb,
        watch=args.watch,
    )
    try:
        tool.run(args.from_stage)
    except SetupError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"Ready: {work}")
    return 0


RUN_ERRORS = (
    ImageMissing,
    ContainerError,
    RecorderError,
    StateError,
    DesktopError,
    BridgeError,
    QemuError,
    GuestBuildMissing,
    ServiceError,
    SettingsError,
)


def _scene_frame(error: BaseException, path: Path) -> str:
    """` (file:LINE, in func)` for the innermost traceback frame in the scene file, else ''."""
    mine = path.resolve()
    hits = [
        f
        for f in traceback.extract_tb(error.__traceback__)
        if Path(f.filename).resolve() == mine
    ]
    return f" ({path.name}:{hits[-1].lineno}, in {hits[-1].name})" if hits else ""


def _guarded(run, extra: tuple = ()):
    """Turn a known failure into `error: ...` and exit 1; ART98_TRACE=1 keeps the traceback."""

    def wrapper(args) -> int:
        try:
            return run(args)
        except KeyboardInterrupt:
            print("interrupted", file=sys.stderr)
            return 130
        except (*RUN_ERRORS, *extra) as error:
            if os.environ.get("ART98_TRACE"):
                raise
            where = getattr(error, "art98_where", "")
            print(f"error: {error}{where}", file=sys.stderr)
            return 1

    return wrapper


def cmd_ping(args) -> int:
    settings = load(args.work)
    freshness.warn(settings.work_dir)
    with service.attach(settings, revert=False, wait=args.wait) as session:
        print(f"guest tick count: {session.bridge.ping()}")
        for pid, name in session.bridge.processes():
            print(f"{pid:#010x} {name}")
    return 0


def _load_scene(path: Path, name: str | None) -> type:
    """Import a scene file and pick the scene class it defines."""
    from .anim import Scene

    if not path.is_file():
        raise SystemExit(f"error: {path} not found")
    module_name = f"art98_scene_{path.stem}"
    sys.path.insert(0, str(path.resolve().parent))
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    # Registered so a scene can find its own file, for state paths beside it.
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    scenes = {
        key: value
        for key, value in vars(module).items()
        if isinstance(value, type)
        and issubclass(value, Scene)
        and value.__module__ == module_name
    }
    if name is None and len(scenes) == 1:
        return next(iter(scenes.values()))
    if name in scenes:
        return scenes[name]
    choices = ", ".join(sorted(scenes)) or "none"
    raise SystemExit(f"error: choose a scene from {path}: {choices}")


def cmd_render(args) -> int:
    from .anim import render

    scene = _load_scene(args.file, args.scene)
    settings = load(args.work)
    freshness.warn(settings.work_dir)
    try:
        with service.attach(settings, wait=args.wait) as session:
            written = render(
                scene,
                args.output,
                gif=args.gif,
                gif_fps=args.gif_fps,
                gif_colors=args.gif_colors,
                gif_scale=args.gif_scale,
                gif_dither=args.gif_dither,
                session=session,
            )
    except RUN_ERRORS as error:
        error.art98_where = _scene_frame(error, args.file)
        raise
    print(f"Wrote {written}" + (f" and {args.gif}" if args.gif else ""))
    return 0


def cmd_serve(args) -> int:
    from .container import serve

    return serve.run(
        args.work,
        port=args.port,
        token_file=args.token_file,
        vnc=args.vnc,
        ready_timeout=args.ready_timeout,
        unix=args.unix,
    )


def cmd_up(args) -> int:
    from art98_setup.up import up

    return up(
        args.work,
        iso=args.iso,
        watch=args.watch,
        rebuild=args.rebuild,
        vnc_port=args.vnc_port,
        disk_mb=args.disk_mb,
    )


def cmd_vm_start(args) -> int:
    service.start(load(args.work), vnc=args.vnc)
    return 0


def cmd_vm_stop(args) -> int:
    service.stop(load(args.work))
    return 0


def cmd_vm_status(args) -> int:
    info = service.status(load(args.work))
    if not info["running"]:
        print("down: the VM is not running; run `art98 vm start`")
        return 1
    snapshots = info["snapshots"] or []
    print(f"up since {info['started_at']}")
    print(
        f"state: {'busy' if info['busy'] else 'paused' if info['paused'] else 'running'}"
    )
    print(f"ready snapshot: {'yes' if 'ready' in snapshots else 'MISSING'}")
    print(f"vnc: {info['vnc'] or 'off'}")
    if info["image_stale"]:
        print(
            "warning: the container image is older than the code: `art98 up --rebuild`"
        )
    return 0 if "ready" in snapshots else 1


WORK_HELP = "folder holding the prepared image (default: [vm] work from art98.toml)"


def _wait_option(command) -> None:
    command.add_argument(
        "--wait",
        type=float,
        default=0.0,
        metavar="S",
        help="if the VM is busy, retry for up to S seconds (default 0: fail at once); "
        "only Busy is retried",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="art98")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("doctor", help="check the host tools").set_defaults(
        run=cmd_doctor
    )

    command = commands.add_parser("setup", help="build the prepared Windows 98 image")
    command.add_argument("--iso", type=Path, help="your Windows 98 SE disc image")
    command.add_argument("--key", help="product key, or set ART98_PRODUCT_KEY")
    command.add_argument(
        "--work",
        type=Path,
        default=None,
        help="folder for the prepared image (default: [vm] work from art98.toml)",
    )
    command.add_argument(
        "--disk-mb", type=int, default=2047, help="virtual disk size in MB"
    )
    command.add_argument(
        "--from",
        dest="from_stage",
        help="redo this step and everything after it: vm, setup, init_api or ready",
    )
    command.add_argument("--watch", action="store_true", help="show the VM's screen")
    command.set_defaults(run=_guarded(cmd_setup))

    command = commands.add_parser("ping", help="talk to the agent on the running VM")
    command.add_argument("--work", type=Path, default=None, help=WORK_HELP)
    _wait_option(command)
    command.set_defaults(run=_guarded(cmd_ping))

    command = commands.add_parser("render", help="play a scene and write a video")
    command.add_argument("file", type=Path, help="Python file holding the scene")
    command.add_argument(
        "scene", nargs="?", help="scene class, if the file has several"
    )
    command.add_argument("-o", "--output", type=Path, help="output file, .mp4 or .gif")
    command.add_argument("--gif", type=Path, help="write a GIF as well")
    command.add_argument("--work", type=Path, default=None, help=WORK_HELP)
    command.add_argument(
        "--gif-fps", type=int, default=GIF_FPS, help="GIF frames per second"
    )
    command.add_argument(
        "--gif-colors", type=int, default=GIF_COLORS, help="GIF palette size"
    )
    command.add_argument(
        "--gif-scale",
        type=float,
        default=1.0,
        help="GIF size relative to the video (0.5 = half width and height)",
    )
    command.add_argument(
        "--gif-dither",
        default="none",
        help="ffmpeg paletteuse dither: none, bayer, sierra2_4a...",
    )
    _wait_option(command)
    command.set_defaults(run=_guarded(cmd_render))

    command = commands.add_parser("vm", help="start, stop or check the long-lived VM")
    vm = command.add_subparsers(dest="vm_command", required=True)
    for name, run, text in (
        ("start", cmd_vm_start, "boot the VM in a container and leave it paused"),
        ("stop", cmd_vm_stop, "stop and remove the VM's container"),
        (
            "status",
            cmd_vm_status,
            "up or down, busy or paused; exit 1 when not healthy",
        ),
    ):
        sub = vm.add_parser(name, help=text, description=text)
        sub.add_argument("--work", type=Path, default=None, help=WORK_HELP)
        if name == "start":
            sub.add_argument(
                "--vnc",
                action="store_true",
                help="show the screen on VNC at [ports] bind:vnc from art98.toml; "
                "view only, fixed at start",
            )
        sub.set_defaults(run=_guarded(run))

    text = "from your disc and key to a working desktop, in the art98 container"
    command = commands.add_parser("up", help=text, description=text)
    command.add_argument(
        "--iso", type=Path, help="your Windows 98 SE disc image, for setup"
    )
    command.add_argument("--work", type=Path, default=None, help=WORK_HELP)
    command.add_argument(
        "--watch",
        action="store_true",
        help="show setup's screen over VNC on a loopback port",
    )
    command.add_argument(
        "--vnc-port",
        type=int,
        default=None,
        help="host port for --watch (default: [ports] setup_vnc from art98.toml)",
    )
    command.add_argument(
        "--rebuild", action="store_true", help="rebuild the container image"
    )
    command.add_argument(
        "--disk-mb", type=int, default=2047, help="virtual disk size in MB"
    )
    command.set_defaults(run=_guarded(cmd_up, extra=(FileNotFoundError,)))

    text = "inside the container: boot a stage and serve it to the host"
    command = commands.add_parser("serve", help=text, description=text)
    command.add_argument(
        "--work",
        type=Path,
        default=Path("/work"),
        help="folder holding the prepared image (default /work)",
    )
    command.add_argument(
        "--port",
        type=int,
        default=CONTAINER_CONTROL_PORT,
        help=f"TCP port to listen on (default {CONTAINER_CONTROL_PORT})",
    )
    command.add_argument(
        "--unix", type=Path, help="listen on this Unix socket instead of TCP"
    )
    command.add_argument(
        "--token-file", type=Path, help="default /run/secrets/art98_token"
    )
    command.add_argument(
        "--vnc", action="store_true", help="show the screen on VNC port 5900"
    )
    command.add_argument(
        "--ready-timeout",
        type=float,
        default=60.0,
        help="seconds to wait for the guest agent after boot (default 60)",
    )
    command.set_defaults(run=cmd_serve)

    args = parser.parse_args(argv)
    return args.run(args)


if __name__ == "__main__":
    sys.exit(main())
