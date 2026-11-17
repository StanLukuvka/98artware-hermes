"""The Python side of talking to the running Win98 VM.

`qemu.py` starts and controls QEMU (QMP, snapshots, screenshots). `protocol.py` and `bridge.py` are
the host side of the COM1 agent wire format, as `guest.user32.X(...)`. `win32.py` holds the Win32
constants and small named guest reads. `guestfiles.py` and `disc.py` get files and CD images to the
guest.

The guest's own C programs (AGENT.EXE and so on) are in the repo's top-level `guest/` folder, not here.
"""
