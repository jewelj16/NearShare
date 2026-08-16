"""CANCEL message handling and partial-file cleanup.

Implements CANCEL (0x08) payload encoding/decoding and the cleanup
logic that runs when a transfer is cancelled by either party.

See protocol.md §3.8 (CANCEL).
"""

from __future__ import annotations

import struct

from engine.protocol.wire import encode_string, decode_string, WireError
from engine.types import FileMetadata, TransferState


class CancelError(Exception):
    """Raised when CANCEL handling fails."""


def build_cancel_payload(transfer_id: str, reason_msg: str = "") -> bytes:
    """Build a CANCEL (0x08) payload.

    Layout: [string transfer_id][string reason_msg]
    """
    return encode_string(transfer_id) + encode_string(reason_msg)


def parse_cancel_payload(payload: bytes) -> tuple[str, str]:
    """Parse a CANCEL payload into (transfer_id, reason_msg).

    Raises:
        CancelError: If the payload is malformed.
    """
    try:
        offset = 0
        transfer_id, offset = decode_string(payload, offset)
        reason_msg, offset = decode_string(payload, offset)
        return transfer_id, reason_msg
    except WireError as e:
        raise CancelError(str(e)) from e


class CancelHandler:
    """Manages the cancellation workflow for a transfer session.

    Tracks which files have partial data that needs to be cleaned up
    on cancellation.

    Attributes:
        transfer_id:    The session being managed.
        cancelled:      Whether cancellation has been triggered.
        cleanup_done:   Whether cleanup has completed.
        partial_files:  List of files that had partial data written.
    """

    def __init__(self, transfer_id: str, files: list[FileMetadata]) -> None:
        self._transfer_id = transfer_id
        self._files = list(files)
        self._cancelled = False
        self._cleanup_done = False
        self._partial_files: list[FileMetadata] = []

    @property
    def transfer_id(self) -> str:
        return self._transfer_id

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    @property
    def cleanup_done(self) -> bool:
        return self._cleanup_done

    @property
    def partial_files(self) -> list[FileMetadata]:
        return list(self._partial_files)

    def cancel(self, ack_bitmaps: dict[int, set[int]] | None = None) -> list[FileMetadata]:
        """Mark the transfer as cancelled and identify partial files.

        Args:
            ack_bitmaps: Current ACK state. Files with any received
                         chunks but not fully complete are considered partial.

        Returns:
            List of FileMetadata for files that have partial data.
        """
        self._cancelled = True
        self._partial_files = []

        if ack_bitmaps is None:
            return self._partial_files

        for i, f in enumerate(self._files):
            acked = ack_bitmaps.get(i, set())
            if acked and len(acked) < f.chunk_count:
                self._partial_files.append(f)

        return self._partial_files

    def mark_cleanup_done(self) -> None:
        """Mark that partial file cleanup has been completed."""
        self._cleanup_done = True
