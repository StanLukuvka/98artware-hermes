# art98 plugin for Hermes

Lets a Hermes agent drive a real Windows 98 SE VM through art98. The `drive`
backend talks to the art98 VM container; `stub` and `demo` work without one.

```
art98/
├── plugin.yaml          agent half: name, version, the 13 tools it provides, settings schema
├── __init__.py          register(ctx): the art98 toolset and the art98 skill
├── schemas.py           what the model sees for each tool
├── tools.py             handlers: args → Desk → JSON or text+image (_multimodal)
├── desk.py              the seam to art98: Desk interface, StubDesk, DemoDesk, DriveDesk (real), get_desk()
├── media.py             the data folders (see below): list, resolve safe names, data_root()
├── config.py            settings with defaults and checks; saved in Hermes's config.yaml via ctx.set_config
├── vnc.py               view-only VNC reader for the dashboard's live view
├── combine.py           joins videos with ffmpeg (art98_combine); also a CLI
├── history.py           each script run (art98_render) appended to history.jsonl in the data folder
├── demo.py              placeholder data: `python3 demo.py seed` / `clear`
├── skills/art98/        SKILL.md: how the model should use the tools
├── standalone-skills/art98-install/  SKILL.md: setting up art98 and this plugin (installed on its own)
├── dashboard/           plugin_api.py: the /api/plugins/art98/... routes below
│                        manifest.json + dist/index.js + dist/style.css: the /art98 tab
├── desktop/plugin.js    Hermes Desktop pane "Windows 98": status and media lists
└── states/              starter desktop state files (.json), copied to the data folder on first use
```

What the plugin makes lives outside this folder, in Hermes's per-plugin data folder
(`<HERMES_HOME>/plugin-data/art98/`, from `plugin_data_dir("art98")`), so `hermes plugins update`
or `remove` never touches it. In the Docker image that is `/opt/data/plugin-data/art98/`:

```
plugin-data/art98/
├── recordings/          videos (.mp4, .gif)
├── animations/          art98 scene scripts (.py)
├── states/              desktop state files (.json)
├── screenshots/         saved frames (.png)
└── history.jsonl        one line per render
```

Outside Hermes (tests, `demo.py`) the folders fall back to this folder, or to `ART98_DATA` if set.
Settings are in Hermes's `config.yaml` under `plugins.entries.art98.settings`, declared in
`plugin.yaml`'s `config_schema`, so Hermes's own plugin settings form shows them too.

## The /art98 tab

Left: **Status** (with refresh and a cog that opens the settings popout), **Stored
animation scripts** (sortable table) and **History**: one row per script run with its
result and, when it worked, a Watch link that plays its recording. Right: **Video output**
with Live and Recording tabs; Recording plays what you picked in History, else the newest. It uses the dashboard's own components and table styling, so it
follows the dashboard theme; `dist/style.css` only holds layout sizes the dashboard's CSS
build doesn't include (a plugin can't add Tailwind classes, since there's no build step).

Routes under `/api/plugins/art98/`:

| Route | What |
|---|---|
| `GET status` | backend, connection, media counts |
| `GET media/<kind>` / `GET media/<kind>/<name>` | list a media folder / fetch one file |
| `GET history?limit=100` | recent script runs, newest first |
| `GET live?since=` | PNG of the guest screen: over VNC when the VM runs with `--vnc` (204 when unchanged since `since`), else a screenshot through the backend; 503 while neither is available |
| `GET scenes` | scene classes in each script in animations/ (read with ast, never run); null for a helper with no classes |
| `POST render` | `{animation, scene?}`: starts a render into recordings/ as `<stem>.mp4` (or `<stem>_<Scene>.mp4`), answers 202 at once; 409 while one is running. It replaces an earlier video of the same name (the reply's `replaces` says so). The run lands in History |
| `GET config` / `PUT config` | settings and their schema; PUT takes only the changed keys |

Settings (`config.py`, declared in `plugin.yaml`): backend, revert target, screenshot-after
default, allow `art98_win32` (off by default), live refresh rate (screenshot fallback only),
recording frame rate (used by `art98_combine`), and the data folder as your PC sees it (for
the Open in VS Code links).

### Demo data

`python3 demo.py seed` puts placeholder `demo_*` files in the media folders, adds 12 sample
runs (most with a 2 s test-pattern recording) and switches the backend to `demo`, which reports a running VM, a fake
render/record loop and a plain teal screen. `python3 demo.py clear` removes exactly those
and goes back to the stub. Actions still answer `not_connected` under `demo`.

## Requirements and behaviour

What the plugin needs, and what it does beyond Hermes's own tools:

- **The art98 library** (the `core/` folder of the 98artware repo), importable inside the
  Hermes process. `desk.py` adds `ART98_CORE` (default `/opt/art98/core`) to `sys.path`.
  Mount it read-only. No PyPI packages are needed. It needs a checkout that takes the token
  from the host program (`Settings.token` / `ART98_TOKEN`, after art98 0.1.0).
- **An art98 VM**, run by art98 in its own container. The plugin reaches it over two loopback
  TCP ports: the control API (`7598`, token-authenticated) and an optional view-only VNC port
  (`5900`, `ART98_VNC`, default `127.0.0.1:5900`). It opens no other network connections and
  never sends input over VNC.
- **Client settings**: a folder with art98's client `art98.toml`, `ART98_CLIENT` (default
  `/opt/art98-client`).
- **The VM token**, as the plugin's `vm_token` secret (stored by Hermes as `ART98_VM_TOKEN` and
  read with `get_secret`). The plugin never reads a token file.
- **Approval for renders**: `art98_render` runs the agent's scene script as Python, so it asks
  through the same guard as Hermes's `execute_code`: the user approves it wherever Hermes would
  ask for `execute_code`, and single-query, cron and other unattended runs follow their
  `approvals` settings (by default they refuse). Hermes has no public approval call for plugin
  tools yet, so this imports `tools.approval`; if a release moves it, renders are refused. The
  dashboard's own Render button is the user's click and doesn't ask.
- **Subprocesses**: `art98_render` runs `art98 render` in a child Python process, so scene
  code never runs in the Hermes process. The child gets a small environment (`PATH`, `HOME`,
  locale, `ART98_*` and the token), none of Hermes's keys. `art98_combine` runs `ffmpeg` and `ffprobe`, which
  must be on `PATH`.
- **Guest control**: the tools move the guest's mouse and type keys in the VM. `art98_win32`
  calls any Win32 function in the guest, and refuses unless the user turns on `allow_win32`.
- **Files**: it writes only to its data folder (`plugin_data_dir("art98")`, or `ART98_DATA`).

Never give the Hermes container art98's `work/` folder: it holds the Windows disk images and
the product key the user entered.

## Running it with the real backend (`drive`)

Set `backend` to `drive` in the settings. `DriveDesk` (desk.py) imports art98's library and
talks to the VM container's API; no art98 command runs outside the Hermes container.

- **VM side.** Keep the VM's host settings in their own folder (not the 98artware repo, whose
  `art98.toml` stays at its shipping defaults), with `[vm] transport = "tcp"` and a
  `token_file` outside `work/`. `art98 vm start` run from that folder publishes the control
  API on `127.0.0.1:7598` only; add `--vnc` for the live view on `127.0.0.1:5900`.
- **Hermes side.** Give the Hermes container:
  - `<98artware>/core` read-only at `/opt/art98/core`,
  - `<98artware>/guest/build` (the guest programs, e.g. `view.exe` for picture windows)
    read-only at `/opt/art98/guest/build`,
  - the client folder (the client `art98.toml`) at `/opt/art98-client`,
  - the host's loopback ports 7598 and 5900 inside the container's loopback. With rootless
    Podman's pasta network that is `-T 7598 -T 5900`; nothing is published beyond the PC.
- **How it drives.** Each action attaches (takes the VM's lock), acts, waits 0.3 s for the
  guest, and releases; the VM pauses in between with the desktop as it was. Screenshots read
  the paused screen without resuming it. A render runs `art98 render` in a child process with
  the same library; errors come back with the scene's line.
- **Live view.** With the VM started with `--vnc`, the dashboard's Live tab watches it over
  VNC: `vnc.py` is a view-only reader that needs no VM lock, so the screen keeps moving during
  renders, at about 6 fps. Without `--vnc` the tab falls back to screenshots through the
  backend, which pause while a render holds the VM.
- **The token.** Paste the contents of the VM's token file into the plugin's `vm_token`
  setting (Hermes Desktop → Settings → Plugins → art98), or add `ART98_VM_TOKEN=<token>` to
  Hermes's `.env`.
- Restart the Hermes container (or gateway) after changing `desk.py`, `dashboard/manifest.json`
  or `plugin_api.py`; edits to `dashboard/dist/` only need a page reload.

## Turning it on

1. Install: `hermes plugins install StanLukuvka/98artware-hermes --enable`, or for a local
   checkout link it as `~/.hermes/plugins/art98` (the link's name is the plugin id) and run
   `hermes plugins enable art98`.
2. Web dashboard: `hermes dashboard`, then http://127.0.0.1:9119/art98. Open the cog, set
   `backend` to `drive`, and restart the dashboard after the plugin's first install.
3. Desktop half: Hermes Desktop → Settings → Plugins → turn on "art98 (Windows 98)".
   ⌘K → Reload desktop plugins if it doesn't show.

## The skills

- `art98:art98` ships with the plugin (`skills/art98/`) and loads while the plugin is enabled: how
  to use the tools, render, fix a failed render and join videos.
- `art98-install` (`standalone-skills/art98-install/`) walks through installing art98 and this
  plugin. Plugin skills only exist once their plugin is enabled, so this one is installed on its
  own: `hermes skills install StanLukuvka/98artware-hermes/standalone-skills/art98-install`, or
  copy the folder into `~/.hermes/skills/devops/`.

## Next

- `set_state` and `record` on the real backend. Their schemas and handlers are written
  (`schemas.py`, `tools.py`) but not registered: DriveDesk would only answer `not_built`.
  Add them back to `__init__.py` and `plugin.yaml` once they work.
