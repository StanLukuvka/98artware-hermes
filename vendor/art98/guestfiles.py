"""Files the host copies to the guest, named by their content.

A revert throws away everything copied since the baseline, so anything a
scene needs on the guest is copied again when it is missing. Each file goes in
a folder named after its hash, so a file that is there is the right one, and
checking for it costs one call instead of a copy.
"""

from __future__ import annotations

import hashlib
import os
import struct
from pathlib import Path

GUEST_DIR = r"C:\ART98"
INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF
BAD_NAME_CHARS = set('\\/:*?"<>|')
BMP_HEADER_SIZES = {12, 40, 52, 56, 108, 124}
BMP_DEPTHS = {1, 4, 8, 16, 24, 32}
BI_RGB, BI_BITFIELDS = 0, 3
TEXT_LIMIT = 50_000  # Windows 98 Notepad offers WordPad above this


class GuestBuildMissing(FileNotFoundError):
    """guest/build has no programs: make hasn't run."""


def guest_build() -> Path:
    """Where `make` in guest/ leaves the programs that run on the guest. ART98_GUEST_BUILD
    overrides it, for a copy of the library that lives outside the checkout."""
    if os.environ.get("ART98_GUEST_BUILD"):
        return Path(os.environ["ART98_GUEST_BUILD"]).expanduser()
    return Path(__file__).resolve().parents[2] / "guest" / "build"


def _not_cp1252(text: str) -> bool:
    """True if protocol.py would turn part of it into "?" (it encodes with errors="replace")."""
    try:
        text.encode("cp1252")
    except UnicodeEncodeError:
        return True
    return False


def name_problem(name: str) -> str | None:
    """Why this can't be a file name on the guest command line, or None if it can."""
    if not name:
        return "is empty"
    if set(name) & BAD_NAME_CHARS:
        return 'has one of \\ / : * ? " < > |'
    if any(ord(c) < 0x20 for c in name):
        return "has a control character"
    if name[-1] in " .":
        return "ends in a space or a dot, which Windows drops"
    if _not_cp1252(name):
        return "has characters outside Windows 1252, which would become '?'"
    return None


def title_problem(title: str) -> str | None:
    """Why this can't be a window title on the guest command line, or None if it can."""
    if '"' in title:
        return "has a double quote"
    if any(ord(c) < 0x20 for c in title):
        return "has a control character"
    if title.endswith("\\"):
        return "ends in a backslash, which would escape the closing quote"
    if _not_cp1252(title):
        return "has characters outside Windows 1252, which would become '?'"
    return None


def check_name(name: str) -> str:
    """Return the file name, or raise ValueError saying what is wrong with it."""
    problem = name_problem(name)
    if problem:
        raise ValueError(f"{name!r} can't be a file name on the guest: it {problem}")
    return name


def guest_path(data: bytes, name: str) -> str:
    """The guest path for these bytes: C:\\ART98\\F<7 hex digits>\\<name>."""
    check_name(name)
    return rf"{GUEST_DIR}\F{hashlib.sha256(data).hexdigest()[:7]}\{name}"


def ensure(guest, data: bytes, name: str) -> str:
    """Copy data to the guest unless it is there already, and return its path."""
    path = guest_path(data, name)
    if guest.kernel32.GetFileAttributesA(path).value == INVALID_FILE_ATTRIBUTES:
        guest.kernel32.CreateDirectoryA(path.rsplit("\\", 1)[0], 0)
        guest.put_file(path, data)
    return path


def viewer() -> bytes:
    """VIEW.EXE, the picture window, as built in guest/build."""
    path = guest_build() / "view.exe"
    if not path.exists():
        raise GuestBuildMissing(f"{path} missing; run make in guest/")
    return path.read_bytes()


def check_bmp(data: bytes, source: str = "image") -> tuple[int, int]:
    """Return a BMP's (width, height), or raise ValueError if it isn't one Windows 98 can load."""
    if len(data) < 26 or data[:2] != b"BM":
        raise ValueError(f"{source} is not a BMP file")
    header = struct.unpack_from("<I", data, 14)[0]
    if header not in BMP_HEADER_SIZES or len(data) < 14 + header:
        raise ValueError(
            f"{source} has a BMP header Windows 98 can't read (size {header})"
        )
    compression = 0
    if header == 12:
        width, height, _, depth = struct.unpack_from("<HHHH", data, 18)
    else:
        width, height, _, depth, compression = struct.unpack_from("<iiHHI", data, 18)
    if width <= 0 or height == 0:
        raise ValueError(f"{source} has no pixels ({width}x{height})")
    if depth not in BMP_DEPTHS:
        raise ValueError(
            f"{source} uses {depth} bits per pixel, which Windows 98 can't load"
        )
    # LoadImage on Windows 98 can crash reading past the end, so the pixels must all be there.
    pixels = struct.unpack_from("<I", data, 10)[0]
    needed = pixels + (width * depth + 31) // 32 * 4 * abs(height)
    if compression in (BI_RGB, BI_BITFIELDS) and len(data) < needed:
        raise ValueError(
            f"{source} is cut short: {len(data)} bytes, its header needs {needed}"
        )
    if pixels >= len(data):
        raise ValueError(f"{source} is cut short: its pixels would start past the end")
    return width, abs(height)


def check_text(data: bytes, source: str = "text") -> bytes:
    """Check a text file Windows 98 Notepad can open, and return it with CRLF line ends."""
    if b"\0" in data:
        raise ValueError(f"{source} has NUL bytes, so it isn't a text file")
    if len(data) > TEXT_LIMIT:
        raise ValueError(
            f"{source} is {len(data)} bytes; Notepad on Windows 98 takes at most {TEXT_LIMIT}"
        )
    try:
        data.decode("cp1252")
    except UnicodeDecodeError as error:
        raise ValueError(
            f"{source} has bytes outside Windows 1252 (at {error.start})"
        ) from None
    # A bare LF shows as a box in Windows 98 Notepad.
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
