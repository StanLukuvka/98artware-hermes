"""The desktop colour."""

from __future__ import annotations

from ..base.animation import Instant
from ..desktop import resolve_colour, set_colour


class Backdrop(Instant):
    """The desktop colour, set in one frame.

    `colour` is an (r, g, b) tuple or a name in desktop.NAMED_COLOURS. Raises
    ValueError on anything else.
    """

    claims = ()

    def __init__(self, colour, **kwargs):
        super().__init__(None, **kwargs)
        self.rgb = resolve_colour(colour)

    def act(self) -> None:
        set_colour(self.scene.guest, self.rgb)
