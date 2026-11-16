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
from dataclasses import dataclass
from pathlib import Path

from ..errors import MissingFile, UsageError
from .win32 import BI_BITFIELDS, BI_JPEG, BI_PNG, BI_RGB, INVALID_FILE_ATTRIBUTES

GUEST_DIR = r"C:\ART98"
BAD_NAME_CHARS = set('\\/:*?"<>|')
BMP_HEADER_SIZES = {12, 40, 52, 56, 108, 124}
BMP_DEPTHS = {1, 4, 8, 16, 24, 32}
# A path is at most 260 characters with its NUL; `C:\ART98\F1234567\` takes 17.
MAX_NAME = 260 - 1 - len(GUEST_DIR + "\\F1234567\\")
DOS_DEVICES = {"CON", "PRN", "AUX", "NUL"}
DOS_DEVICES |= {f"{kind}{n}" for kind in ("COM", "LPT") for n in range(1, 10)}
BMP_COMPRESSIONS = {BI_RGB, BI_BITFIELDS}  # the ones whose pixel size is known
BMP_REFUSED = {BI_JPEG, BI_PNG}  # LoadImage on Windows 98 can't read these
TEXT_LIMIT = 50_000  # Windows 98 Notepad offers WordPad above this


class GuestBuildMissing(MissingFile):
    """guest/build has no programs: make hasn't run."""


def guest_build() -> Path:
    """Where `make` in guest/ leaves the programs that run on the guest. ART98_GUEST_BUILD
    overrides it, for a copy of the library that lives outside the checkout."""
    if os.environ.get("ART98_GUEST_BUILD"):
        return Path(os.environ["ART98_GUEST_BUILD"]).expanduser()
    return Path(__file__).resolve().parents[3] / "guest" / "build"


def not_cp1252(text: str) -> str:
    """The characters of `text` that Windows 1252 can't hold, which protocol.py
    would turn into "?". Empty if none."""
    return "".join(sorted({c for c in text if not _fits_cp1252(c)}))


def _fits_cp1252(char: str) -> bool:
    try:
        char.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


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
    if not_cp1252(name):
        return "has characters outside Windows 1252, which would become '?'"
    if len(name) > MAX_NAME:
        return f"is {len(name)} characters; the longest the guest path allows is {MAX_NAME}"
    if name.split(".")[0].upper() in DOS_DEVICES:
        return "is a DOS device name (CON, PRN, AUX, NUL, COM1-9, LPT1-9)"
    return None


def title_problem(title: str) -> str | None:
    """Why this can't be a window title on the guest command line, or None if it can."""
    if '"' in title:
        return "has a double quote"
    if any(ord(c) < 0x20 for c in title):
        return "has a control character"
    if title.endswith("\\"):
        return "ends in a backslash, which would escape the closing quote"
    if not_cp1252(title):
        return "has characters outside Windows 1252, which would become '?'"
    return None


def read_host_file(path: Path) -> bytes:
    """The bytes of a file on the host, or MissingFile if it isn't there."""
    try:
        return path.read_bytes()
    except FileNotFoundError:
        raise MissingFile(f"no file {str(path)!r}") from None


def check_name(name: str) -> str:
    """Return the file name, or raise UsageError saying what is wrong with it."""
    problem = name_problem(name)
    if problem:
        raise UsageError(f"{name!r} can't be a file name on the guest: it {problem}")
    return name


def guest_path(data: bytes, name: str) -> str:
    """The guest path for these bytes: C:\\ART98\\F<7 hex digits>\\<name>."""
    check_name(name)
    return rf"{GUEST_DIR}\F{hashlib.sha256(data).hexdigest()[:7]}\{name}"


def ensure(guest, data: bytes, name: str) -> str:
    """Copy data to the guest unless a file is there already, and return its path.

    The folder name is the first 7 hex digits of the data's hash, so a file found there is
    almost certainly these bytes. A copy cut short earlier in the same boot would be reused.
    """
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


@dataclass(frozen=True)
class BmpHeader:
    """What a BMP's header says. `height` is negative for a top-down image; `pixels` is the
    offset of the pixel data and `palette` the offset of the colour table."""

    width: int
    height: int
    depth: int
    compression: int
    pixels: int
    palette: int


def read_bmp_header(data: bytes, source: str = "image") -> BmpHeader:
    """Read a BMP's header, or raise UsageError if it isn't one Windows 98 can load."""
    if len(data) < 26 or data[:2] != b"BM":
        raise UsageError(f"{source} is not a BMP file")
    size = struct.unpack_from("<I", data, 14)[0]
    if size not in BMP_HEADER_SIZES or len(data) < 14 + size:
        raise UsageError(
            f"{source} has a BMP header Windows 98 can't read (size {size})"
        )
    compression = 0
    if size == 12:
        width, height, _, depth = struct.unpack_from("<HHHH", data, 18)
    else:
        width, height, _, depth, compression = struct.unpack_from("<iiHHI", data, 18)
    if width <= 0 or height == 0:
        raise UsageError(f"{source} has no pixels ({width}x{height})")
    if depth not in BMP_DEPTHS:
        raise UsageError(
            f"{source} uses {depth} bits per pixel, which Windows 98 can't load"
        )
    # LoadImage on Windows 98 can crash reading past the end, so the pixels must all be there.
    pixels = struct.unpack_from("<I", data, 10)[0]
    needed = pixels + (width * depth + 31) // 32 * 4 * abs(height)
    if compression in BMP_COMPRESSIONS and len(data) < needed:
        raise UsageError(
            f"{source} is cut short: {len(data)} bytes, its header needs {needed}"
        )
    if compression in BMP_REFUSED:
        raise UsageError(
            f"{source} uses BMP compression {compression} (JPEG or PNG inside a BMP),"
            " which Windows 98 can't load"
        )
    if pixels >= len(data):
        raise UsageError(f"{source} is cut short: its pixels would start past the end")
    return BmpHeader(width, height, depth, compression, pixels, 14 + size)


def check_bmp(data: bytes, source: str = "image") -> tuple[int, int]:
    """Return a BMP's (width, height), or raise UsageError if it isn't one Windows 98 can load."""
    header = read_bmp_header(data, source)
    return header.width, abs(header.height)


def check_text(data: bytes, source: str = "text") -> bytes:
    """Check a text file Windows 98 Notepad can open, and return it with CRLF line ends."""
    if b"\0" in data:
        raise UsageError(f"{source} has NUL bytes, so it isn't a text file")
    try:
        data.decode("cp1252")
    except UnicodeDecodeError as error:
        raise UsageError(
            f"{source} has bytes outside Windows 1252 (at {error.start})"
        ) from None
    # A bare LF shows as a box in Windows 98 Notepad.
    data = data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")
    if len(data) > TEXT_LIMIT:
        raise UsageError(
            f"{source} is {len(data)} bytes after line ends are fixed;"
            f" Notepad on Windows 98 takes at most {TEXT_LIMIT}"
        )
    return data
