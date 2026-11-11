"""Paint: the stock Windows 98 paint program, as a prop."""

from __future__ import annotations

import time
from typing import ClassVar

from ...base.props import Spot
from ...bridge import BridgeTimeout
from ...win32 import window_rect
from ..pointer import Drag
from ..window.win import Win


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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._canvas_size: tuple[int, int] | None = None

    def ready(self) -> None:
        deadline = time.monotonic() + 10
        while True:
            children = self.children()
            buttons = [c for c in children if c.class_name == "ToolChild"]
            if len(buttons) == len(self.TOOLS):
                break
            if time.monotonic() > deadline:
                raise BridgeTimeout(f"Paint showed {len(buttons)} tool buttons")
            time.sleep(0.05)
        ids = {self.guest.user32.GetDlgCtrlID(c.hwnd).value: c.hwnd for c in buttons}
        self._tools = {name: ids[number] for name, number in self.TOOLS.items()}
        self._colors = next(
            c.hwnd
            for c in children
            if c.class_name == "AfxWnd42" and c.title == "Colors"
        )
        self._view = next(c.hwnd for c in children if c.class_name.startswith("Afx:"))
        super().ready()

    def _rect_of(self, hwnd: int) -> tuple[int, int, int, int]:
        return window_rect(self.guest, hwnd)

    def tool(self, name: str) -> Spot:
        """The toolbox button with this name. See `Paint.TOOLS`."""
        if name not in self.TOOLS:
            raise ValueError(f"no tool {name!r}; Paint has {', '.join(self.TOOLS)}")

        def locate():
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
            raise ValueError(f"no colour {name!r} in Paint's palette")

        def locate():
            left, top, _, _ = self._rect_of(self._colors)
            return (
                left + self.SWATCH_LEFT + column * self.SWATCH,
                top + self.SWATCH_TOP + row * self.SWATCH,
            )

        return Spot(locate)

    def canvas(self, x: float, y: float) -> Spot:
        """A pixel of the canvas, as a place on screen."""

        def locate():
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

    def _measure_canvas(self) -> tuple[int, int]:
        left, top, right, bottom = self._rect_of(self._view)
        x0, y0 = left + self.CANVAS_MARGIN, top + self.CANVAS_MARGIN
        screen = self.guest.user32.GetDC(0).value

        def in_canvas(x: int, y: int) -> bool:
            colour = self.guest.gdi32.GetPixel(screen, x, y).value & 0xFFFFFF
            return colour != self.WORKSPACE_GREY

        def edge(low: int, high: int, inside) -> int:
            while high - low > 1:
                middle = (low + high) // 2
                low, high = (middle, high) if inside(middle) else (low, middle)
            return low

        # Ten pixels in from the corner, clear of the handles on the edges.
        width = edge(x0 + 4, right, lambda x: in_canvas(x, y0 + 10)) - x0 + 1
        height = edge(y0 + 4, bottom, lambda y: in_canvas(x0 + 10, y)) - y0 + 1
        self.guest.user32.ReleaseDC(0, screen)
        return width, height

    @property
    def canvas_handle(self) -> Spot:
        """The grab handle on the canvas's bottom right corner."""
        return Spot(
            lambda: self.canvas(self.canvas_size[0] + 1, self.canvas_size[1]).point()
        )

    def resize_canvas(self, width: int, height: int, **kwargs) -> Drag:
        """Drag the corner handle to give the canvas a new size."""
        return _CanvasDrag(self, (width, height), **kwargs)


class _CanvasDrag(Drag):
    def __init__(self, paint: Paint, size: tuple[int, int], **kwargs):
        super().__init__(
            paint.canvas_handle, paint.canvas(size[0] + 1, size[1]), **kwargs
        )
        self.paint, self.size = paint, size

    def finish(self, scene) -> None:
        super().finish(scene)
        self.paint._canvas_size = self.size
