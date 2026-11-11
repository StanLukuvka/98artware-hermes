"""The one seam between the Hermes tools and art98.

Every tool calls a method on a Desk; get_desk() picks one from the backend setting:
DriveDesk (drive) is the real one: it imports art98 from the read-only library mount and
attaches to the VM container over its control API. StubDesk (stub) answers NotConnected and
DemoDesk (demo) fakes data, both for working on the plugin without a VM.

No imports from the rest of the plugin, so dashboard/plugin_api.py can load it by path.
"""

from __future__ import annotations

import threading
from typing import Any


class NotConnected(RuntimeError):
    """The art98 backend isn't wired up yet (or isn't running)."""


class DeskError(RuntimeError):
    """A backend failure with a code the agent can act on (busy, render_failed, ...)."""

    def __init__(self, message: str, code: str):
        super().__init__(message)
        self.code = code


class Desk:
    """What the tools need from a Windows 98 desktop. Coordinates are guest pixels."""

    name = "abstract"

    def status(self) -> dict[str, Any]:
        """{backend, connected, note?, vm, activity}

        vm: "none" (no VM to talk to), "off", "booting", "running" or "paused".
        activity: None when idle, else {"kind": "render" | "record" | "action",
        "name": what is running (a script name, a tool), "since": unix time}.
        """
        raise NotImplementedError
    def screenshot(self) -> bytes: raise NotImplementedError  # PNG
    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> dict: raise NotImplementedError
    def type_text(self, text: str) -> dict: raise NotImplementedError
    def key(self, keys: str) -> dict: raise NotImplementedError
    def windows(self) -> list[dict]: raise NotImplementedError
    def open(self, program: str) -> dict: raise NotImplementedError
    def win32(self, dll: str, function: str, args: list) -> dict: raise NotImplementedError
    def revert(self) -> dict: raise NotImplementedError
    def set_state(self, state_path: str) -> dict: raise NotImplementedError
    def record(self, action: str, out_path: str | None = None) -> dict: raise NotImplementedError
    def render(self, animation_path: str, out_path: str, scene: str | None = None) -> dict: raise NotImplementedError


class StubDesk(Desk):
    name = "stub"

    def status(self) -> dict[str, Any]:
        return {"backend": self.name, "connected": False, "vm": "none", "activity": None,
                "note": "stub backend: no Windows 98 VM is attached yet"}

    def _no(self, what: str):
        raise NotConnected(f"{what}: the art98 backend is a stub, no VM is attached yet")

    def screenshot(self): self._no("screenshot")
    def click(self, x, y, button="left", double=False): self._no("click")
    def type_text(self, text): self._no("type")
    def key(self, keys): self._no("key")
    def windows(self): self._no("windows")
    def open(self, program): self._no("open")
    def win32(self, dll, function, args): self._no("win32")
    def revert(self): self._no("revert")
    def set_state(self, state_path): self._no("set_state")
    def record(self, action, out_path=None): self._no("record")
    def render(self, animation_path, out_path, scene=None): self._no("render")


class PendingDesk(StubDesk):
    """A backend named in the settings that isn't built yet."""

    def __init__(self, name: str):
        self.name = name

    def status(self) -> dict[str, Any]:
        return {"backend": self.name, "connected": False, "vm": "none", "activity": None,
                "note": f"backend {self.name!r} is not built yet; every VM tool answers not_connected"}


class DemoDesk(StubDesk):
    """Pretends a VM is running so the dashboard has something to show (see demo.py).
    Status and screenshot are fake; every action still answers not_connected."""

    name = "demo"

    def __init__(self):
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location("art98_demo", Path(__file__).resolve().parent / "demo.py")
        self._demo = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self._demo)

    def status(self) -> dict[str, Any]:
        return {"backend": self.name, "connected": True, "vm": "running", "activity": self._demo.activity(),
                "note": "demo backend: fake status and screen; actions still answer not_connected"}

    def screenshot(self) -> bytes:
        return self._demo.screen_png()

    def _no(self, what: str):
        raise NotConnected(f"{what}: the demo backend only fakes the status and the screen")


class DriveDesk(Desk):
    """The real backend: art98's library, imported here, talks to the VM container's API.

    ART98_CORE is the art98 library folder (mounted read-only) and ART98_CLIENT the folder
    with the client art98.toml and the VM token. Each action attaches (taking the VM's
    lock), acts and releases, so the desktop carries on between tool calls. A render runs
    `art98 render` in a child process so a scene's code never runs inside Hermes itself.
    """

    name = "drive"
    RENDER_TIMEOUT_S = 900
    SETTLE_S = 0.3  # let the guest handle input before the VM is paused again

    def __init__(self):
        import os
        import sys
        from pathlib import Path
        self.core = Path(os.environ.get("ART98_CORE", "/opt/art98/core"))
        self.client = Path(os.environ.get("ART98_CLIENT", "/opt/art98-client"))
        self.activity_file = _plugin_module("media").data_root() / ".activity.json"
        if not (self.core / "art98").is_dir():
            raise NotConnected(f"art98 library not found at {self.core} (set ART98_CORE)")
        if str(self.core) not in sys.path:
            sys.path.insert(0, str(self.core))
        from art98 import service, settings  # noqa: F401  (import check)
        self._service, self._settings_mod = service, settings

    # -- plumbing ------------------------------------------------------------------
    def _settings(self):
        st = self._settings_mod
        path = st.find(self.client)
        if path is None:
            raise NotConnected(f"no art98.toml in {self.client} (set ART98_CLIENT)")
        return st.Settings(**st._read(path), source=path, root=path.parent)

    def _mark(self, kind: str | None, name: str = "") -> None:
        import json
        import time
        try:
            if kind is None:
                self.activity_file.unlink(missing_ok=True)
            else:
                self.activity_file.write_text(json.dumps({"kind": kind, "name": name, "since": time.time()}))
        except OSError:
            pass

    def _translate(self, error: Exception):
        sv = self._service
        if isinstance(error, sv.Busy):
            return DeskError("the VM is busy: a render or another action holds it; try again shortly", "busy")
        if isinstance(error, sv.NotRunning):
            return NotConnected(str(error))
        if isinstance(error, sv.ServiceError):
            return DeskError(str(error), "vm_unhealthy")
        return error

    def _session(self, what: str, revert: bool = False):
        from contextlib import contextmanager

        @contextmanager
        def run():
            self._mark("action", what)
            try:
                with self._service.attach(self._settings(), revert=revert, wait=5.0) as session:
                    yield session
            except Exception as error:
                translated = self._translate(error)
                if translated is error:
                    raise
                raise translated from None
            finally:
                self._mark(None)
        return run()

    def _settle(self, session) -> None:
        import time
        time.sleep(self.SETTLE_S)
        session.bridge.sync()

    # -- Desk ----------------------------------------------------------------------
    def status(self) -> dict[str, Any]:
        import json
        import time
        base = {"backend": self.name, "connected": False, "vm": "off", "activity": None}
        try:
            settings = self._settings()
            reply = self._service._read_status(self._service._address(settings),
                                                self._service._token(settings) or "", timeout=2.0)
        except NotConnected as e:
            return {**base, "vm": "none", "note": str(e)}
        except Exception as e:  # NotRunning, Unhealthy, a bad token...
            return {**base, "note": str(e)}
        busy = bool(reply.get("busy"))
        activity = None
        if busy:
            try:
                activity = json.loads(self.activity_file.read_text())
            except (OSError, ValueError):
                activity = {"kind": "action", "name": "another client", "since": time.time()}
        return {**base, "connected": True, "vm": "paused" if reply.get("paused") and not busy else "running",
                "activity": activity, "ready_snapshot": "ready" in (reply.get("snapshots") or [])}

    def screenshot(self) -> bytes:
        """The screen as it is, without resuming the VM (a paused VM shows its last frame)."""
        import tempfile
        from pathlib import Path
        try:
            session = self._service._connect(self._settings())
        except Exception as error:
            raise self._translate(error) from None
        try:
            with tempfile.TemporaryDirectory() as tmp:
                return Path(session.vm.screenshot(Path(tmp) / "screen.png")).read_bytes()
        finally:
            session.close()

    def click(self, x, y, button="left", double=False):
        from art98.win32 import mouse_button
        if button not in ("left", "right", "middle"):
            raise ValueError("button must be left, right or middle")
        with self._session("click") as s:
            s.bridge.user32.SetCursorPos(int(x), int(y))
            for _ in range(2 if double else 1):
                mouse_button(s.bridge, button, True)
                mouse_button(s.bridge, button, False)
            self._settle(s)
        return {"clicked": [x, y], "button": button, "double": double}

    def type_text(self, text):
        import time
        from art98.actions.keyboard import KEY_HOLD_MS, KEY_SETTLE, char_chord
        chords = [char_chord(c) for c in text]  # ValueError for a character no key types
        with self._session("type") as s:
            for chord in chords:  # paced like art98's TypeKeys: the PS/2 queue drops a flood
                s.vm.send_keys(*chord, hold_ms=KEY_HOLD_MS)
                time.sleep(KEY_SETTLE)
            self._settle(s)
        return {"typed": len(text)}

    def key(self, keys):
        import time
        from art98.actions.keyboard import KEY_HOLD_MS, KEY_SETTLE, key_chord
        combos = [key_chord(k) for k in str(keys).split()]
        if not combos:
            raise ValueError("keys is empty")
        with self._session("key") as s:
            for chord in combos:
                s.vm.send_keys(*chord, hold_ms=KEY_HOLD_MS)
                time.sleep(KEY_SETTLE)
            self._settle(s)
        return {"pressed": str(keys).split()}

    def windows(self):
        with self._session("windows") as s:
            found = s.bridge.windows()
        return [{"hwnd": w.hwnd, "title": w.title, "class": w.class_name, "rect": list(w.rect),
                 "visible": w.visible, "pid": w.pid} for w in found if w.visible and w.title]

    def open(self, program):
        with self._session("open") as s:
            pid = s.bridge.spawn(program)
            import time
            time.sleep(1.0)  # give the window time to appear before the screenshot
            self._settle(s)
        return {"opened": program, "pid": pid}

    def win32(self, dll, function, args):
        with self._session("win32") as s:
            result = s.bridge.call(dll, function, *args)
        return {"value": getattr(result, "value", None)}

    def revert(self):
        with self._session("revert", revert=True) as s:
            self._settle(s)
        return {"reverted": "ready"}

    def set_state(self, state_path):
        raise DeskError("set_state is not built for the real backend yet; put `self.set_state(...)` "
                        "at the top of a scene and use art98_render", "not_built")

    def record(self, action, out_path=None):
        raise DeskError("live recording is not built yet; write a scene and use art98_render", "not_built")

    def render(self, animation_path, out_path, scene=None):
        import os
        import subprocess
        import sys
        from pathlib import Path
        out = Path(out_path)
        env = {**os.environ, "PYTHONPATH": str(self.core)}
        cmd = [sys.executable, "-c", "import sys; from art98.cli import main; sys.exit(main())",
               "render", str(animation_path), *([scene] if scene else []), "-o", str(out), "--wait", "60"]
        self._mark("render", Path(animation_path).name)
        try:
            run = subprocess.run(cmd, cwd=self.client, env=env, capture_output=True, text=True,
                                 timeout=self.RENDER_TIMEOUT_S)
        except subprocess.TimeoutExpired:
            raise DeskError(f"render took longer than {self.RENDER_TIMEOUT_S} s", "timeout") from None
        finally:
            self._mark(None)
        if run.returncode != 0:
            lines = [l for l in (run.stderr or run.stdout).strip().splitlines() if l.strip()]
            message = next((l for l in reversed(lines) if l.startswith("error:")), lines[-1] if lines else "no output")
            message = message.removeprefix("error: ")
            if "(" + Path(animation_path).name + ":" not in message:  # a plain traceback: add the scene's line
                import re
                frames = re.findall(r'File "[^"]*?([^/"]+\.py)", line (\d+), in (\S+)', run.stderr or "")
                mine = [f for f in frames if f[0] == Path(animation_path).name]
                if mine:
                    message += f" ({mine[-1][0]}:{mine[-1][1]}, in {mine[-1][2]})"
            code = ("busy" if "busy" in message else "not_connected" if "not running" in message
                    else "render_failed")
            raise DeskError(message, code)
        if not out.is_file():
            raise DeskError(f"render finished but wrote no {out.name}", "render_failed")
        return {"output": out.name, "bytes": out.stat().st_size}


_desk: Desk | None = None
_desk_lock = threading.Lock()
_modules: dict[str, Any] = {}


def _plugin_module(name: str):
    """A sibling module loaded by path (desk.py may itself be loaded on its own, by path)."""
    if name not in _modules:
        import importlib.util
        from pathlib import Path
        spec = importlib.util.spec_from_file_location(f"_art98_desk_{name}", Path(__file__).resolve().parent / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _modules[name] = module
    return _modules[name]


def _configured_backend() -> str:
    try:
        return _plugin_module("config").load()["backend"]
    except Exception:  # noqa: BLE001
        return "stub"


def get_desk() -> Desk:
    """The Desk for the configured backend; rebuilt when the setting changes.
    Locked: Hermes runs tool calls on several threads, and a second DriveDesk would be wasted work."""
    name = _configured_backend()
    with _desk_lock:
        return _get_desk(name)


def _get_desk(name: str) -> Desk:
    global _desk
    if _desk is None or _desk.name != name:
        if name == "drive":
            try:
                _desk = DriveDesk()
            except Exception as e:  # no library mounted, or it failed to import
                _desk = PendingDesk(name)
                _desk.status = lambda e=e: {"backend": "drive", "connected": False, "vm": "none",
                                            "activity": None, "note": f"art98 library unavailable: {e}"}
        else:
            _desk = StubDesk() if name == "stub" else DemoDesk() if name == "demo" else PendingDesk(name)
    return _desk
