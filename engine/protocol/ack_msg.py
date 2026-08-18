"""ACK (0x07) message payload serialization.

Implements build/parse for the ACK wire format as specified in
protocol.md §3.7.  This module handles **only** the wire encoding;
ACK tracking (bitmaps, completion checks) lives in engine/transfer/ack.py.

Layout:
    [string transfer_id][uint16 file_index]
    [uint32 acked_count][uint32 × N acked_seqs (sorted)]
"""

from __future__ import annotations

import struct

from engine.protocol.wire import WireError, encode_string, decode_string


class AckMsgError(Exception):
    """Raised when ACK payload parsing fails."""


def build_ack_msg_payload(
    transfer_id: str,
    file_index: int,
    acked_seqs: set[int],
) -> bytes:
    """Encode an ACK (0x07) payload.

    Args:
        transfer_id: The session identifier.
        file_index:  Zero-based index of the file being acknowledged.
        acked_seqs:  Set of chunk sequence numbers to acknowledge.

    Returns:
        Raw payload bytes ready to pass to the codec.
    """
    sorted_seqs = sorted(acked_seqs)
    parts = [
        encode_string(transfer_id),
        struct.pack("!H", file_index),
        struct.pack("!I", len(sorted_seqs)),
    ]
    for s in sorted_seqs:
        parts.append(struct.pack("!I", s))
    return b"".join(parts)


def parse_ack_msg_payload(payload: bytes) -> tuple[str, int, set[int]]:
    """Decode an ACK (0x07) payload.

    Args:
        payload: Raw bytes from a received ACK frame.

    Returns:
        Tuple of (transfer_id, file_index, acked_seqs).

    Raises:
        AckMsgError: If the payload is malformed or truncated.
    """
    try:
        offset = 0
        transfer_id, offset = decode_string(payload, offset)

        if offset + 2 > len(payload):
            raise AckMsgError("Truncated file_index in ACK")
        (file_index,) = struct.unpack_from("!H", payload, offset)
        offset += 2

        if offset + 4 > len(payload):
            raise AckMsgError("Truncated acked_count in ACK")
        (acked_count,) = struct.unpack_from("!I", payload, offset)
        offset += 4

        acked_seqs: set[int] = set()
        for _ in range(acked_count):
            if offset + 4 > len(payload):
                raise AckMsgError("Truncated acked seq in ACK")
            (s,) = struct.unpack_from("!I", payload, offset)
            offset += 4
            acked_seqs.add(s)

        return transfer_id, file_index, acked_seqs

    except WireError as exc:
        raise AckMsgError(str(exc)) from exc
