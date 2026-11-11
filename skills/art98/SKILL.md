---
name: art98
description: "Drive a real Windows 98 SE desktop through the art98 tools: look, click, type, open programs, call Win32, revert, record."
version: 0.1.0
---

# Driving Windows 98 with art98

The `art98_*` tools control a real Windows 98 SE VM running under QEMU on this PC.
The screen is 800×600. Coordinates are guest pixels, origin top-left, y down: the
same pixels as the screenshots, with no scaling.

## Loop

1. `art98_status`. If `connected` is false, say so and stop. The backend is not attached yet.
2. `art98_screenshot` to look.
3. Act with `art98_click`, `art98_type`, `art98_key` or `art98_open`. Each one returns a
   screenshot afterwards by default, so you rarely need a separate screenshot.
4. `art98_windows` gives exact window rectangles when a screenshot is ambiguous.
5. `art98_revert` puts the desktop back to a clean state at any time. Use it freely: the VM
   is disposable.

## Media folders

The plugin keeps its files beside its code. `art98_media` lists them:

- `recordings/`: videos from `art98_record` and `art98_render`
- `animations/`: art98 scene scripts that `art98_render` can turn into video
- `states/`: desktop state files for `art98_set_state`
- `screenshots/`: PNGs saved by `art98_screenshot` with `save: true`

File arguments are bare names inside those folders, never paths.

## Notes

- `art98_win32` reaches any exported Win32 function. Prefer the plain tools, and use it
  for things they can't do (move or resize a window exactly, read window text).
- Windows 98 has no network here, and nothing in the guest can reach this PC.
