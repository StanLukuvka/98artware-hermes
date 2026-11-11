---
name: art98
description: Drive a Windows 98 VM and render art98 scenes.
version: 0.5.0
author: StanLukuvka
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [windows-98, video, desktop, animation]
    category: creative
    related_skills: [art98-install]
---

# art98 Skill

The `art98_*` tools control a real Windows 98 SE desktop running in its own VM container, and
turn art98 scene scripts into videos. Use them to look at the desktop, click and type on it,
and render scenes from `animations/` into `recordings/`. They do not install anything: if the
plugin or the VM is missing, use the `art98-install` skill.

## When to Use

- The user wants something done on, or a video made of, the Windows 98 desktop.
- The user asks to render, fix or join art98 scene scripts, or to see what a script makes.
- A render failed and the user wants it fixed.

## Prerequisites

- This plugin enabled (`hermes plugins enable art98`), with the `backend` setting on `drive`.
  `stub` answers `not_connected` to everything and `demo` only fakes the status.
- The VM running on the host (`art98 vm start`). It sits paused between calls; that is normal.
- If either is missing, `art98_status` says so. Tell the user and point them at `art98-install`.

## How to Run

1. `art98_status`. If `connected` is false, say why (its `note`) and stop.
2. `art98_screenshot` to look. The screen is 800x600 guest pixels, origin top left, y down,
   the same pixels as the screenshot with no scaling.
3. Act with `art98_click`, `art98_type`, `art98_key` or `art98_open`. Each returns a screenshot
   afterwards by default, so a separate screenshot is rarely needed.
4. `art98_revert` puts the desktop back to a clean state at any time. The VM is disposable.

## Quick Reference

| Tool | Use |
| --- | --- |
| `art98_status` | Backend, VM state, whether something is rendering |
| `art98_screenshot` | Look; `save: true` keeps a PNG in `screenshots/` |
| `art98_click` | Click at guest pixels; `button`, `double` |
| `art98_type` | Type printable ASCII, `\n`, `\t` into the focused window (US keys, about 0.1 s each) |
| `art98_key` | Presses such as `enter`, `alt+f4`, `ctrl+s`, space separated |
| `art98_windows` | Exact window rectangles and titles |
| `art98_open` | Start a program |
| `art98_win32` | Any exported Win32 function; off unless the user enabled `allow_win32` |
| `art98_revert` | Clean desktop |
| `art98_media` | List `recordings/`, `animations/`, `states/`, `screenshots/` |
| `art98_save_scene` | Save a scene script into `animations/`; checks the Python, lists its scenes |
| `art98_render` | Scene script in `animations/` to a video in `recordings/` |
| `art98_combine` | Join videos from `recordings/` end to end; no VM needed |

File arguments are bare names inside the media folders, never paths.

## Procedure

### Writing a scene

Scene scripts live in the plugin's `animations/` folder, which is in Hermes's plugin data
(`/opt/data/plugin-data/art98/animations/` inside the container), not beside the plugin's code. The full scene docs are not in the container, so start
from this template or from a script that `art98_media` lists:

```python
from art98.anim import *

WM_NCACTIVATE = 0x0086


class Hello(Scene):
    fps = 24
    desktop = "blank"  # black, no icons or taskbar; "stock" is the normal desktop

    def construct(self):
        self.play(Backdrop((0, 128, 176)))  # every channel a multiple of 8
        self.pause(0.5)
        boxes = []
        for i, text in enumerate(["First.", "Second."]):
            box = Dialog(text, "???", icon="info", at=(250 + 24 * i, 235 + 18 * i), size=(300, 130))
            self.add(box)
            boxes.append(box)
            for each in boxes:  # only the active window has a blue title bar; make all of them blue
                self.guest.user32.SendMessageA(each.hwnd, WM_NCACTIVATE, 1, 0)
            self.pause(1)
        self.pause(1)
```

- `Backdrop(colour)` takes `(r, g, b)` or `teal`, `red`, `dark_red`, `white`, `black`.
- `Dialog(text, title, icon=, buttons=, at=, size=, disabled=)` is a real message box.
  `icon` is `error`, `question`, `warning`, `info`, or `computer`, `printer`, `mouse`,
  `telephone`, `cd`, `globe`, `flag`. `buttons` is `ok`, `okcancel`, `yesno` and so on. `size`
  can't be smaller than the stock box. Give every box in a group the same icon: a box with no
  icon puts its text in a different place.
- `self.add(x)` shows a thing at once, `self.play(x)` runs an action, `self.pause(s)` holds.
- The screen is 800x600. Keep `at` plus `size` inside it.

Save it with `art98_save_scene` (`name`, `source`, and `overwrite: true` to replace a script).
It checks the Python first, refuses a syntax error with its line, and answers with the scene
classes it found. `write_file` and `patch` often can't reach `animations/` (it is outside
`HERMES_WRITE_SAFE_ROOT`), so don't start with them.

The art98 tools may be deferred: if `art98_save_scene` (or any art98 tool) isn't in your tool
list, find it with `tool_search` and call it through `tool_call`. It is there; don't work around it.
Only on an older plugin that really has no `art98_save_scene`, write the file with `execute_code`,
and only for `.py` files in `animations/`:

```python
from pathlib import Path
Path("/opt/data/plugin-data/art98/animations/hello.py").write_text(src, encoding="utf-8")
```

### Rendering a scene

1. `art98_media` with `kind: animations` to find the script.
2. `art98_render` with `animation` and `output` (`.mp4` or `.gif`). If the file holds several
   scenes, name one with `scene`; the error lists them when you don't.
3. Every scene starts from a fresh, clean desktop, whatever the live tools did before.
4. A scene that calls `cut()` also writes its pieces as `<output>-01.mp4` and on.
5. To start from a saved desktop, put `self.set_state(...)` at the top of the scene.

### Fixing a failed render

A failed render answers `render_failed` with the error and the scene's line, such as
`NameError: ... (intro.py:18, in construct)`. Read the script with `read_file`, fix that line, save the
whole script again with `art98_save_scene` and `overwrite: true`, and render again. A file that defines no `Scene` class (a helper other scripts import)
can't be rendered on its own.

### Joining videos

`art98_combine` joins recordings in play order into a new file. Give `fade` (seconds) for
cross-fades, or leave it out for hard cuts. Other sizes are scaled with sharp pixels and
letterboxed to the first clip, or to `size` (such as `1280x720`). Output is silent. It won't
replace an existing file unless `overwrite` is set.

### The dashboard

The user can do the same from the Hermes dashboard's art98 tab
(`<dashboard>/art98`, by default `http://127.0.0.1:9119/art98`):
Render buttons per script and per scene, a History of runs with Watch and download, a Live
view of the screen, and one message line in Status for what just happened.

## Pitfalls

- `busy`: a render or another action holds the VM. Only one thing drives it at a time. Wait a
  few seconds and retry; don't loop fast.
- `disabled` from `art98_render`: the user hasn't turned on `allow_render`, because a render
  runs the script as Python on their machine. Tell them; don't turn it on yourself.
- `not_approved`: Hermes's approval for running code (the same one `execute_code` uses) refused
  the render, or no one could approve it, as in a single-query or cron run. Stop and tell the
  user. Don't render another way or change approval settings.
- A render writes over an existing video of the same name. Pick a new `output` to keep the old one.
- The VM's screen is 16-bit. A desktop colour comes out flat only when each of r, g and b is a
  multiple of 8; anything else, such as `(0, 126, 178)`, is painted as dotted lines.
  `(0, 128, 176)` is a flat Windows blue.
- Save scripts with `art98_save_scene`. `write_file` and `patch` are often refused in
  `animations/`.
- Windows 98 Notepad has no Ctrl+A.
- Prefer the plain tools over `art98_win32`; use it only for what they can't do (move or resize
  a window exactly, read window text).
- The guest has no network, and nothing in it can reach this machine.
- Never ask for, read or repeat the Windows product key. It stays on the host, in a folder the
  plugin never mounts.

## Verification

- `art98_status` shows `connected: true` and a VM state of `paused` or `running`.
- After a render, `art98_media` with `kind: recordings` lists the new file, and the run shows in
  the dashboard's History with a Watch link.
- To check the picture, take a frame with `ffmpeg -ss <s> -i <video> -frames:v 1` and crop a plain
  patch of background, scaled up with `-vf "crop=120:90:560:60,scale=480:360:flags=neighbor"`.
  Look for dotted lines there; a whole-frame "does it look right" misses them.
- Tell the user files by their folder and name, such as `animations/intro.py` and
  `recordings/intro.mp4`. The `/opt/data/...` paths exist only inside the container.
