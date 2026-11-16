"""Paint: the stock Windows 98 paint program, as a prop."""

from __future__ import annotations

import time
from typing import ClassVar

from ...base.props import Spot
from ...errors import GuestError, NotFound, UsageError
from ...guest.bridge import BridgeTimeout
from ...guest.win32 import screen_dc, window_rect
from ..pointer import Drag
from ..window.win import Win

READY_TIMEOUT = 10.0  # seconds Paint has to show its 16 tool buttons
READY_POLL = 0.05  # seconds between looks at them
PROBE_INSET = 4  # pixels in from the canvas corner where the edge search starts
PROBE_ACROSS = 10  # pixels along the probe line, clear of the handles on the edges


class Paint(Win):
    """Paint, with every button found from the live window.

    `tool` and `color` give targets that are located when an animation begins,
    from the ToolChild buttons and the Colors box, so they stay right after
    the window moves or resizes. Colours are Paint's default palette.
    """

    command = r'"C:\Program Files\Accessories\MSPAINT.EXE"'
    window_class = "MSPaintApp"

    # Control ids of the ToolChild buttons.
    TOOLS: ClassVar[dict[str, int]] = {
        "free_select": 621,
        "select": 620,
        "eraser": 637,
        "fill": 623,
        "pick_color": 639,
        "magnifier": 638,
        "pencil": 636,
        "brush": 640,
        "airbrush": 627,
        "text": 622,
        "line": 624,
        "curve": 628,
        "rectangle": 641,
        "polygon": 632,
        "ellipse": 643,
        "rounded_rectangle": 634,
    }
    PALETTE = (
        (
            "black",
            "gray",
            "maroon",
            "olive",
            "green",
            "teal",
            "navy",
            "purple",
            "khaki",
            "dark_teal",
            "azure",
            "steel",
            "indigo",
            "brown",
        ),
        (
            "white",
            "silver",
            "red",
            "yellow",
            "lime",
            "cyan",
            "blue",
            "magenta",
            "cream",
            "mint",
            "sky",
            "periwinkle",
            "rose",
            "orange",
        ),
    )
    SWATCH = 16
    SWATCH_LEFT, SWATCH_TOP = (
        40,
        16,
    )  # centre of the first swatch, inside the Colors box
    CANVAS_MARGIN = 3  # between the canvas view's corner and pixel (0, 0)
    WORKSPACE_GREY = 0x808080

    def __init__(self, *, at=None, size=None):
        super().__init__(at=at, size=size)
        self._canvas_size: tuple[int, int] | None = None
        # The parts of the window, found in ready.
        self._tools: dict[str, int] | None = None
        self._colors: int | None = None
        self._view: int | None = None

    def ready(self) -> None:
        deadline = time.monotonic() + READY_TIMEOUT
        while True:
            children = self.children()
            buttons = [c for c in children if c.class_name == "ToolChild"]
            if len(buttons) == len(self.TOOLS):
                break
            if time.monotonic() > deadline:
                raise BridgeTimeout(f"Paint showed {len(buttons)} tool buttons")
            time.sleep(READY_POLL)
        ids = {self.guest.user32.GetDlgCtrlID(c.hwnd).value: c.hwnd for c in buttons}
        try:
            self._tools = {name: ids[number] for name, number in self.TOOLS.items()}
        except KeyError as missing:
            raise GuestError(
                f"Paint has no tool button with id {missing.args[0]}"
            ) from None
        colors = [
            c for c in children if c.class_name == "AfxWnd42" and c.title == "Colors"
        ]
        if not colors:
            raise GuestError("Paint has no Colors box")
        views = [c for c in children if c.class_name.startswith("Afx:")]
        if not views:
            raise GuestError("Paint has no canvas view")
        self._colors, self._view = colors[0].hwnd, views[0].hwnd
        super().ready()

    def forget(self) -> None:
        super().forget()
        self._canvas_size = None
        self._tools = self._colors = self._view = None

    def _check_ready(self) -> None:
        if self._view is None:
            raise UsageError("Paint is not on stage yet: add it to the scene first")

    def _rect_of(self, hwnd: int) -> tuple[int, int, int, int]:
        rect = window_rect(self.guest, hwnd)
        if rect is None:
            raise GuestError("Paint has closed")
        return rect

    def tool(self, name: str) -> Spot:
        """The toolbox button with this name. See `Paint.TOOLS`."""
        if name not in self.TOOLS:
            raise NotFound(f"no tool {name!r}; Paint has {', '.join(self.TOOLS)}")

        def locate():
            self._check_ready()
            left, top, right, bottom = self._rect_of(self._tools[name])
            return (left + right) // 2, (top + bottom) // 2

        return Spot(locate)

    def color(self, name: str) -> Spot:
        """A swatch of the palette. Left click sets ink, right click sets paper."""
        for row, names in enumerate(self.PALETTE):
            if name in names:
                column = names.index(name)
                break
        else:
            raise NotFound(f"no colour {name!r} in Paint's palette")

        def locate():
            self._check_ready()
            left, top, _, _ = self._rect_of(self._colors)
            return (
                left + self.SWATCH_LEFT + column * self.SWATCH,
                top + self.SWATCH_TOP + row * self.SWATCH,
            )

        return Spot(locate)

    def canvas(self, x: float, y: float) -> Spot:
        """A pixel of the canvas, as a place on screen."""

        def locate():
            self._check_ready()
            left, top, _, _ = self._rect_of(self._view)
            return left + self.CANVAS_MARGIN + x, top + self.CANVAS_MARGIN + y

        return Spot(locate)

    @property
    def canvas_size(self) -> tuple[int, int]:
        """The canvas in pixels, measured the first time it is needed.

        Paint doesn't report it, so the edge is found with GetPixel between
        the white canvas and the grey workspace. That needs a blank canvas,
        and after `resize_canvas` the new size is simply remembered.
        """
        if self._canvas_size is None:
            self._canvas_size = self._measure_canvas()
        return self._canvas_size

    @canvas_size.setter
    def canvas_size(self, size: tuple[int, int]) -> None:
        self._canvas_size = size

    def _measure_canvas(self) -> tuple[int, int]:
        self._check_ready()
        left, top, right, bottom = self._rect_of(self._view)
        x0, y0 = left + self.CANVAS_MARGIN, top + self.CANVAS_MARGIN

        def in_canvas(x: int, y: int) -> bool:
            colour = self.guest.gdi32.GetPixel(screen, x, y).value & 0xFFFFFF
            return colour != self.WORKSPACE_GREY

        def edge(low: int, high: int, inside) -> int:
            while high - low > 1:
                middle = (low + high) // 2
                low, high = (middle, high) if inside(middle) else (low, middle)
            return low

        with screen_dc(self.guest) as screen:
            # The search needs canvas at its start and grey at its far end. A
            # canvas bigger than the window has canvas at both, so every probe
            # would read as inside and the size would be the window's.
            fits_across = in_canvas(
                x0 + PROBE_INSET, y0 + PROBE_ACROSS
            ) and not in_canvas(right - 1, y0 + PROBE_ACROSS)
            fits_down = in_canvas(
                x0 + PROBE_ACROSS, y0 + PROBE_INSET
            ) and not in_canvas(x0 + PROBE_ACROSS, bottom - 1)
            if not (fits_across and fits_down):
                raise GuestError(
                    "Paint's canvas does not fit the window: make the window bigger"
                )
            width = (
                edge(x0 + PROBE_INSET, right, lambda x: in_canvas(x, y0 + PROBE_ACROSS))
                - x0
                + 1
            )
            height = (
                edge(
                    y0 + PROBE_INSET,
                    bottom,
                    lambda y: in_canvas(x0 + PROBE_ACROSS, y),
                )
                - y0
                + 1
            )
        return width, height

    @property
    def canvas_handle(self) -> Spot:
        """The grab handle on the canvas's bottom right corner."""
        return Spot(lambda: _handle_at(self, *self.canvas_size).point())

    def resize_canvas(self, width: int, height: int, **kwargs) -> Drag:
        """Drag the corner handle to give the canvas a new size."""
        return _CanvasDrag(self, (width, height), **kwargs)


def _handle_at(paint: Paint, width: int, height: int) -> Spot:
    """The grab handle for a canvas this size: one pixel past the right edge, so the press lands on the handle."""
    return paint.canvas(width + 1, height)


class _CanvasDrag(Drag):
    def __init__(self, paint: Paint, size: tuple[int, int], **kwargs):
        super().__init__(paint.canvas_handle, _handle_at(paint, *size), **kwargs)
        self.paint, self.size = paint, size

    def finish(self, scene) -> None:
        super().finish(scene)
        self.paint.canvas_size = self.size
