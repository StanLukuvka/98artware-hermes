"""Minesweeper: the stock Windows 98 game, as a prop that can win itself."""

from __future__ import annotations

import time
from typing import ClassVar

from ...base.props import Spot
from ...base.units import distance
from ...errors import GuestError, UsageError
from ...guest.bridge import BridgeTimeout
from ...guest.win32 import SW_HIDE, SW_SHOWNA, screen_dc, show_window
from ..keyboard import Keys, TypeKeys
from ..pointer import Click, RightClick
from ..window.win import Win

BLACK = 0  # the pixel colour that marks a mine during the XYZZY read
KEY_GAP = 0.15  # seconds between the letters of XYZZY
CHEAT_SETTLE = 0.2  # seconds after Shift, for the cheat to paint the pixel
POINTER_SETTLE = 0.3  # seconds after the pointer goes home, for the guest to settle
FIRST_CLICK_PAUSE = 0.5  # seconds after the first click, before the flags
CLICK_TIME = 0.43  # seconds per click while the board is cleared
PROMPT_PAUSE = 1  # seconds to let the best-time prompt and the table settle
TABLE_PAUSE = 2  # seconds the best-times table stays up before it is dismissed
TYPE_SECONDS_PER_KEY = 0.3  # seconds per key typed into the best-time prompt


class Minesweeper(Win):
    """Minesweeper, with the board found from the window's client area."""

    command = r"C:\WINDOWS\WINMINE.EXE"
    window_class = "Minesweeper"

    BOARD_LEFT, BOARD_TOP = 12, 55  # the first cell, in client coordinates
    MARGIN_X, MARGIN_Y = 20, 63  # client area outside the cells
    CELL = 16
    MINES: ClassVar[dict[tuple[int, int], int]] = {
        (8, 8): 10,
        (16, 16): 40,
        (30, 16): 99,
    }

    def __init__(self, *, at=None, size=None):
        super().__init__(at=at, size=size)
        # None until find_mines reads them. A set is kept as read: a new game needs find_mines again.
        self.mines: frozenset[tuple[int, int]] | None = None
        self._board: tuple[int, int] | None = None  # columns and rows, see `_grid`

    def forget(self) -> None:
        super().forget()
        self.mines = None
        self._board = None

    @property
    def board_size(self) -> tuple[int, int]:
        """Columns and rows, from the size of the client area."""
        width, height = self.client_size()
        return (width - self.MARGIN_X) // self.CELL, (
            height - self.MARGIN_Y
        ) // self.CELL

    def _grid(self) -> tuple[int, int]:
        """Columns and rows, read from the window the first time and then kept, so
        the neighbour lookups of a flood fill make no guest calls."""
        if self._board is None:
            self._board = self.board_size
        return self._board

    def cell(self, column: int, row: int) -> Spot:
        """The centre of a board cell, as a place on screen."""
        return self.client(
            self.BOARD_LEFT + column * self.CELL + self.CELL // 2,
            self.BOARD_TOP + row * self.CELL + self.CELL // 2,
        )

    def find_mines(self) -> frozenset[tuple[int, int]]:
        """Read the board through Minesweeper's own XYZZY cheat.

        After XYZZY and Shift it paints the top left pixel of the screen black
        over a mine and white elsewhere. This happens between two video frames
        and needs no frames of its own. Progman would clip that pixel, so it
        is hidden while the pointer visits every cell. No window may cover
        the top left corner of the screen. The pointer goes back afterwards.

        Typing XYZZY again toggles the cheat off, so a second read in the same
        game finds no mines: read once per game. Windows 98 moves a mine away
        from the first click only when that click lands on a mine, so a set
        read before the first click is stale by at most one mine.
        """
        guest, vm = self.guest, self.scene.vm
        columns, rows = self.board_size
        self._board = (columns, rows)
        if columns < 1 or rows < 1:
            raise GuestError(
                f"Minesweeper board is {columns}x{rows}: make the window bigger"
            )
        self.activate()
        for letter in "xyzzy":
            vm.send_keys(letter)
            time.sleep(KEY_GAP)
        vm.send_keys("shift")
        time.sleep(CHEAT_SETTLE)

        desktop = guest.find_window(class_name="Progman")
        show_window(guest, desktop.hwnd, SW_HIDE)
        guest.sync()
        pointer = self.scene.pointer
        pointer.refresh()
        home = pointer.get_center()
        mines = set()
        try:
            with screen_dc(guest) as screen:
                for row in range(rows):
                    for column in range(columns):
                        pointer.place(self.cell(column, row))
                        guest.sync()
                        if guest.gdi32.GetPixel(screen, 0, 0).value & 0xFFFFFF == BLACK:
                            mines.add((column, row))
        finally:
            # Whatever went wrong, the desktop and the pointer come back.
            show_window(guest, desktop.hwnd, SW_SHOWNA)
            guest.sync()
            pointer.place(home)
        time.sleep(POINTER_SETTLE)

        expected = self.MINES.get((columns, rows))
        if expected is not None and len(mines) != expected:
            raise GuestError(f"expected {expected} mines, read {len(mines)}")
        self.mines = frozenset(mines)
        return self.mines

    def neighbours(self, cell: tuple[int, int]) -> list[tuple[int, int]]:
        columns, rows = self._grid()
        return [
            (c, r)
            for c in range(cell[0] - 1, cell[0] + 2)
            for r in range(cell[1] - 1, cell[1] + 2)
            if (c, r) != cell and 0 <= c < columns and 0 <= r < rows
        ]

    def opened_by(self, cell: tuple[int, int]) -> set[tuple[int, int]]:
        """The cells a click opens: all of a flood fill from an empty cell."""
        if self.mines is None:
            raise UsageError("the mines are not read yet: call find_mines first")
        opened, queue = {cell}, [cell]
        while queue:
            current = queue.pop()
            near = self.neighbours(current)
            if not self.mines.intersection(near):
                for other in near:
                    if other not in opened:
                        opened.add(other)
                        queue.append(other)
        return opened

    def clear(self) -> None:
        """Win the game: open the biggest area, flag every mine, finish the rest.

        Reads the mines first if `find_mines` has not been called yet. Call it
        again for each new game. This plays its own animations.
        """
        if self.mines is None:
            self.find_mines()
        scene = self.scene
        columns, rows = self._grid()
        cells = {(c, r) for c in range(columns) for r in range(rows)}
        centres = {cell: self.cell(*cell).point() for cell in cells}

        def nearest(group):
            here = scene.pointer.get_center()
            return min(group, key=lambda c: distance(centres[c], here))

        remaining = cells - self.mines
        first = max(sorted(remaining), key=lambda cell: len(self.opened_by(cell)))
        scene.play(Click(self.cell(*first)))
        remaining -= self.opened_by(first)
        scene.pause(FIRST_CLICK_PAUSE)

        flags = set(self.mines)
        while flags:
            mine = nearest(flags)
            flags.discard(mine)
            scene.play(RightClick(self.cell(*mine), run_time=CLICK_TIME))
        while remaining:
            cell = nearest(remaining)
            scene.play(Click(self.cell(*cell), run_time=CLICK_TIME))
            remaining -= self.opened_by(cell)

    def sign_scoreboard(self, name: str = "art98") -> None:
        """Answer the best time prompt that follows a win, and show the table.

        Winning a first game always beats the best time, so the prompt comes up.
        """
        scene = self.scene
        self._wait_for_window(
            "Congratulations",
            "no best-time prompt: was the game won, and is it a new best time?",
        )
        scene.pause(PROMPT_PAUSE)
        scene.play(
            TypeKeys(name + "\n", run_time=TYPE_SECONDS_PER_KEY * (len(name) + 1))
        )
        self._wait_for_window("Best Times", "no Best Times table after the name")
        scene.pause(TABLE_PAUSE)
        scene.play(Keys("enter"))
        scene.pause(PROMPT_PAUSE)

    def _wait_for_window(self, title: str, missing: str) -> None:
        """Wait for a window the game shows, or say what it means that it did not come."""
        try:
            self.scene.guest.find_window(title=title)
        except BridgeTimeout:
            raise GuestError(missing) from None
