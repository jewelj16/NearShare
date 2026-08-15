"""HELLO handshake — version negotiation over a Connection.

Implements the HELLO exchange described in protocol.md §3.1 and §4.
Both sides send HELLO and wait for the peer's HELLO.  If protocol
versions are incompatible, an ERROR is sent and the connection is closed.
"""

from __future__ import annotations

import struct

from engine.protocol.codec import Frame, MessageType
from engine.types import PROTOCOL_VERSION, DeviceId, PeerInfo


class HandshakeError(Exception):
    """Raised when the HELLO handshake fails."""


# wire format for variable-length strings: [uint16 length][utf-8 bytes]
_STR_LEN_FMT = "!H"
_VERSION_FMT = "!H"


def _encode_string(s: str) -> bytes:
    """Encode a string as [uint16 length][utf-8 bytes]."""
    encoded = s.encode("utf-8")
    return struct.pack(_STR_LEN_FMT, len(encoded)) + encoded


def _decode_string(data: bytes, offset: int) -> tuple[str, int]:
    """Decode a string from [uint16 length][utf-8 bytes] at offset.

    Returns (decoded_string, new_offset).
    """
    if offset + 2 > len(data):
        raise HandshakeError("Truncated string length in payload")
    (length,) = struct.unpack_from(_STR_LEN_FMT, data, offset)
    offset += 2
    if offset + length > len(data):
        raise HandshakeError("Truncated string data in payload")
    s = data[offset:offset + length].decode("utf-8")
    return s, offset + length


def build_hello_payload(
    device_id: DeviceId,
    display_name: str,
    proto_version: int = PROTOCOL_VERSION,
) -> bytes:
    """Build the HELLO message payload.

    Layout: [uint16 proto_version][string device_id][string display_name]
    """
    parts = [
        struct.pack(_VERSION_FMT, proto_version),
        _encode_string(device_id),
        _encode_string(display_name),
    ]
    return b"".join(parts)


def parse_hello_payload(payload: bytes) -> tuple[int, DeviceId, str]:
    """Parse a HELLO payload into (proto_version, device_id, display_name).

    Raises:
        HandshakeError: If the payload is malformed or truncated.
    """
    if len(payload) < 2:
        raise HandshakeError("HELLO payload too short")

    (proto_version,) = struct.unpack_from(_VERSION_FMT, payload, 0)
    offset = 2

    device_id_str, offset = _decode_string(payload, offset)
    display_name, offset = _decode_string(payload, offset)

    return proto_version, DeviceId(device_id_str), display_name


async def perform_handshake(
    conn: object,
    local_device_id: DeviceId,
    local_display_name: str,
    local_proto_version: int = PROTOCOL_VERSION,
) -> PeerInfo:
    """Execute the full HELLO handshake on a connection.

    Sends our HELLO, waits for the peer's HELLO, and validates
    the protocol version.  On version mismatch, sends an ERROR frame
    and closes the connection.

    Args:
        conn:                Connection-like object with send_frame/recv_frame/close.
        local_device_id:     Our device ID.
        local_display_name:  Our display name.
        local_proto_version: Our protocol version (default PROTOCOL_VERSION).

    Returns:
        PeerInfo populated from the peer's HELLO.

    Raises:
        HandshakeError: If version negotiation fails or the peer sends
                        an unexpected message.
    """
    # send our HELLO
    payload = build_hello_payload(local_device_id, local_display_name, local_proto_version)
    await conn.send_frame(MessageType.HELLO, payload)  # type: ignore[attr-defined]

    # wait for peer's HELLO
    frame: Frame = await conn.recv_frame()  # type: ignore[attr-defined]

    if frame.msg_type is MessageType.ERROR:
        raise HandshakeError("Peer sent ERROR during handshake")

    if frame.msg_type is not MessageType.HELLO:
        raise HandshakeError(
            f"Expected HELLO, got {frame.msg_type.name}"
        )

    peer_version, peer_device_id, peer_display_name = parse_hello_payload(frame.payload)

    # version check
    if peer_version != local_proto_version:
        # send ERROR with code 0x0004 (version mismatch)
        error_payload = struct.pack("!H", 0x0004) + _encode_string(
            f"Version mismatch: local={local_proto_version}, peer={peer_version}"
        )
        await conn.send_frame(MessageType.ERROR, error_payload)  # type: ignore[attr-defined]
        await conn.close()  # type: ignore[attr-defined]
        raise HandshakeError(
            f"Protocol version mismatch: "
            f"local={local_proto_version}, peer={peer_version}"
        )

    host, port = conn.peer_address  # type: ignore[attr-defined]
    return PeerInfo(
        device_id=peer_device_id,
        display_name=peer_display_name,
        ip=host,
        port=port,
        proto_version=peer_version,
    )
