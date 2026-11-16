"""A tiny ISO 9660 level 1 writer: one root directory, no Joliet, no Rock Ridge, no El Torito.

Layout (same as the pycdlib image that Windows 98 mounted in probe P2):
sectors 0-15 zero, 16 PVD, 17 terminator, 18/19 path tables (L/M), 20 root directory, 21+ files.
"""

import re
import struct
import time
from datetime import UTC, datetime

from ..errors import GuestError
from .win32 import DRIVE_CDROM, INVALID_FILE_ATTRIBUTES

SECTOR = 2048
VOLUME = "ART98"  # the volume id of every disc
DISC_POLL = 0.05  # seconds between looks for a new disc
DISC_TIMEOUT = 2.0  # seconds to wait for one
_NAME = re.compile(r"^[A-Z0-9_]{1,8}\.[A-Z0-9_]{1,3}$")


def _both(n: int, fmt: str) -> bytes:
    """A number in both byte orders (little then big), as ECMA-119 stores most fields."""
    return struct.pack("<" + fmt, n) + struct.pack(">" + fmt, n)


def _record(extent: int, size: int, flags: int, name: bytes, now: bytes) -> bytes:
    body = (
        struct.pack("B", 0)
        + _both(extent, "I")
        + _both(size, "I")
        + now
        + struct.pack("BBB", flags, 0, 0)
        + _both(1, "H")
        + struct.pack("B", len(name))
        + name
    )
    if len(body) % 2 == 0:  # the whole record, length byte included, must be even
        body += b"\0"
    return bytes([len(body) + 1]) + body


def _text(s: str, n: int) -> bytes:
    return s.encode("ascii").ljust(n, b" ")


def build_iso(files: dict[str, bytes]) -> bytes:
    """Return a level 1 ISO image holding `files` (name -> bytes) in its root directory."""
    for name in files:
        if not _NAME.match(name):
            raise ValueError(
                f"bad ISO 9660 level 1 name {name!r}: need 8.3 of A-Z 0-9 _"
            )
    d = datetime.now(UTC)
    now = bytes([d.year - 1900, d.month, d.day, d.hour, d.minute, d.second, 0])
    stamp = d.strftime("%Y%m%d%H%M%S00").encode() + b"\0"

    names = sorted(files)
    extents, sector = {}, 21
    for n in names:
        extents[n] = sector
        sector += -(-len(files[n]) // SECTOR)  # ceil; an empty file takes no sectors
    total = sector

    root = 20
    dot = lambda b: _record(root, SECTOR, 2, b, now)
    directory = dot(b"\0") + dot(b"\1")
    for n in names:
        directory += _record(extents[n], len(files[n]), 0, f"{n};1".encode(), now)
    # lazy: one directory sector holds about 40 files; a bigger set needs multi-sector directories
    if len(directory) > SECTOR:
        raise ValueError("too many files for a one-sector root directory")

    # root entry: id length 1, no ext attrs, extent, parent 1, id 0x00, pad
    path_l = struct.pack("<BBIH", 1, 0, root, 1) + b"\0\0"
    path_m = struct.pack(">BBIH", 1, 0, root, 1) + b"\0\0"

    pvd = (
        b"\1CD001\1\0"
        + _text("", 32)
        + _text(VOLUME, 32)
        + bytes(8)
        + _both(total, "I")
        + bytes(32)
        + _both(1, "H")
        + _both(1, "H")
        + _both(SECTOR, "H")
        + _both(len(path_l), "I")
        + struct.pack("<II", 18, 0)
        + struct.pack(">II", 19, 0)
        + _record(root, SECTOR, 2, b"\0", now)
        + _text("", 128 * 4 + 37 * 3)
        + stamp
        + stamp
        + bytes(17)
        + stamp
        + b"\1\0"
    )
    pvd = pvd.ljust(SECTOR, b"\0")
    term = b"\xffCD001\1".ljust(SECTOR, b"\0")

    out = bytearray(SECTOR * 16) + pvd + term
    out += (
        path_l.ljust(SECTOR, b"\0")
        + path_m.ljust(SECTOR, b"\0")
        + directory.ljust(SECTOR, b"\0")
    )
    for n in names:
        out += files[n].ljust(-(-len(files[n]) // SECTOR) * SECTOR, b"\0")
    return bytes(out)


def cd_drive(guest) -> str | None:
    """The CD drive as "D:\\", or None if the guest has none."""
    mask = guest.kernel32.GetLogicalDrives().value
    for i in range(26):
        letter = f"{chr(65 + i)}:\\"
        if mask >> i & 1 and guest.kernel32.GetDriveTypeA(letter).value == DRIVE_CDROM:
            return letter
    return None


def wait_for_disc(guest, name: str, timeout: float = DISC_TIMEOUT) -> str:
    """Poll every DISC_POLL seconds until `name` can be read on the CD; return its full path.

    Raises GuestError if it doesn't show up within `timeout` seconds.
    """
    deadline = time.monotonic() + timeout
    while True:
        letter = cd_drive(guest)
        if (
            letter
            and guest.kernel32.GetFileAttributesA(letter + name).value
            != INVALID_FILE_ATTRIBUTES
        ):
            return letter + name
        if time.monotonic() >= deadline:
            raise GuestError(f"the CD with {name} did not show up in {timeout:g} s")
        time.sleep(DISC_POLL)
