"""Backend routes for the art98 dashboard tab and desktop pane, mounted at /api/plugins/art98/."""

from __future__ import annotations

import importlib.util
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import FileResponse, Response

_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_art98_dashboard_{name}", _ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


media = _load("media")
desk = _load("desk")
history = _load("history")
config = _load("config")
vnc = _load("vnc")

router = APIRouter()


@router.get("/status")
async def status():
    return {**desk.get_desk().status(), "media": media.counts(), "folder": str(media.data_root())}


@router.get("/media/{kind}")
async def media_list(kind: str):
    try:
        return {"kind": kind, "files": media.listing(kind)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/media/{kind}/{name}")
async def media_file(kind: str, name: str):
    """One file from a media folder: a video, a scene script, a state or a screenshot."""
    try:
        path = media.resolve(kind, name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not path.is_file() or path.suffix.lower() not in media.KINDS[kind]:
        raise HTTPException(status_code=404, detail=f"no {kind} file {name!r}")
    return FileResponse(path)


@router.get("/history")
async def history_list(limit: int = 50):
    return {"entries": history.recent(min(max(limit, 1), 500))}


@router.get("/live")
def live(since: int | None = None):
    """The guest screen right now, as PNG (a plain def: FastAPI runs it off the event loop).

    With the real backend and a VM started with --vnc, the frame comes from vnc.py's view-only
    watcher, which needs no VM lock and so keeps updating while a render runs; `since` (the last
    X-Art98-Frame) answers 204 when nothing changed. Otherwise it's a screenshot through the
    backend, which answers 503 while no VM is attached or a render holds it.
    """
    headers = {"Cache-Control": "no-store"}
    if desk.get_desk().name == "drive":
        got = vnc.watcher().frame(since)
        if got:
            version, png = got
            headers.update({"X-Art98-Frame": str(version), "X-Art98-Source": "vnc"})
            if png is None:
                return Response(status_code=204, headers=headers)
            return Response(content=png, media_type="image/png", headers=headers)
    try:
        png = desk.get_desk().screenshot()
    except (desk.NotConnected, desk.DeskError) as e:
        raise HTTPException(status_code=503, detail=str(e))
    headers["X-Art98-Source"] = "api"
    return Response(content=png, media_type="image/png", headers=headers)


def _scene_classes(path: Path) -> list[str] | None:
    """media.scene_classes for a file; a file that can't be read or parsed has no scenes ([])."""
    try:
        return media.scene_classes(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return []


_scene_cache: dict[str, tuple[float, list[str] | None]] = {}  # name -> (mtime, classes)


@router.get("/scenes")
def scenes():
    """Scene classes in each animation script, so the dashboard can offer one Render per scene.
    Plain def: FastAPI runs it in a thread, so parsing never stalls other requests. A file is
    parsed again only when its mtime changes."""
    out = {}
    for f in media.listing("animations"):
        cached = _scene_cache.get(f["name"])
        if cached is None or cached[0] != f["mtime"]:
            cached = (f["mtime"], _scene_classes(media.resolve("animations", f["name"])))
            _scene_cache[f["name"]] = cached
        out[f["name"]] = cached[1]
    return {"scenes": out}


_render_lock = threading.Lock()


def _render_job(scene_path: Path, out: Path, scene: str | None) -> None:
    """Runs on its own thread. History is written before the lock goes, so a run is never
    finished-but-unlogged while another can start."""
    start = time.monotonic()
    ok, code, message = True, None, ""
    try:
        desk.get_desk().render(str(scene_path), str(out), scene)
    except desk.DeskError as e:
        ok, code, message = False, e.code, str(e)
    except desk.NotConnected as e:
        ok, code, message = False, "not_connected", str(e)
    except Exception as e:  # noqa: BLE001  (a run's failure goes to history, not the log)
        ok, code, message = False, "error", f"{type(e).__name__}: {e}"
    finally:
        try:
            history.log_run(scene_path.name, out, ok, code, message, start)
        finally:
            _render_lock.release()


@router.post("/render", status_code=202)
async def render(body: dict = Body(...)):
    """Start rendering a script from animations/ into recordings/. Answers at once (202);
    the run shows in Status while it goes and in History when it ends."""
    name = str(body.get("animation") or "")
    scene = body.get("scene") or None
    try:
        scene_path = media.resolve("animations", name)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if not scene_path.is_file() or scene_path.suffix.lower() != ".py":
        raise HTTPException(status_code=404, detail=f"no animation {name!r} in animations/")
    if scene is not None and not str(scene).isidentifier():
        raise HTTPException(status_code=400, detail="scene must be a class name")
    out = media.resolve("recordings", f"{scene_path.stem}_{scene}.mp4" if scene else f"{scene_path.stem}.mp4")
    if not _render_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="a render from the dashboard is already running")
    replaces = out.exists()  # a render overwrites its own earlier video; the page says so
    try:
        threading.Thread(target=_render_job, args=(scene_path, out, scene), daemon=True,
                         name="art98-dashboard-render").start()
    except RuntimeError:
        _render_lock.release()
        raise
    return {"started": name, "scene": scene, "output": out.name, "replaces": replaces}


@router.get("/config")
async def config_get():
    return {"values": config.load(), "schema": config.schema()}


@router.put("/config")
async def config_put(changes: dict = Body(...)):
    try:
        return {"values": config.update(changes), "schema": config.schema()}
    except config.ConfigError as e:
        raise HTTPException(status_code=400, detail=str(e))
