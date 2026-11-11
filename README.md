# art98 plugin for Hermes

Lets a Hermes agent drive a real Windows 98 SE VM through art98. **Stub stage:** every
VM tool answers `not_connected` until a real backend replaces `StubDesk` in `desk.py`.

```
art98/
├── plugin.yaml          agent half: name, version, the 13 tools it provides
├── __init__.py          register(ctx): the art98 toolset and the art98 skill
├── schemas.py           what the model sees for each tool
├── tools.py             handlers: args → Desk → JSON or text+image (_multimodal)
├── desk.py              the seam to art98: Desk interface, StubDesk, get_desk()
├── media.py             the media folders below: list, resolve safe names
├── skills/art98/        SKILL.md: how the model should use the tools
├── dashboard/           plugin_api.py: /api/plugins/art98/status and /media/<kind>
│                        manifest.json + dist/index.js: the /art98 tab in `hermes dashboard`
├── desktop/plugin.js    Hermes Desktop pane "Windows 98": status and media lists
├── recordings/          videos (.mp4, .gif)
├── animations/          art98 scene scripts (.py)
├── states/              desktop state files (.json)
└── screenshots/         saved frames (.png)
```

## Turning it on

0. Hermes finds it through a symlink: `~/.hermes/plugins/art98` → this folder. The link's
   name is the plugin id. Recreate it with
   `ln -s "$PWD" ~/.hermes/plugins/art98` from this folder if it goes missing.
1. Agent half: `hermes plugins enable art98` (adds it to `plugins.enabled` in config.yaml).
   This also imports `dashboard/plugin_api.py` for the pane.
2. Web dashboard: `hermes dashboard`, then http://127.0.0.1:9119/art98 (restart the
   dashboard after changing `dashboard/manifest.json`, or the nav tab won't show).
3. Desktop half: Hermes Desktop → Settings → Plugins → turn on "art98 (Windows 98)".
   ⌘K → Reload desktop plugins if it doesn't show.

## Next

- A real `Desk` that talks to an art98 service on this PC (`art98 drive`), chosen in
  `get_desk()`. Nothing else in the plugin should need to change.
- The dashboard loads its own copy of `desk.py`. Once the Desk holds a live
  connection, the pane should ask the service for status instead.
- The media folders live inside the plugin folder. `hermes plugins install --force`
  or removing the plugin would delete them, so keep anything precious elsewhere too.
