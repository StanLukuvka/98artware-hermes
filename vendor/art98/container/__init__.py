"""The VM service: one long-lived container that runs the VM.

`service.py` is `start`, `stop`, `status` and `attach()`, which every render uses. `compose.py` and
`host.py` run on the host: the compose project, and the engine, `open_channel` and `RemoteVM`.
`serve.py` runs inside the container, and `entry.py` is the image's entry point. `freshness.py`
warns when the image or the guest programs are older than the code.
"""
