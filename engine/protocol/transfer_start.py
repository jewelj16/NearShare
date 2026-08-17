"""TRANSFER_START (0x05) message payload serialization.

Implements build/parse for the TRANSFER_START wire format as specified
in protocol.md §3.5.

Layout:
    [string transfer_id]

The sender emits TRANSFER_START after receiving ACCEPT to signal that
chunk transmission is about to begin.  The receiver uses this as the
cue to arm its ChunkAssembler and start listening for CHUNK frames.
"""

from __future__ import annotations

from engine.protocol.wire import WireError, encode_string, decode_string


class TransferStartError(Exception):
    """Raised when TRANSFER_START payload parsing fails."""


def build_transfer_start_payload(transfer_id: str) -> bytes:
    """Encode a TRANSFER_START (0x05) payload.

    Args:
        transfer_id: The session identifier.

    Returns:
        Raw payload bytes ready to pass to the codec.
    """
    return encode_string(transfer_id)


def parse_transfer_start_payload(payload: bytes) -> str:
    """Decode a TRANSFER_START payload.

    Args:
        payload: Raw bytes from a received TRANSFER_START frame.

    Returns:
        The transfer_id string.

    Raises:
        TransferStartError: If the payload is malformed or truncated.
    """
    try:
        transfer_id, _ = decode_string(payload, 0)
        return transfer_id
    except WireError as exc:
        raise TransferStartError(str(exc)) from exc
