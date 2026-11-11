---
name: art98-install
description: Set up art98 and its Hermes plugin on this machine.
version: 0.1.0
author: StanLukuvka
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [windows-98, setup, install, docker]
    category: devops
    related_skills: [art98]
---

# art98 Install Skill

Walks the user through installing art98 (a Windows 98 SE VM that renders desktop scenes to
video) and the art98 Hermes plugin that drives it. It checks each step and says what is left.
It does not use the plugin's tools; once setup works, the plugin's own `art98:art98` skill takes over.

This is a standalone skill on purpose: plugin skills only load once their plugin is installed
and enabled, so the install guide can't live inside the plugin.

## When to Use

- The user asks to install, set up or repair art98 or the art98 plugin.
- `art98_*` tools are missing, or `art98_status` answers `not_connected` or no VM.

## Prerequisites

On the host (the machine with Docker), the user needs:

- Linux, Docker (or Podman with `podman-docker`), Python 3.11+, `uv` and `ffmpeg`.
- Their own Windows 98 SE disc image (`.iso`) and its product key.
- About 3 GB of disk and 25 to 40 minutes for the one-time Windows install.

The user types the product key into art98 themselves. Never ask for it, store it, or put it in a
command. Never read, list or copy anything in art98's `work/` folder: it holds the disks and the key.

## How to Run

First find out where Hermes runs. Use `terminal` to run `docker version` and `hermes plugins list`.

- **Hermes on the host:** you can run the host steps yourself with `terminal`, except
  `art98 up`, which the user runs because it asks for the key.
- **Hermes in a container** (no `docker` inside): give the user the host commands to run, and
  do only the Hermes steps yourself.

## Quick Reference

| Step | Where | Command |
| --- | --- | --- |
| Get art98 | host | `git clone https://github.com/StanLukuvka/98artware 98artware` |
| Check tools | host | `uv run art98 doctor` (from the checkout) |
| Install Windows, once | host, user | `uv run art98 up --iso /path/to/win98se.iso` |
| Start the VM | host | `uv run art98 vm start` (add `--vnc` for the dashboard's Live view) |
| VM state | host | `uv run art98 vm status` |
| Install the plugin | Hermes | `hermes plugins install StanLukuvka/98artware-hermes --enable` |
| Check it | Hermes | `art98_status`, then `art98_screenshot` |

## Procedure

1. **art98 on the host.** Clone it, then run `uv run art98 doctor` and read its list. It is
   enough when it says the VM is usable once `art98 up` has run.
2. **Windows, once.** The user runs `uv run art98 up --iso <their iso>`. It asks for the key
   without echoing it, installs with no further input and stops. If it stops part way, the same
   command resumes. Wait for the user to say it finished.
3. **Settings for a Hermes container.** If Hermes runs in a container, the VM must listen on TCP
   loopback instead of a unix socket. Have the user make a host settings folder (for example
   `~/art98-host/`) with an `art98.toml`:

   ```toml
   [vm]
   work       = "/path/to/98artware/work"
   transport  = "tcp"
   token_file = "~/art98-client/vm.token"  # outside work/
   ```

   Run every `art98 vm` command from that folder, with `uv run --project /path/to/98artware art98 ...`.
   Skip this step when Hermes runs on the host.
4. **Start the VM.** `art98 vm start` creates the token file if it is missing, and publishes the
   control port on `127.0.0.1:7598` only. It stays up, paused, until `art98 vm stop`.
5. **Give Hermes the client side** (container only). The container needs, read-only:
   - the art98 library, `98artware/core`, with `ART98_CORE` pointing at it;
   - `98artware/guest/build`, at `/opt/art98/guest/build`;
   - a client folder holding `vm.token` and an `art98.toml` with `[vm] transport = "tcp"`,
     `token_file = "vm.token"` and `[ports] connect = "127.0.0.1"`, with `ART98_CLIENT` pointing at it;
   - the host's loopback ports 7598 (and 5900 for VNC) forwarded into the container's loopback.

   Never mount `work/`. Restart the container after changing its mounts.
6. **Install the plugin.** `hermes plugins install StanLukuvka/98artware-hermes --enable`. If
   it is a local checkout instead, link it: `~/.hermes/plugins/art98` pointing at the folder,
   then `hermes plugins enable art98`.
7. **Point it at the VM.** In the dashboard's art98 tab (http://127.0.0.1:9119/art98), open the
   cog and set `backend` to `drive`. Set `host_folder` to the plugin's data folder as the host sees
   it (`<HERMES_HOME>/plugin-data/art98` on the host side), so the VS Code links work. Restart the dashboard after the plugin's first install.

## Pitfalls

- `busy` from a check means a render holds the VM. Wait and try again; it is not broken.
- `art98 up` takes 25 to 40 minutes. Don't treat a quiet terminal as a hang.
- On rootless Podman, `docker` may point at the wrong socket. If the VM container is missing
  from `docker ps`, check which engine `art98 vm start` used.
- Running a `vm` command from the wrong folder uses the wrong settings: the default unix socket
  instead of TCP, and a different token.
- Don't stop, restart or rebuild the VM while the user or another session is rendering.

## Verification

- On the host, `art98 vm status` says the VM is running or paused.
- In Hermes, `art98_status` shows `connected: true`, backend `drive`.
- `art98_screenshot` returns the Windows 98 desktop.
- The dashboard's Status card shows the VM as Paused and the backend as drive, connected.
- Optional: render a short scene with `art98_render` and check it appears in History.
