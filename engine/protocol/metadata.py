"""METADATA, ACCEPT, and REJECT message handling.

Implements payload serialization for METADATA (0x02), ACCEPT (0x03),
and REJECT (0x04) as specified in protocol.md §3.2–§3.4.
"""

from __future__ import annotations

import struct

from engine.protocol.codec import Frame, MessageType
from engine.protocol.handshake import _encode_string, _decode_string, HandshakeError
from engine.types import FileMetadata, DEFAULT_CHUNK_SIZE


class MetadataError(Exception):
    """Raised when METADATA/ACCEPT/REJECT handling fails."""


# METADATA payload

def build_metadata_payload(
    transfer_id: str,
    files: list[FileMetadata],
) -> bytes:
    """Build a METADATA message payload.

    Layout: [string transfer_id][uint16 file_count][FileEntry × N]

    FileEntry: [string name][uint64 size][uint32 chunk_size]
               [uint32 chunk_count][string mime_type][64-byte ASCII sha256]
    """
    parts: list[bytes] = []
    parts.append(_encode_string(transfer_id))
    parts.append(struct.pack("!H", len(files)))

    for f in files:
        parts.append(_encode_string(f.name))
        parts.append(struct.pack("!Q", f.size))
        parts.append(struct.pack("!I", f.chunk_size))
        parts.append(struct.pack("!I", f.chunk_count))
        parts.append(_encode_string(f.mime_type or ""))
        sha = f.sha256 or ("0" * 64)
        parts.append(sha.encode("ascii"))

    return b"".join(parts)


def parse_metadata_payload(payload: bytes) -> tuple[str, list[FileMetadata]]:
    """Parse a METADATA payload into (transfer_id, list of FileMetadata).

    Raises:
        MetadataError: If the payload is malformed.
    """
    try:
        offset = 0
        transfer_id, offset = _decode_string(payload, offset)

        if offset + 2 > len(payload):
            raise MetadataError("Truncated file count")
        (file_count,) = struct.unpack_from("!H", payload, offset)
        offset += 2

        files: list[FileMetadata] = []
        for _ in range(file_count):
            name, offset = _decode_string(payload, offset)

            if offset + 16 > len(payload):
                raise MetadataError("Truncated file entry")
            (size,) = struct.unpack_from("!Q", payload, offset)
            offset += 8
            (chunk_size,) = struct.unpack_from("!I", payload, offset)
            offset += 4
            (chunk_count,) = struct.unpack_from("!I", payload, offset)
            offset += 4

            mime_type, offset = _decode_string(payload, offset)

            if offset + 64 > len(payload):
                raise MetadataError("Truncated SHA-256 field")
            sha256_raw = payload[offset:offset + 64].decode("ascii")
            offset += 64

            sha256 = sha256_raw if sha256_raw != ("0" * 64) else None

            files.append(FileMetadata(
                name=name,
                size=size,
                mime_type=mime_type or None,
                sha256=sha256,
                chunk_size=chunk_size,
                chunk_count=chunk_count,
            ))

        return transfer_id, files

    except HandshakeError as e:
        raise MetadataError(str(e)) from e


# ACCEPT payload

def build_accept_payload(transfer_id: str) -> bytes:
    """Build an ACCEPT (0x03) payload: just the transfer_id string."""
    return _encode_string(transfer_id)


def parse_accept_payload(payload: bytes) -> str:
    """Parse an ACCEPT payload, returns transfer_id.

    Raises:
        MetadataError: If the payload is malformed.
    """
    try:
        transfer_id, _ = _decode_string(payload, 0)
        return transfer_id
    except HandshakeError as e:
        raise MetadataError(str(e)) from e


# REJECT payload

_REJECT_USER_DECLINED = 0x01
_REJECT_NO_SPACE = 0x02
_REJECT_OTHER = 0x03

REJECT_REASONS = {
    _REJECT_USER_DECLINED: "user_declined",
    _REJECT_NO_SPACE: "no_space",
    _REJECT_OTHER: "other",
}


def build_reject_payload(
    transfer_id: str,
    reason_code: int = _REJECT_USER_DECLINED,
    reason_msg: str = "",
) -> bytes:
    """Build a REJECT (0x04) payload.

    Layout: [string transfer_id][uint8 reason_code][string reason_msg]
    """
    parts = [
        _encode_string(transfer_id),
        struct.pack("!B", reason_code),
        _encode_string(reason_msg),
    ]
    return b"".join(parts)


def parse_reject_payload(payload: bytes) -> tuple[str, int, str]:
    """Parse a REJECT payload into (transfer_id, reason_code, reason_msg).

    Raises:
        MetadataError: If the payload is malformed.
    """
    try:
        offset = 0
        transfer_id, offset = _decode_string(payload, offset)

        if offset + 1 > len(payload):
            raise MetadataError("Truncated reason code")
        (reason_code,) = struct.unpack_from("!B", payload, offset)
        offset += 1

        reason_msg, offset = _decode_string(payload, offset)
        return transfer_id, reason_code, reason_msg
    except HandshakeError as e:
        raise MetadataError(str(e)) from e


# high-level flow helpers

async def send_metadata(
    conn: object,
    transfer_id: str,
    files: list[FileMetadata],
) -> None:
    """Send a METADATA message on the connection."""
    payload = build_metadata_payload(transfer_id, files)
    await conn.send_frame(MessageType.METADATA, payload)  # type: ignore[attr-defined]


async def wait_for_accept_or_reject(
    conn: object,
) -> tuple[str, bool, int | None, str | None]:
    """Wait for ACCEPT or REJECT from the peer.

    Returns:
        (transfer_id, accepted, reason_code, reason_msg)
        If accepted, reason_code and reason_msg are None.

    Raises:
        MetadataError: If an unexpected message type is received.
    """
    frame: Frame = await conn.recv_frame()  # type: ignore[attr-defined]

    if frame.msg_type is MessageType.ACCEPT:
        tid = parse_accept_payload(frame.payload)
        return tid, True, None, None

    if frame.msg_type is MessageType.REJECT:
        tid, code, msg = parse_reject_payload(frame.payload)
        return tid, False, code, msg

    raise MetadataError(f"Expected ACCEPT or REJECT, got {frame.msg_type.name}")
