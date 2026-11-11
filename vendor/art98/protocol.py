"""Wire format shared with guest/call_proxy/agent.c. See the header comment there."""

from __future__ import annotations

from dataclasses import dataclass

ENCODING = "cp1252"
PROTOCOL_VERSION = 1


class FrameError(ValueError):
    """A line did not carry a valid checksum."""


@dataclass(frozen=True)
class Out:
    """A zeroed buffer the guest fills in, such as a RECT for GetWindowRect."""

    size: int


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021 if crc & 0x8000 else crc << 1) & 0xFFFF
    return crc


def frame(payload: bytes) -> bytes:
    return b"%s *%04x\n" % (payload, crc16(payload))


def unframe(line: bytes) -> bytes:
    """Strip and check the checksum of one line, without its newline."""
    if len(line) < 6 or line[-6:-4] != b" *":
        raise FrameError(f"no checksum in {line[:60]!r}")
    payload = line[:-6]
    try:
        expected = int(line[-4:], 16)
    except ValueError:
        raise FrameError(f"bad checksum in {line[:60]!r}") from None
    if crc16(payload) != expected:
        raise FrameError(f"checksum mismatch in {line[:60]!r}")
    return payload


def _encode_string(text: str) -> bytes:
    out = bytearray(b'"')
    for byte in text.encode(ENCODING, errors="replace"):
        if byte in b'"\\':
            out += b"\\" + bytes([byte])
        elif byte < 0x20:
            out += b"\\x%02x" % byte
        else:
            out.append(byte)
    out += b'"'
    return bytes(out)


def encode_arg(arg: object) -> bytes:
    if arg is None:
        return b"0"
    if isinstance(arg, bool):
        return b"1" if arg else b"0"
    if isinstance(arg, int):
        return b"%d" % (arg & 0xFFFFFFFF)
    if isinstance(arg, str):
        return _encode_string(arg)
    if isinstance(arg, (bytes, bytearray, memoryview)):
        return b"#" + bytes(arg).hex().encode()
    if isinstance(arg, Out):
        return b"&%d" % arg.size
    raise TypeError(f"cannot send {type(arg).__name__} to the guest")


def encode_request(request_id: int, verb: str, args: tuple | list = ()) -> bytes:
    parts = [b"%d" % request_id, verb.encode("ascii")]
    parts += [encode_arg(arg) for arg in args]
    return frame(b" ".join(parts))


def parse_values(data: bytes) -> list[int | str | bytes]:
    """Parse the space separated values in a reply or event."""
    values: list[int | str | bytes] = []
    i, n = 0, len(data)
    while i < n:
        c = data[i]
        if c == 0x20:
            i += 1
        elif c == 0x22:
            text, i = _parse_string(data, i + 1)
            values.append(text)
        elif c == 0x23:
            end = data.find(b" ", i)
            end = n if end < 0 else end
            values.append(bytes.fromhex(data[i + 1 : end].decode("ascii")))
            i = end
        else:
            end = data.find(b" ", i)
            end = n if end < 0 else end
            values.append(int(data[i:end], 0))
            i = end
    return values


def _parse_string(data: bytes, i: int) -> tuple[str, int]:
    out = bytearray()
    while True:
        if i >= len(data):
            raise FrameError("unterminated string")
        c = data[i]
        if c == 0x22:
            return out.decode(ENCODING, errors="replace"), i + 1
        if c == 0x5C:
            next_byte = data[i + 1]
            if next_byte == ord("x"):
                out.append(int(data[i + 2 : i + 4], 16))
                i += 4
            else:
                out.append(next_byte)
                i += 2
        else:
            out.append(c)
            i += 1
