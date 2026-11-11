"""Tool handlers. Each takes the model's args, calls the Desk, and returns a JSON string
(or a _multimodal dict when there is a screenshot). Handlers never raise."""

from __future__ import annotations

import base64
import json
import time
from typing import Any, Callable

try:
    from . import media
    from .desk import NotConnected, get_desk
except ImportError:  # Hermes may load plugin modules directly.
    import media  # type: ignore[no-redef]
    from desk import NotConnected, get_desk  # type: ignore[no-redef]


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


def _guarded(fn: Callable[[dict], Any]) -> Callable[..., Any]:
    def handler(args: dict, **kwargs: Any) -> Any:
        try:
            return fn(args or {})
        except NotConnected as e:
            return _err(str(e), code="not_connected", backend=get_desk().name)
        except (ValueError, TypeError, KeyError) as e:
            return _err(f"bad arguments: {e}", code="bad_arguments")
        except Exception as e:  # deliberate boundary: a tool must never raise into the agent loop
            return _err(f"{type(e).__name__}: {e}", code="internal")
    handler.__name__ = fn.__name__
    return handler


def _after(result: dict, args: dict) -> Any:
    """Attach a screenshot when the action asked for one (the default)."""
    if not args.get("screenshot_after", True):
        return _ok(**result)
    return _with_image({"ok": True, **result}, get_desk().screenshot())


@_guarded
def art98_status(args: dict) -> Any:
    return _ok(**get_desk().status(), media=media.counts())


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
    return _ok(**get_desk().render(str(scene), str(out)))
