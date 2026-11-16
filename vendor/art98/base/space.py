"""DesktopSpace: the desktop as read once from the guest, which every window action places against."""

from __future__ import annotations

import os
import sys
import warnings
from dataclasses import dataclass

from ..errors import UsageError
from .units import clamp

_PACKAGE = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + os.sep


def _scene_stacklevel() -> int:
    """The `stacklevel` that points a warning from `report` at the first frame outside art98."""
    frame, level = sys._getframe(1), 1  # level 1 is `report`, which calls warn
    while frame is not None and frame.f_code.co_filename.startswith(_PACKAGE):
        frame, level = frame.f_back, level + 1
    return level


class PlacementWarning(UserWarning):
    """A window was asked for a size or place Windows would change, or lose."""


class PlacementError(UsageError):
    """A placement problem in strict mode: `DesktopSpace.report` raises it where it would warn."""


@dataclass(frozen=True)
class Metrics:
    """System metrics in pixels. 0 means unknown, and checks that need it are skipped."""

    caption: int = 0  # SM_CYCAPTION
    size_frame: int = 0  # SM_CYSIZEFRAME (the y one; SM_CXSIZEFRAME is not read)
    min_track: tuple[int, int] = (0, 0)  # SM_CXMINTRACK, SM_CYMINTRACK
    max_track: tuple[int, int] = (0, 0)  # SM_CXMAXTRACK, SM_CYMAXTRACK


@dataclass(frozen=True)
class DesktopSpace:
    screen: tuple[int, int]
    work_area: tuple[int, int, int, int]  # (left, top, right, bottom)
    metrics: Metrics
    strict: bool = False  # a clamp or a lost window raises PlacementError

    @property
    def title_bar_offset(self) -> float:
        """From a window's top edge to the middle of its caption."""
        return self.metrics.size_frame + self.metrics.caption / 2

    def check_rect(
        self,
        name: str,
        rect: tuple[int, int, int, int],
        size: tuple[int, int],
        sizable: bool = True,
        warned: set | None = None,
    ) -> tuple[int, int]:
        """The size to send for a window asked to be `rect` and `size`.

        Only a sizable (WS_THICKFRAME) window is clamped to the track limits,
        which Windows would do itself. Position is never clamped; a rect with no
        pixel on the screen is reported. `warned` holds the reasons already
        reported for this window, so a 30-frame resize warns once.
        """
        w, h = size
        lo_w, lo_h = self.metrics.min_track
        hi_w, hi_h = self.metrics.max_track
        given = size
        if (
            sizable
            and self.metrics.min_track != (0, 0)
            and self.metrics.max_track != (0, 0)
        ):
            given = (clamp(w, lo_w, hi_w), clamp(h, lo_h, hi_h))
        if given != size:
            self.report(
                name,
                "size",
                f"size {size} outside track limits {self.metrics.min_track}..{self.metrics.max_track}, sending {given}",
                warned,
            )
        left, top, right, bottom = rect
        sw, sh = self.screen
        if sw and sh and (right <= 0 or bottom <= 0 or left >= sw or top >= sh):
            self.report(
                name, "offscreen", f"rect {rect} is off the {sw}x{sh} screen", warned
            )
        return given

    def report(
        self, name: str, reason: str, why: str, warned: set | None = None
    ) -> None:
        """Raise in strict mode, else warn once per `reason` in `warned`."""
        if self.strict:
            raise PlacementError(f"{name}: {why}")
        if warned is not None:
            if reason in warned:
                return
            warned.add(reason)
        warnings.warn(
            f"{name}: {why}", PlacementWarning, stacklevel=_scene_stacklevel()
        )


# STOCK is the answer before a scene has read the guest (`win32.calibrate`
# builds the real one): the space a Scene starts with and resets to, and the
# one an unattached Box uses. 0 limits mean "unknown", so such a Box is never
# clamped; caption and frame are placeholders until then.
TASKBAR_HEIGHT = 28
STOCK = DesktopSpace(
    (800, 600), (0, 0, 800, 600 - TASKBAR_HEIGHT), Metrics(caption=18, size_frame=3)
)
