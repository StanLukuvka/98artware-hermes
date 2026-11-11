"""Tool handlers. Each takes the model's args, calls the Desk, and returns a JSON string
(or a _multimodal dict when there is a screenshot). Handlers never raise."""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Callable

try:
    from . import combine as _combine
    from . import config, history, media
    from .desk import DeskError, NotConnected, get_desk
except ImportError:  # Hermes may load plugin modules directly.
    import combine as _combine  # type: ignore[no-redef]
    import config  # type: ignore[no-redef]
    import history  # type: ignore[no-redef]
    import media  # type: ignore[no-redef]
    from desk import DeskError, NotConnected, get_desk  # type: ignore[no-redef]


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


def _run(fn: Callable[[dict], Any], args: dict) -> Any:
    try:
        return fn(args)
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
    return _ok(**get_desk().status(), media=media.counts(), settings=config.load())


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
def art98_render(args: dict) -> Any:
    scene = media.resolve("animations", str(args["animation"]))
    if not scene.is_file():
        return _err(f"no animation {scene.name!r} in animations/", code="not_found")
    out = media.resolve("recordings", str(args["output"]))
    if out.suffix.lower() not in (".mp4", ".gif"):
        return _err("output must end in .mp4 or .gif", code="bad_arguments")
    name = args.get("scene") or None
    if name is not None and not str(name).isidentifier():
        return _err("scene must be a class name, e.g. 'Showcase'", code="bad_arguments")
    return _ok(**get_desk().render(str(scene), str(out), name))


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
