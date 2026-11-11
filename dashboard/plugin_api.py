"""Backend routes for the art98 desktop pane, mounted at /api/plugins/art98/."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import APIRouter, HTTPException

_ROOT = Path(__file__).resolve().parent.parent


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"_art98_dashboard_{name}", _ROOT / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


media = _load("media")
desk = _load("desk")

router = APIRouter()


@router.get("/status")
async def status():
    return {**desk.get_desk().status(), "media": media.counts(), "folder": str(_ROOT)}


@router.get("/media/{kind}")
async def media_list(kind: str):
    try:
        return {"kind": kind, "files": media.listing(kind)}
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
