"""Low-level wire format helpers shared across protocol modules.

Provides encode/decode for variable-length strings used in HELLO,
METADATA, ACCEPT, REJECT, and other message payloads.

Wire string format: [uint16 length][UTF-8 bytes]
"""

from __future__ import annotations

import struct

_STR_LEN_FMT = "!H"


class WireError(Exception):
    """Raised when wire-level parsing fails (truncated data, etc.)."""


def encode_string(s: str) -> bytes:
    """Encode a string as [uint16 length][utf-8 bytes]."""
    encoded = s.encode("utf-8")
    return struct.pack(_STR_LEN_FMT, len(encoded)) + encoded


def decode_string(data: bytes, offset: int) -> tuple[str, int]:
    """Decode a string from [uint16 length][utf-8 bytes] at offset.

    Returns (decoded_string, new_offset).

    Raises:
        WireError: If the data is truncated.
    """
    if offset + 2 > len(data):
        raise WireError("Truncated string length in payload")
    (length,) = struct.unpack_from(_STR_LEN_FMT, data, offset)
    offset += 2
    if offset + length > len(data):
        raise WireError("Truncated string data in payload")
    s = data[offset:offset + length].decode("utf-8")
    return s, offset + length
