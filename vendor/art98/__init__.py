"""art98: script animations with a real Windows 98 desktop inside a VM."""

from . import legacy  # noqa: F401  (the Hermes plugin's old names; see legacy.py)
from .container.service import attach
from .errors import Art98Error, NotFound, UsageError
from .guest.bridge import (
    Bridge,
    BridgeError,
    BridgeTimeout,
    CallResult,
    GuestCrashed,
    Window,
)
from .guest.protocol import Out
from .guest.qemu import VM, VMConfig
from .output.recorder import Recorder, RecorderError
from .session import Session

__all__ = [
    "VM",
    "Art98Error",
    "Bridge",
    "BridgeError",
    "BridgeTimeout",
    "CallResult",
    "GuestCrashed",
    "NotFound",
    "Out",
    "Recorder",
    "RecorderError",
    "Session",
    "UsageError",
    "VMConfig",
    "Window",
    "attach",
]
