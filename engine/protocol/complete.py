"""TRANSFER_COMPLETE verification and whole-file integrity check.

Handles the end-of-transfer flow: after all chunks are ACK'd, the sender
sends TRANSFER_COMPLETE.  The receiver verifies the whole-file SHA-256
before committing.

See protocol.md §3.9 (TRANSFER_COMPLETE).
"""

from __future__ import annotations

import struct

from engine.integrity.hasher import file_hash_incremental, _IncrementalHasher
from engine.protocol.wire import encode_string, decode_string, WireError
from engine.types import FileMetadata


class IntegrityError(Exception):
    """Raised when whole-file hash verification fails."""


def build_transfer_complete_payload(transfer_id: str) -> bytes:
    """Build a TRANSFER_COMPLETE (0x09) payload.

    Layout: [string transfer_id]
    """
    return encode_string(transfer_id)


def parse_transfer_complete_payload(payload: bytes) -> str:
    """Parse a TRANSFER_COMPLETE payload, returns transfer_id.

    Raises:
        IntegrityError: If the payload is malformed.
    """
    try:
        transfer_id, _ = decode_string(payload, 0)
        return transfer_id
    except WireError as e:
        raise IntegrityError(str(e)) from e


def verify_file_integrity(
    data: bytes,
    metadata: FileMetadata,
) -> bool:
    """Verify a complete file's SHA-256 against the metadata.

    Args:
        data:     The reassembled file contents.
        metadata: The file metadata containing the expected sha256.

    Returns:
        True if the hash matches, or if no sha256 was set on metadata.

    Raises:
        IntegrityError: If sha256 is set and does not match.
    """
    if metadata.sha256 is None:
        return True

    hasher = file_hash_incremental()
    hasher.update(data)
    actual = hasher.hexdigest()

    if actual != metadata.sha256:
        raise IntegrityError(
            f"File '{metadata.name}' hash mismatch: "
            f"expected {metadata.sha256[:16]}…, got {actual[:16]}…"
        )
    return True


def create_file_hasher() -> _IncrementalHasher:
    """Create an incremental hasher for streaming whole-file verification.

    Useful when chunks arrive in order and we want to compute the hash
    without buffering the entire file.
    """
    return file_hash_incremental()
