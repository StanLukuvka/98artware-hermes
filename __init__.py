"""art98 for Hermes: drive a real Windows 98 SE VM. Registers the art98 toolset and its skill."""

from pathlib import Path

try:
    from . import schemas, tools
except ImportError:  # Hermes may load plugin modules directly.
    import schemas  # type: ignore[no-redef]
    import tools  # type: ignore[no-redef]

TOOLSET = "art98"
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
    ("art98_set_state", schemas.ART98_SET_STATE, tools.art98_set_state),
    ("art98_record", schemas.ART98_RECORD, tools.art98_record),
    ("art98_render", schemas.ART98_RENDER, tools.art98_render),
]


def register(ctx) -> None:
    for name, schema, handler in TOOLS:
        ctx.register_tool(name=name, toolset=TOOLSET, schema=schema, handler=handler, emoji="🪟")
    ctx.register_skill("art98", Path(__file__).parent / "skills" / "art98" / "SKILL.md")
