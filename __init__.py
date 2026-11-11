"""art98 for Hermes: drive a real Windows 98 SE VM. Registers the art98 toolset and its skill."""

from pathlib import Path

from . import config, schemas, tools

TOOLSET = "art98"
# art98_set_state and art98_record exist in schemas.py and tools.py but aren't registered until
# the real backend can do them (DriveDesk answers not_built), so models don't spend turns on them.
TOOLS = [
    ("art98_status", schemas.ART98_STATUS, tools.art98_status),
    ("art98_media", schemas.ART98_MEDIA, tools.art98_media),
    ("art98_screenshot", schemas.ART98_SCREENSHOT, tools.art98_screenshot),
    ("art98_click", schemas.ART98_CLICK, tools.art98_click),
    ("art98_type", schemas.ART98_TYPE, tools.art98_type),
    ("art98_key", schemas.ART98_KEY, tools.art98_key),
    ("art98_windows", schemas.ART98_WINDOWS, tools.art98_windows),
    ("art98_open", schemas.ART98_OPEN, tools.art98_open),
    ("art98_win32", schemas.ART98_WIN32, tools.art98_win32),
    ("art98_revert", schemas.ART98_REVERT, tools.art98_revert),
    ("art98_save_scene", schemas.ART98_SAVE_SCENE, tools.art98_save_scene),
    ("art98_render", schemas.ART98_RENDER, tools.art98_render),
    ("art98_combine", schemas.ART98_COMBINE, tools.art98_combine),
]


def register(ctx) -> None:
    config.bind(ctx)
    for name, schema, handler in TOOLS:
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler, emoji="🪟")
    ctx.register_skill("art98", Path(__file__).parent / "skills" / "art98" / "SKILL.md")
