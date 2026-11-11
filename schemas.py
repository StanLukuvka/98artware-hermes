"""Model-facing tool schemas. Coordinates are Windows 98 guest pixels (top-left origin, y down)."""

def _tool(name: str, description: str, properties: dict | None = None, required: list | None = None) -> dict:
    return {
        "name": name,
        "description": description,
        "parameters": {"type": "object", "properties": properties or {}, "required": required or []},
    }


SHOT_AFTER = {"screenshot_after": {"type": "boolean", "default": True,
                                   "description": "Return a screenshot of the guest after the action."}}

ART98_STATUS = _tool(
    "art98_status",
    "Whether the Windows 98 VM is attached, which backend drives it, and how many files each media folder holds.",
)
ART98_MEDIA = _tool(
    "art98_media",
    "List the plugin's media folders: recordings (videos), animations (scene scripts), states (desktop state "
    "JSON) or screenshots. Newest first.",
    {"kind": {"type": "string", "enum": ["recordings", "animations", "states", "screenshots"]}},
    ["kind"],
)
ART98_SCREENSHOT = _tool(
    "art98_screenshot",
    "Capture the Windows 98 screen as an image, plus the cursor position and the foreground window.",
    {"save": {"type": "boolean", "default": False, "description": "Also keep the PNG in screenshots/."}},
)
ART98_CLICK = _tool(
    "art98_click",
    "Move the pointer to (x, y) on the Windows 98 screen and click.",
    {"x": {"type": "integer", "minimum": 0}, "y": {"type": "integer", "minimum": 0},
     "button": {"type": "string", "enum": ["left", "right"], "default": "left"},
     "double": {"type": "boolean", "default": False}, **SHOT_AFTER},
    ["x", "y"],
)
ART98_TYPE = _tool(
    "art98_type",
    "Type text into whatever has keyboard focus in Windows 98.",
    {"text": {"type": "string"}, **SHOT_AFTER},
    ["text"],
)
ART98_KEY = _tool(
    "art98_key",
    "Press a key or chord in Windows 98, for example 'enter', 'alt+f4', 'ctrl+esc'.",
    {"keys": {"type": "string"}, **SHOT_AFTER},
    ["keys"],
)
ART98_WINDOWS = _tool(
    "art98_windows",
    "List top-level Windows 98 windows: handle, class, title, rectangle, visible.",
)
ART98_OPEN = _tool(
    "art98_open",
    "Start a program in Windows 98 (for example 'notepad.exe' or 'mspaint.exe') and return its window.",
    {"program": {"type": "string"}, **SHOT_AFTER},
    ["program"],
)
ART98_WIN32 = _tool(
    "art98_win32",
    "Call any exported Win32 function in the guest, e.g. dll='user32', function='SetWindowPos'. "
    "Arguments are integers or strings. Blocking calls may time out.",
    {"dll": {"type": "string"}, "function": {"type": "string"},
     "args": {"type": "array", "items": {"type": ["integer", "string"]}, "default": []}},
    ["dll", "function"],
)
ART98_REVERT = _tool(
    "art98_revert",
    "Put Windows 98 back to its clean 'ready' snapshot. Every open window and process is lost.",
    {**SHOT_AFTER},
)
ART98_SET_STATE = _tool(
    "art98_set_state",
    "Revert, then build the desktop described by a state file from states/ (windows, positions, pointer).",
    {"state": {"type": "string", "description": "File name in states/, e.g. 'two_windows.json'."}, **SHOT_AFTER},
    ["state"],
)
ART98_RECORD = _tool(
    "art98_record",
    "Start or stop recording the Windows 98 screen to a video in recordings/.",
    {"action": {"type": "string", "enum": ["start", "stop"]},
     "name": {"type": "string", "description": "Output file name for 'start', e.g. 'demo.mp4'."}},
    ["action"],
)
ART98_RENDER = _tool(
    "art98_render",
    "Render an art98 animation (scene script in animations/) to a video in recordings/.",
    {"animation": {"type": "string", "description": "File name in animations/, e.g. 'intro.py'."},
     "output": {"type": "string", "description": "File name in recordings/, e.g. 'intro.mp4'."}},
    ["animation", "output"],
)
