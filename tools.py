"""Tool handlers. Each takes the model's args, calls the Desk, and returns a JSON string
(or a _multimodal dict when there is a screenshot). Handlers never raise."""

from __future__ import annotations

import base64
import json
import threading
import time
from pathlib import Path
from typing import Any, Callable

from . import combine as _combine
from . import config, history, media
from .desk import DeskError, NotConnected, get_desk


def _ok(**payload: Any) -> str:
    return json.dumps({"ok": True, **payload}, ensure_ascii=False)


def _err(message: str, **extra: Any) -> str:
    return json.dumps({"ok": False, "error": message, **extra}, ensure_ascii=False)


def _with_image(text: dict, png: bytes) -> dict:
    """Hermes' multimodal tool result: text plus an image the model can see."""
    summary = json.dumps(text, ensure_ascii=False)
    url = "data:image/png;base64," + base64.b64encode(png).decode()
    return {"_multimodal": True,
            "content": [{"type": "text", "text": summary}, {"type": "image_url", "image_url": {"url": url}}],
            "text_summary": summary}


def _run(fn: Callable[..., Any], args: dict, **kwargs: Any) -> Any:
    try:
        return fn(args, **kwargs)
    except NotConnected as e:
        return _err(str(e), code="not_connected", backend=get_desk().name)
    except DeskError as e:
        return _err(str(e), code=e.code)
    except (ValueError, TypeError, KeyError) as e:
        return _err(f"bad arguments: {e}", code="bad_arguments")
    except Exception as e:  # deliberate boundary: a tool must never raise into the agent loop
        return _err(f"{type(e).__name__}: {e}", code="internal")


def _outcome(result: Any) -> tuple[bool, str | None, str]:
    """ok, error code and message of a result, for the history log."""
    text = result.get("text_summary", "") if isinstance(result, dict) else result
    try:
        data = json.loads(text)
        return bool(data.get("ok")), data.get("code"), data.get("error", "")
    except (TypeError, ValueError, AttributeError):
        return True, None, ""


def _guarded(fn: Callable[[dict], Any]) -> Callable[..., Any]:
    """Never raise. Renders (script runs) are logged to the history."""
    def handler(args: dict, **kwargs: Any) -> Any:
        args = {} if args is None else args
        if not isinstance(args, dict):
            return _err("arguments must be an object", code="bad_arguments")
        start = time.monotonic()
        if fn.__name__ == "art98_render":  # its approval is per session (see pre_tool_call)
            result = _run(fn, args, session_id=str(kwargs.get("session_id") or ""))
        else:
            result = _run(fn, args)
        if fn.__name__ == "art98_render":
            try:  # history is best-effort: a failure here never turns into a raised tool call
                ok, code, message = _outcome(result)
                out = media.folder("recordings") / str(args.get("output", ""))
                history.log_run(str(args.get("animation", "?")), out, ok, code, message, start)
            except Exception:  # noqa: BLE001
                pass
        return result
    handler.__name__ = fn.__name__
    return handler


def _after(result: dict, args: dict) -> Any:
    """Attach a screenshot when the action asked for one (the default)."""
    if not args.get("screenshot_after", config.load()["screenshot_after"]):
        return _ok(**result)
    return _with_image({"ok": True, **result}, get_desk().screenshot())


@_guarded
def art98_status(args: dict) -> Any:
    settings = {k: v for k, v in config.load().items() if k != "host_folder"}  # a host path; not for the model
    return _ok(**get_desk().status(), media=media.counts(), settings=settings)


@_guarded
def art98_media(args: dict) -> Any:
    kind = args["kind"]
    return _ok(kind=kind, folder=str(media.folder(kind)), files=media.listing(kind))


@_guarded
def art98_screenshot(args: dict) -> Any:
    png = get_desk().screenshot()
    info: dict[str, Any] = {"ok": True}
    if args.get("save"):
        path = media.resolve("screenshots", time.strftime("shot-%Y%m%d-%H%M%S.png"))
        path.write_bytes(png)
        info["saved"] = str(path)
    return _with_image(info, png)


@_guarded
def art98_click(args: dict) -> Any:
    r = get_desk().click(int(args["x"]), int(args["y"]), args.get("button", "left"), bool(args.get("double", False)))
    return _after(r, args)


@_guarded
def art98_type(args: dict) -> Any:
    return _after(get_desk().type_text(str(args["text"])), args)


@_guarded
def art98_key(args: dict) -> Any:
    return _after(get_desk().key(str(args["keys"])), args)


@_guarded
def art98_windows(args: dict) -> Any:
    return _ok(windows=get_desk().windows())


@_guarded
def art98_open(args: dict) -> Any:
    return _after(get_desk().open(str(args["program"])), args)


@_guarded
def art98_win32(args: dict) -> Any:
    if not config.load()["allow_win32"]:
        return _err("art98_win32 is off; turn on allow_win32 in the plugin settings", code="disabled")
    return _ok(**get_desk().win32(str(args["dll"]), str(args["function"]), list(args.get("args") or [])))


@_guarded
def art98_revert(args: dict) -> Any:
    return _after(get_desk().revert(), args)


@_guarded
def art98_set_state(args: dict) -> Any:
    path = media.resolve("states", str(args["state"]))
    if not path.is_file():
        return _err(f"no state file {path.name!r} in states/", code="not_found")
    return _after(get_desk().set_state(str(path)), args)


@_guarded
def art98_record(args: dict) -> Any:
    action = args["action"]
    if action not in ("start", "stop"):
        raise ValueError("action must be 'start' or 'stop'")
    out = None
    if action == "start":
        if not args.get("name"):
            raise ValueError("'start' needs a name")
        out = str(media.resolve("recordings", str(args["name"])))
    return _ok(**get_desk().record(action, out))


@_guarded
def art98_render(args: dict, session_id: str = "") -> Any:
    scene = media.resolve("animations", str(args["animation"]))
    if not scene.is_file():
        return _err(f"no animation {scene.name!r} in animations/", code="not_found")
    out = media.resolve("recordings", str(args["output"]))
    if out.suffix.lower() not in (".mp4", ".gif"):
        return _err("output must end in .mp4 or .gif", code="bad_arguments")
    name = args.get("scene") or None
    if name is not None and not str(name).isidentifier():
        return _err("scene must be a class name, e.g. 'Showcase'", code="bad_arguments")
    desk = get_desk()
    if not getattr(desk, "runs_code", False):
        return _ok(**desk.render(str(scene), str(out), name))
    if not config.load()["allow_render"]:
        return _err("art98_render is off: it runs the scene script as Python on the Hermes machine. "
                    "The user can turn on allow_render in the plugin settings", code="disabled")
    with _snapshot(scene) as (copy, files):
        if not _take_approval(session_id, scene, _digest(files)):
            return _err("art98_render runs the scene as Python, so the user approves each render, and "
                        "this one wasn't approved (declined, nobody there to ask, or the script "
                        "changed after it was approved)", code="not_approved")
        return _ok(**desk.render(str(copy), str(out), name))


def _imported(text: str) -> set[str]:
    """Top-level names a script imports (absolute imports only), read with ast."""
    import ast
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return set()
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module.split(".")[0])
    return names


_CODE = {".py", ".pyw", ".pyc", ".pyo", ".so", ".pth"}  # what Python could import or run


def _has_python(folder) -> bool:
    """True if the folder holds anything importable, or a symlink (which could lead to some)."""
    return any(p.is_symlink() or p.suffix in _CODE for p in folder.rglob("*"))


def _snapshot(scene):
    """Copy the scene and every animations/ module it imports into a fresh folder, so what runs
    is exactly what was approved (a later art98_save_scene can't swap it). Asset and state
    folders and other files beside the scene are linked in, but only ones holding no Python;
    anything with code is either copied (and shown) or left out."""
    import contextlib
    import shutil
    import tempfile
    from pathlib import Path

    @contextlib.contextmanager
    def run():
        src = scene.parent
        files, dirs = _collect(scene)
        tmp = Path(tempfile.mkdtemp(prefix=".run-", dir=media.data_root()))
        try:
            for rel, text in files.items():
                (tmp / rel).parent.mkdir(parents=True, exist_ok=True)
                (tmp / rel).write_text(text, encoding="utf-8")
            for entry in src.iterdir():
                if entry.name in files or entry.name in dirs or entry.name.startswith((".", "__")):
                    continue
                if entry.is_dir() and not _has_python(entry):
                    (tmp / entry.name).symlink_to(entry, target_is_directory=True)
                elif entry.is_file() and not entry.is_symlink() and entry.suffix not in _CODE:
                    (tmp / entry.name).symlink_to(entry)  # images, JSON and the like
            yield tmp / scene.name, files
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    return run()


def _collect(scene) -> tuple[dict[str, str], set[str]]:
    """The scene and every animations/ module or package it imports: {relative path: text}, and
    the package folders among them."""
    src = scene.parent
    files: dict[str, str] = {}
    dirs: set[str] = set()
    todo = [scene.name]
    while todo:
        rel = todo.pop()
        if rel in files:
            continue
        files[rel] = (src / rel).read_text(encoding="utf-8")
        for mod in _imported(files[rel]):
            if (src / f"{mod}.py").is_file():
                todo.append(f"{mod}.py")
            elif (src / mod).is_dir() and mod not in dirs and not mod.startswith("."):
                dirs.add(mod)
                todo.extend(str(p.relative_to(src)) for p in sorted((src / mod).rglob("*.py")))
    return files, dirs


def _digest(files: dict[str, str]) -> str:
    """sha256 of the scene and the animations/ modules it imports (the Python that gets copied;
    data files beside it are linked, not hashed)."""
    import hashlib
    h = hashlib.sha256()
    for rel in sorted(files):
        for part in (rel, files[rel]):
            data = part.encode("utf-8")
            h.update(len(data).to_bytes(8, "big") + data)
    return h.hexdigest()


# Renders the pre_tool_call hook sent to Hermes's approval gate, waiting for their art98_render
# call: (session, scene path, sha256) -> times asked. The hook runs before the user answers, so
# post_tool_call takes back any whose call didn't go through (denied, nobody there, cancelled), and the
# rest lapse soon after the gate's own timeout. The hook and the handler run in the same process
# (Hermes, or its plugin host), so this is plain memory: a restart in between refuses the render.
_approvals: dict[tuple[str, str, str], list[float]] = {}
_asked_by_call: dict[str, tuple[str, str, str]] = {}  # tool_call_id -> its key
_approvals_lock = threading.Lock()


def _approval_ttl() -> float:
    """approvals.timeout (how long Hermes waits for an answer, 300 s by default) plus a minute."""
    try:
        from hermes_cli.config import load_config_readonly
        timeout = int(((load_config_readonly() or {}).get("approvals") or {}).get("timeout", 300))
    except Exception:  # noqa: BLE001
        timeout = 300
    return max(timeout, 30) + 60


def _drop_stale(now: float, ttl: float) -> None:
    for key in list(_approvals):
        _approvals[key] = [t for t in _approvals[key] if now - t < ttl]
        if not _approvals[key]:
            del _approvals[key]
    for call, key in list(_asked_by_call.items()):
        if key not in _approvals:
            del _asked_by_call[call]


def _take_approval(session_id: str, scene, digest: str) -> bool:
    """True once per approval the hook asked for with this exact digest and Hermes didn't block.
    Outside Hermes (tests, demo.py) there is no gate and nothing to wait for."""
    import sys
    import time
    if "hermes_cli" not in sys.modules:
        return True
    key = (session_id or "", str(scene), digest)
    ttl = _approval_ttl()
    with _approvals_lock:
        _drop_stale(time.monotonic(), ttl)
        asked = _approvals.get(key)
        if not asked:
            return False
        asked.pop(0)
        if not asked:
            del _approvals[key]
        return True


def pre_tool_call(tool_name: str = "", args: dict | None = None, session_id: str = "",
                  tool_call_id: str = "", **_: Any):
    """Hermes pre_tool_call hook: send every art98_render that would run Python to Hermes's
    human-approval gate, naming the scripts and the sha256 of the scene and the animations/
    modules it imports. The gate follows Hermes's approval settings (it asks the user, or refuses
    when nobody can answer, unless approvals say otherwise). The handler hashes its snapshot again
    and refuses unless it matches an approval that wasn't blocked."""
    import time
    if tool_name != "art98_render" or not isinstance(args, dict):
        return None
    try:
        if not getattr(get_desk(), "runs_code", False) or not config.load()["allow_render"]:
            return None  # nothing runs: the stand-in backends, or the handler refuses with disabled
        scene = media.resolve("animations", str(args.get("animation", "")))
        if not scene.is_file():
            return None  # the handler answers not_found
        media.resolve("recordings", str(args.get("output", "")))
        if Path(str(args.get("output", ""))).suffix.lower() not in (".mp4", ".gif"):
            return None  # the handler answers bad_arguments
        name = args.get("scene") or None
        if name is not None and not str(name).isidentifier():
            return None  # likewise
        files, _ = _collect(scene)
    except Exception:  # noqa: BLE001  (bad arguments: the handler reports them, and refuses to run)
        return None
    digest = _digest(files)
    key = (session_id or "", str(scene), digest)
    ttl = _approval_ttl()
    with _approvals_lock:
        now = time.monotonic()
        _drop_stale(now, ttl)
        _approvals.setdefault(key, []).append(now)
        if tool_call_id:
            _asked_by_call[tool_call_id] = key
    return {"action": "approve", "message": _approval_message(scene.name, files, digest),
            "rule_key": f"art98_render:{digest}"}


def _approval_message(scene_rel: str, files: dict[str, str], digest: str) -> str:
    """The text under Hermes's approval prompt. Hermes wraps it at about 64 columns and shows at
    most 10 rows, cutting from the end, so: the risk first, at most 3 file names, the scope its
    option labels can't state, and the hash last on a row of its own."""
    rels = [scene_rel] + sorted(rel for rel in files if rel != scene_rel)
    shown = ", ".join(f"animations/{rel}" for rel in rels[:3])
    more = f" +{len(rels) - 3} more" if len(rels) > 3 else ""
    return "\n".join([
        "Runs agent-written Python on the Hermes machine, with your file",
        "access and the VM token; Hermes's command checks don't apply.",
        f"Files: {shown}{more}",
        '"Session"/"permanent" cover only this exact content; editing',
        'these files asks again. "Permanent" also runs it unprompted',
        "in cron, -q and other unattended runs.",
        "sha256 of these files:",
        digest,
    ])


_TAKE_BACK = {"blocked", "cancelled", "timeout"}  # an extra take-back can only refuse, never run


def post_tool_call(tool_name: str = "", tool_call_id: str = "", status: str | None = None, **_: Any):
    """Hermes post_tool_call hook: take back the approval an art98_render asked for when the call
    didn't go through (blocked by the gate, cancelled or timed out while the prompt was open)."""
    if tool_name != "art98_render" or not tool_call_id:
        return None
    with _approvals_lock:
        key = _asked_by_call.pop(tool_call_id, None)
        if key is not None and status in _TAKE_BACK and _approvals.get(key):
            _approvals[key].pop()
            if not _approvals[key]:
                del _approvals[key]
    return None


MAX_SCENE_BYTES = 256 * 1024


@_guarded
def art98_save_scene(args: dict) -> Any:
    path = media.resolve("animations", str(args["name"]))
    if path.suffix.lower() != ".py":
        return _err("name must end in .py", code="bad_arguments")
    source = args["source"]
    if not isinstance(source, str):
        return _err("source must be the script as text", code="bad_arguments")
    if len(source.encode("utf-8")) > MAX_SCENE_BYTES:
        return _err(f"source is over {MAX_SCENE_BYTES // 1024} KB", code="bad_arguments")
    try:
        scenes = media.scene_classes(source)
    except SyntaxError as e:
        return _err(f"SyntaxError: {e.msg} (line {e.lineno})", code="syntax_error", line=e.lineno)
    except ValueError as e:
        return _err(f"not valid Python: {e}", code="syntax_error")
    replaced = path.exists()
    if replaced and not args.get("overwrite"):
        return _err(f"{path.name} already exists; set overwrite to replace it", code="exists")
    if not source.endswith("\n"):
        source += "\n"
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(source, encoding="utf-8")
    tmp.replace(path)  # atomic: a render never reads half a file
    note = None
    if scenes is None:
        note = "no class in it, so it can't be rendered on its own (a helper other scripts import)"
    elif not scenes:
        note = "no class built on Scene found here; a render picks the file's only scene"
    return _ok(saved=path.name, bytes=path.stat().st_size, scenes=scenes or [], replaced=replaced,
               **({"note": note} if note else {}))


@_guarded
def art98_combine(args: dict) -> Any:
    names = args["recordings"]
    if not isinstance(names, list) or len(names) < 2:
        return _err("recordings must list at least two file names", code="bad_arguments")
    inputs = [media.resolve("recordings", str(n)) for n in names]
    missing = [p.name for p in inputs if not p.is_file()]
    if missing:
        return _err(f"not in recordings/: {', '.join(missing)}", code="not_found",
                    available=[f["name"] for f in media.listing("recordings")][:30])
    out = media.resolve("recordings", str(args["output"]))
    if out.suffix.lower() not in (".mp4", ".gif"):
        return _err("output must end in .mp4 or .gif", code="bad_arguments")
    if out.exists() and not args.get("overwrite"):
        return _err(f"{out.name} already exists; pick another name or set overwrite", code="exists")
    size = None
    if args.get("size"):
        try:
            w, h = (int(v) for v in str(args["size"]).lower().split("x"))
        except ValueError:
            return _err("size must look like 1920x1080", code="bad_arguments")
        if not (16 <= w <= 3840 and 16 <= h <= 2160):
            return _err("size must be between 16x16 and 3840x2160", code="bad_arguments")
        size = (w, h)
    try:
        result = _combine.combine(inputs, out, size=size, fps=config.load()["record_fps"],
                                  fade=float(args.get("fade") or 0))
    except _combine.CombineError as e:
        return _err(str(e), code="combine_failed")
    return _ok(**result)
