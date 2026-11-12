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
├── states/              starter desktop state files (.json), copied to the data folder on first use
└── vendor/              the art98 library (art98/), its LICENSE, and VENDORED.md naming its commit
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

What the plugin needs, and everything it does beyond Hermes's own tools. Linux only.

- **Running agent code (`art98_render`).** A render runs the agent's scene script as Python on
  the Hermes machine, in a child process outside Hermes's terminal backend and without a sandbox.
  Two gates, both needed:
  - the `allow_render` setting, off by default. Off, `art98_render` refuses with `disabled`.
  - Hermes's approval for that render. The plugin's `pre_tool_call` hook sends every
    `art98_render` that would run Python to Hermes's human-approval gate, naming the scripts and
    the sha256 of the scene and the `animations/` modules it imports. The gate follows Hermes's
    approval settings: in an interactive session (CLI, TUI, Desktop, gateway) it asks the user;
    where nobody can answer it refuses, unless the matching setting says otherwise
    (`approvals.single_query_mode` for `-q`, `approvals.cron_mode` for cron,
    `approvals.unattended_mode` for unattended platforms, all `deny` by default). `--yolo` and
    `approvals.mode: off` approve without asking. "This session" and "Always" cover only that
    exact script content (the approval key is `art98_render:<sha256>`), and "Always" is kept by
    Hermes for every session: content approved that way also renders unasked in `-q`, cron and
    unattended runs, whatever their `approvals.*_mode`. Refused, the render
    answers `not_approved`, and the `post_tool_call` hook takes back what it asked for (also
    when the call is cancelled or times out). Hermes uses the first plugin's approval request for
    a call, so another plugin that also asks for approval of `art98_render` with a broader rule
    would decide what "Always" covers.
  - What is approved is what runs, for the Python the plugin can see: the scene and the
    `animations/` modules it imports are hashed for the approval, then copied to a temporary
    folder and hashed again, and the copy is rendered only if the two match an approval that
    wasn't refused (each approval is used once, and lapses about a minute after Hermes's approval timeout).
    Other files beside the scene (images, JSON) and folders holding no Python and no symlinks
    are linked in, not copied or hashed: a scene that runs their contents, for example with
    `exec(open(...).read())` or a dynamic import, runs content the approval didn't cover, and
    that content can change after approval. Approvals live in memory, so a restart of Hermes or
    its plugin host between the prompt and the render refuses it.
  - The dashboard's own Render button is the user's click: it renders with no approval.
- **The render's environment.** The child gets `PATH`, `HOME`, the locale, `ART98_*` settings and
  the VM token (`ART98_TOKEN`), none of Hermes's other variables. It is not a sandbox: scene
  code can read and write whatever the Hermes user can, including files under `HOME`, and it can
  read the VM token. A render is stopped after 15 minutes, with anything it started.
- **The art98 library is bundled** in `vendor/art98/` (MIT, `vendor/LICENSE`), copied unchanged
  from the 98artware repo by its `tools/vendor_to_hermes.py`, so it is pinned with the plugin;
  `vendor/VENDORED.md` names the source commit and `art98_status` reports it. `desk.py` refuses
  to start if another `art98` module is already loaded. Run the VM's server from a 98artware
  checkout at that commit. The guest programs (`view.exe` and the like) are Windows binaries and
  are never shipped: mount `98artware/guest/build` at `ART98_GUEST_BUILD` (default
  `/opt/art98/guest/build`). No PyPI packages are needed.
- **Network: loopback only.** The art98 VM's control API at the address in the client
  `art98.toml` (default `127.0.0.1:7598`, token-authenticated), and optionally its view-only VNC
  port (`ART98_VNC`, default `127.0.0.1:5900`, no password, nothing sent but frame requests).
  No other connections, no third-party services, no telemetry, no self-update.
- **One stored credential.** The VM token, as the plugin's `vm_token` secret, kept by Hermes as
  `ART98_VM_TOKEN` and read with `get_secret`. The plugin never reads a token file, `.env` or
  `auth.json`. The token goes to the control API address in the client `art98.toml`.
- **Other subprocesses.** `art98_combine` runs `ffmpeg` and `ffprobe`, which must be on `PATH`.
- **Background threads.** A VNC reader while the dashboard's Live tab is open (it stops after
  30 s with no viewer), and one thread per dashboard render.
- **Reads outside its data.** The guest programs folder (`ART98_GUEST_BUILD`) and the client
  `art98.toml` (`ART98_CLIENT`, default `/opt/art98-client`).
- **Writes.** Its data folder (`plugin_data_dir("art98")`, or `ART98_DATA`): recordings,
  animations, states, screenshots, `history.jsonl`, `.activity.json` and the render copies; its
  own settings in Hermes's `config.yaml`; a temporary folder per screenshot.
- **Guest control.** The tools move the mouse, type and start programs inside the Windows 98 VM,
  so the agent can already run anything in the guest. `art98_win32` (any Win32 call in the guest)
  is off unless `allow_win32` is on; that gate is about direct API calls, not a guest sandbox.
- **Environment variables it reads:** `ART98_GUEST_BUILD`, `ART98_CLIENT`, `ART98_VNC`, `ART98_DATA`
  (all optional, with the defaults above).

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
2. Web dashboard: `hermes dashboard`, then `<dashboard>/art98` (by default
   `http://127.0.0.1:9119/art98`). Open the cog, set `backend` to `drive`, and restart
   the dashboard after the plugin's first install.
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
