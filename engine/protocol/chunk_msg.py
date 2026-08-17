"""CHUNK (0x06) message payload serialization.

Implements build/parse for the CHUNK wire format as specified in
protocol.md §3.6.

Layout:
    [string transfer_id][uint16 file_index][uint32 seq]
    [uint32 data_length][N bytes data][64-byte ASCII sha256]
"""

from __future__ import annotations

import struct

from engine.protocol.wire import WireError, encode_string, decode_string
from engine.types import Chunk


class ChunkMsgError(Exception):
    """Raised when CHUNK payload parsing fails."""


# fmt strings (big-endian, no alignment)
_FILE_INDEX_FMT = "!H"   # uint16
_SEQ_FMT        = "!I"   # uint32
_DATA_LEN_FMT   = "!I"   # uint32
_SHA256_BYTES   = 64     # ASCII hex


def build_chunk_payload(chunk: Chunk) -> bytes:
    """Encode a Chunk into a CHUNK (0x06) payload.

    Args:
        chunk: The Chunk to encode.

    Returns:
        Raw payload bytes ready to pass to Frame/codec.
    """
    parts: list[bytes] = [
        encode_string(chunk.transfer_id),
        struct.pack(_FILE_INDEX_FMT, chunk.file_index),
        struct.pack(_SEQ_FMT, chunk.seq),
        struct.pack(_DATA_LEN_FMT, len(chunk.data)),
        chunk.data,
        chunk.sha256.encode("ascii"),
    ]
    return b"".join(parts)


def parse_chunk_payload(payload: bytes) -> Chunk:
    """Decode a CHUNK payload into a Chunk.

    Args:
        payload: Raw bytes from a received CHUNK frame.

    Returns:
        A Chunk dataclass instance.

    Raises:
        ChunkMsgError: If the payload is malformed or truncated.
    """
    try:
        offset = 0

        # transfer_id
        transfer_id, offset = decode_string(payload, offset)

        # file_index (uint16)
        if offset + 2 > len(payload):
            raise ChunkMsgError("Truncated file_index")
        (file_index,) = struct.unpack_from(_FILE_INDEX_FMT, payload, offset)
        offset += 2

        # seq (uint32)
        if offset + 4 > len(payload):
            raise ChunkMsgError("Truncated seq")
        (seq,) = struct.unpack_from(_SEQ_FMT, payload, offset)
        offset += 4

        # data_length (uint32)
        if offset + 4 > len(payload):
            raise ChunkMsgError("Truncated data_length")
        (data_length,) = struct.unpack_from(_DATA_LEN_FMT, payload, offset)
        offset += 4

        # data
        if offset + data_length > len(payload):
            raise ChunkMsgError("Truncated data field")
        data = payload[offset : offset + data_length]
        offset += data_length

        # sha256 (64 ASCII bytes)
        if offset + _SHA256_BYTES > len(payload):
            raise ChunkMsgError("Truncated sha256 field")
        sha256 = payload[offset : offset + _SHA256_BYTES].decode("ascii")
        offset += _SHA256_BYTES

        return Chunk(
            transfer_id=transfer_id,
            file_index=file_index,
            seq=seq,
            data=data,
            sha256=sha256,
        )

    except WireError as exc:
        raise ChunkMsgError(str(exc)) from exc
