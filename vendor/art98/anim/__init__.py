"""A Manim-style layer over the bridge: `from art98.anim import *`.

Names follow Manim where the meaning matches. Units do not: everything is in
screen pixels with y running down, because that is what Win32 speaks.
"""

from ..actions.backdrop import Backdrop
from ..actions.keyboard import Keys, TypeKeys, TypeWithCursor, UntypeWithCursor
from ..actions.pointer import (
    Click,
    DoubleClick,
    Drag,
    Glide,
    Hover,
    Pointer,
    RightClick,
    Stroke,
)
from ..actions.programs import Dialog, Minesweeper, Notepad, Paint, Screensaver
from ..actions.window import (
    Close,
    Maximize,
    Menu,
    Minimize,
    Picture,
    RestoreWindow,
    Teleport,
    Win,
)
from ..base.animation import (
    Animate,
    Animation,
    AnimationGroup,
    Instant,
    LaggedStart,
    MoveAlongPath,
    Succession,
    Wait,
)
from ..base.props import Box, Prop, Spot
from ..base.rate_functions import (
    linear,
    rush_from,
    rush_into,
    smooth,
    smoothstep,
    there_and_back,
)
from ..base.vectors import DL, DOWN, DR, LEFT, ORIGIN, RIGHT, UL, UP, UR, Vec, lerp
from .renderer import render, write_gif
from .scene import Scene

__all__ = [
    "DL",
    "DOWN",
    "DR",
    "LEFT",
    "ORIGIN",
    "RIGHT",
    "UL",
    "UP",
    "UR",
    "Animate",
    "Animation",
    "AnimationGroup",
    "Backdrop",
    "Box",
    "Click",
    "Close",
    "Dialog",
    "DoubleClick",
    "Drag",
    "Glide",
    "Hover",
    "Instant",
    "Keys",
    "LaggedStart",
    "Maximize",
    "Menu",
    "Minesweeper",
    "Minimize",
    "MoveAlongPath",
    "Notepad",
    "Paint",
    "Picture",
    "Pointer",
    "Prop",
    "RestoreWindow",
    "RightClick",
    "Scene",
    "Screensaver",
    "Spot",
    "Stroke",
    "Succession",
    "Teleport",
    "TypeKeys",
    "TypeWithCursor",
    "UntypeWithCursor",
    "Vec",
    "Wait",
    "Win",
    "lerp",
    "linear",
    "render",
    "rush_from",
    "rush_into",
    "smooth",
    "smoothstep",
    "there_and_back",
    "write_gif",
]
