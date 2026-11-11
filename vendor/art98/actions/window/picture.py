"""The art98 picture window."""

from __future__ import annotations

from pathlib import Path

from ... import guestfiles
from ...bridge import Window
from .win import Win

DEFAULT_AT = (100, 100)
DEFAULT_SIZE = (320, 240)


class Picture(Win):
    """A plain window that shows one BMP at 1:1, centred, on grey.

    Windows 98 has no small image viewer, so this is art98's own program,
    VIEW.EXE, copied to the guest when it is needed. `image` is a BMP on the
    host, or None for an empty window. The window opens at `at` with the outer
    `size`, so it never has to move off a default position first.
    """

    window_class = "Art98View"

    def __init__(
        self,
        title: str = "Picture",
        *,
        image: Path | str | None = None,
        at: tuple[int, int] | None = None,
        size: tuple[int, int] | None = None,
    ):
        if guestfiles.title_problem(title):
            raise ValueError(
                f"{title!r} can't be a picture window's title: it {guestfiles.title_problem(title)}"
            )
        super().__init__(title, at=at or DEFAULT_AT, size=size or DEFAULT_SIZE)
        self.image = Path(image) if image is not None else None
        self._image_data = None
        if self.image is not None:
            guestfiles.check_name(self.image.name)
            self._image_data = self.image.read_bytes()
            self.image_size = guestfiles.check_bmp(self._image_data, str(self.image))

    @classmethod
    def from_state(cls, window) -> Picture:
        return cls(
            window.title,
            image=window.open.path if window.open else None,
            at=window.at,
            size=window.size,
        )

    def attach(self, scene) -> None:
        guest = scene.guest
        args = [
            guestfiles.ensure(guest, guestfiles.viewer(), "VIEW.EXE"),
            *map(str, self._at),
            *map(str, self._size),
            f'"{self.title_text}"',
        ]
        if self._image_data is not None:
            args.append(
                f'"{guestfiles.ensure(guest, self._image_data, self.image.name)}"'
            )
        self.command = " ".join(args)
        super().attach(scene)

    def _look(self, pid: int | None) -> Window | None:
        """Look for the window, failing at once if VIEW.EXE gave up or crashed."""
        for window in self.guest.windows():
            if (
                window.pid == pid
                and window.visible
                and window.class_name == self.class_name
            ):
                return window
            if (
                window.visible
                and window.class_name == "#32770"
                and window.title == "View"
            ):
                raise RuntimeError(
                    f"VIEW.EXE crashed opening {self.image or 'its window'}, and Windows 98's"
                    " crash dialog is on the desktop"
                )
        if pid not in {p for p, _ in self.guest.processes()}:
            what = f"the image {self.image}" if self.image else "its window"
            raise RuntimeError(
                f"VIEW.EXE closed without a window: Windows 98 couldn't open {what}"
            )
        return None

    def _not_found(self, pid: int | None) -> str:
        return f"no window for Picture {self.title_text!r}"
