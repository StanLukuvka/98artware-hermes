"""Minesweeper: the stock Windows 98 game, as a prop that can win itself."""

from __future__ import annotations

import math
import time
from typing import ClassVar

from ...base.props import Spot
from ...base.vectors import Vec
from ..keyboard import Keys, TypeKeys
from ..pointer import Click, RightClick
from ..window.win import Win

SW_HIDE, SW_SHOWNA = 0, 8
BLACK = 0


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

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.mines: set[tuple[int, int]] = set()

    @property
    def board_size(self) -> tuple[int, int]:
        """Columns and rows, from the size of the client area."""
        width, height = self.client_size()
        return (width - self.MARGIN_X) // self.CELL, (
            height - self.MARGIN_Y
        ) // self.CELL

    def cell(self, column: int, row: int) -> Spot:
        """The centre of a board cell, as a place on screen."""
        return self.client(
            self.BOARD_LEFT + column * self.CELL + self.CELL // 2,
            self.BOARD_TOP + row * self.CELL + self.CELL // 2,
        )

    def find_mines(self) -> set[tuple[int, int]]:
        """Read the board through Minesweeper's own XYZZY cheat.

        After XYZZY and Shift it paints the top left pixel of the screen black
        over a mine and white elsewhere. This happens between two video frames
        and needs no frames of its own. Progman would clip that pixel, so it
        is hidden while the pointer visits every cell. No window may cover
        the top left corner of the screen. The pointer goes back afterwards.
        """
        guest, vm = self.guest, self.scene.vm
        self.activate()
        for letter in "xyzzy":
            vm.send_keys(letter)
            time.sleep(0.15)
        vm.send_keys("shift")
        time.sleep(0.2)

        columns, rows = self.board_size
        desktop = guest.find_window(class_name="Progman")
        guest.user32.ShowWindow(desktop.hwnd, SW_HIDE)
        guest.sync()
        screen = guest.user32.GetDC(0).value
        pointer = self.scene.pointer
        home = pointer.get_center()
        mines = set()
        for row in range(rows):
            for column in range(columns):
                guest.user32.SetCursorPos(*self.cell(column, row).point().rounded())
                guest.sync()
                if guest.gdi32.GetPixel(screen, 0, 0).value & 0xFFFFFF == BLACK:
                    mines.add((column, row))
        guest.user32.ReleaseDC(0, screen)
        guest.user32.ShowWindow(desktop.hwnd, SW_SHOWNA)
        guest.sync()
        guest.user32.SetCursorPos(*Vec(*home).rounded())
        time.sleep(0.3)

        expected = self.MINES.get((columns, rows))
        if expected is not None and len(mines) != expected:
            raise RuntimeError(f"expected {expected} mines, read {len(mines)}")
        self.mines = mines
        return mines

    def neighbours(self, cell: tuple[int, int]) -> list[tuple[int, int]]:
        columns, rows = self.board_size
        return [
            (c, r)
            for c in range(cell[0] - 1, cell[0] + 2)
            for r in range(cell[1] - 1, cell[1] + 2)
            if (c, r) != cell and 0 <= c < columns and 0 <= r < rows
        ]

    def opened_by(self, cell: tuple[int, int]) -> set[tuple[int, int]]:
        """The cells a click opens: all of a flood fill from an empty cell."""
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

        Call `find_mines` first. This plays its own animations.
        """
        if not self.mines:
            self.find_mines()
        scene = self.scene
        columns, rows = self.board_size
        cells = {(c, r) for c in range(columns) for r in range(rows)}
        centres = {cell: self.cell(*cell).point() for cell in cells}

        def nearest(group):
            here = scene.pointer.get_center()
            return min(group, key=lambda c: math.hypot(*(centres[c] - here)))

        remaining = cells - self.mines
        first = max(sorted(remaining), key=lambda cell: len(self.opened_by(cell)))
        scene.play(Click(self.cell(*first)))
        remaining -= self.opened_by(first)
        scene.pause(0.5)

        flags = set(self.mines)
        while flags:
            mine = nearest(flags)
            flags.discard(mine)
            scene.play(RightClick(self.cell(*mine), run_time=0.43))
        while remaining:
            cell = nearest(remaining)
            scene.play(Click(self.cell(*cell), run_time=0.43))
            remaining -= self.opened_by(cell)

    def sign_scoreboard(self, name: str = "art98") -> None:
        """Answer the best time prompt that follows a win, and show the table.

        Winning a first game always beats the best time, so the prompt comes up.
        """
        scene = self.scene
        scene.guest.find_window(title="Congratulations")
        scene.pause(1)
        scene.play(TypeKeys(name + "\n", run_time=0.3 * (len(name) + 1)))
        scene.guest.find_window(title="Best Times")
        scene.pause(2)
        scene.play(Keys("enter"))
        scene.pause(1)
