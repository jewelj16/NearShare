"""Length-prefixed frame codec for the NearShare protocol.

Frame layout (see docs/architecture/protocol.md §1):

    [4-byte big-endian payload length][1-byte message type][payload]

The payload length field counts only the payload bytes, not the type byte.
Maximum payload size is MAX_PAYLOAD_SIZE (64 MiB).
"""

from __future__ import annotations

import enum
import struct
from dataclasses import dataclass

# 64 MiB max payload
MAX_PAYLOAD_SIZE: int = 67_108_864

# struct formats
_HEADER_FMT = "!IB"  # uint32 payload_length + uint8 msg_type
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)  # 5 bytes
_LENGTH_FMT = "!I"
_LENGTH_SIZE = struct.calcsize(_LENGTH_FMT)  # 4 bytes


class MessageType(enum.IntEnum):
    """Protocol message type codes (see protocol.md §2)."""

    HELLO             = 0x01
    METADATA          = 0x02
    ACCEPT            = 0x03
    REJECT            = 0x04
    TRANSFER_START    = 0x05
    CHUNK             = 0x06
    ACK               = 0x07
    CANCEL            = 0x08
    TRANSFER_COMPLETE = 0x09
    ERROR             = 0x0A
    JOIN_SESSION      = 0x0B


class CodecError(Exception):
    """Raised when encoding or decoding a frame fails."""


@dataclass(frozen=True)
class Frame:
    """A decoded protocol frame.

    Attributes:
        msg_type: The message type code.
        payload:  Raw payload bytes (may be empty).
    """

    msg_type: MessageType
    payload: bytes


def encode_frame(msg_type: MessageType, payload: bytes = b"") -> bytes:
    """Encode a message type and payload into a length-prefixed frame.

    Returns the complete frame bytes ready to send on the wire.

    Raises:
        CodecError: If the payload exceeds MAX_PAYLOAD_SIZE.
    """
    if len(payload) > MAX_PAYLOAD_SIZE:
        raise CodecError(
            f"Payload too large: {len(payload)} bytes "
            f"(max {MAX_PAYLOAD_SIZE})"
        )
    header = struct.pack(_HEADER_FMT, len(payload), msg_type)
    return header + payload


def decode_frame(data: bytes) -> Frame:
    """Decode a complete frame from raw bytes.

    Expects exactly one full frame (header + payload). Use
    decode_frame_header to parse incrementally from a stream.

    Raises:
        CodecError: If the data is too short, the message type is unknown,
                    or the payload length doesn't match the remaining bytes.
    """
    if len(data) < _HEADER_SIZE:
        raise CodecError(
            f"Frame too short: need at least {_HEADER_SIZE} bytes, "
            f"got {len(data)}"
        )

    payload_length, raw_type = struct.unpack_from(_HEADER_FMT, data)

    try:
        msg_type = MessageType(raw_type)
    except ValueError:
        raise CodecError(f"Unknown message type: 0x{raw_type:02X}")

    if payload_length > MAX_PAYLOAD_SIZE:
        raise CodecError(
            f"Payload length {payload_length} exceeds max {MAX_PAYLOAD_SIZE}"
        )

    expected_total = _HEADER_SIZE + payload_length
    if len(data) < expected_total:
        raise CodecError(
            f"Incomplete frame: header says {payload_length} payload bytes, "
            f"but only {len(data) - _HEADER_SIZE} available"
        )

    payload = data[_HEADER_SIZE : expected_total]
    return Frame(msg_type=msg_type, payload=payload)


def decode_frame_header(data: bytes) -> tuple[int, MessageType]:
    """Parse just the frame header and return (payload_length, msg_type).

    This is useful for stream-based reading: first read HEADER_SIZE bytes,
    call this to learn how many payload bytes to read next.

    Raises:
        CodecError: If the data is too short or the message type is unknown.
    """
    if len(data) < _HEADER_SIZE:
        raise CodecError(
            f"Header too short: need {_HEADER_SIZE} bytes, got {len(data)}"
        )

    payload_length, raw_type = struct.unpack_from(_HEADER_FMT, data)

    try:
        msg_type = MessageType(raw_type)
    except ValueError:
        raise CodecError(f"Unknown message type: 0x{raw_type:02X}")

    if payload_length > MAX_PAYLOAD_SIZE:
        raise CodecError(
            f"Payload length {payload_length} exceeds max {MAX_PAYLOAD_SIZE}"
        )

    return payload_length, msg_type
