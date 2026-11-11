"""The one seam between the Hermes tools and art98.

Every tool calls a method on a Desk. StubDesk answers each call with
NotConnected, so the tools, schemas, skill and desktop pane can be built and
tested before the art98 service exists. A real Desk (talking to an art98
service on this PC) replaces it in get_desk() and nothing else changes.

No imports from the rest of the plugin, so dashboard/plugin_api.py can load it by path.
"""

from __future__ import annotations

from typing import Any


class NotConnected(RuntimeError):
    """The art98 backend isn't wired up yet (or isn't running)."""


class Desk:
    """What the tools need from a Windows 98 desktop. Coordinates are guest pixels."""

    name = "abstract"

    def status(self) -> dict[str, Any]: raise NotImplementedError
    def screenshot(self) -> bytes: raise NotImplementedError  # PNG
    def click(self, x: int, y: int, button: str = "left", double: bool = False) -> dict: raise NotImplementedError
    def type_text(self, text: str) -> dict: raise NotImplementedError
    def key(self, keys: str) -> dict: raise NotImplementedError
    def windows(self) -> list[dict]: raise NotImplementedError
    def open(self, program: str) -> dict: raise NotImplementedError
    def win32(self, dll: str, function: str, args: list) -> dict: raise NotImplementedError
    def revert(self) -> dict: raise NotImplementedError
    def set_state(self, state_path: str) -> dict: raise NotImplementedError
    def record(self, action: str, out_path: str | None = None) -> dict: raise NotImplementedError
    def render(self, animation_path: str, out_path: str) -> dict: raise NotImplementedError


class StubDesk(Desk):
    name = "stub"

    def status(self) -> dict[str, Any]:
        return {"backend": self.name, "connected": False,
                "note": "stub backend: no Windows 98 VM is attached yet"}

    def _no(self, what: str):
        raise NotConnected(f"{what}: the art98 backend is a stub, no VM is attached yet")

    def screenshot(self): self._no("screenshot")
    def click(self, x, y, button="left", double=False): self._no("click")
    def type_text(self, text): self._no("type")
    def key(self, keys): self._no("key")
    def windows(self): self._no("windows")
    def open(self, program): self._no("open")
    def win32(self, dll, function, args): self._no("win32")
    def revert(self): self._no("revert")
    def set_state(self, state_path): self._no("set_state")
    def record(self, action, out_path=None): self._no("record")
    def render(self, animation_path, out_path): self._no("render")


_desk: Desk | None = None


def get_desk() -> Desk:
    global _desk
    if _desk is None:
        _desk = StubDesk()
    return _desk
