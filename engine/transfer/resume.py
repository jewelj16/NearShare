"""Resume-on-reconnect logic for interrupted transfers.

After an interruption, the receiver can report its ACK bitmap to the
sender so that only unacknowledged chunks need to be retransmitted.

See protocol.md §4 (Resumption) and engine/types.py RESUME_TIMEOUT_S.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from engine.types import FileMetadata, RESUME_TIMEOUT_S


class ResumeExpiredError(Exception):
    """Raised when the resume window has expired."""


@dataclass
class ResumeToken:
    """Snapshot of transfer progress that enables resumption.

    Created when a transfer is interrupted and stored until the resume
    timeout expires.

    Attributes:
        transfer_id:  The session ID.
        files:        The file list from the original session.
        ack_bitmaps:  Per-file set of acknowledged chunk sequence numbers.
        created_at:   Monotonic timestamp when the token was created.
        timeout_s:    How long the token remains valid.
    """

    transfer_id: str
    files: list[FileMetadata]
    ack_bitmaps: dict[int, set[int]]
    created_at: float = field(default_factory=time.monotonic)
    timeout_s: float = RESUME_TIMEOUT_S

    @property
    def is_expired(self) -> bool:
        return (time.monotonic() - self.created_at) > self.timeout_s

    def chunks_to_send(self, file_index: int) -> set[int]:
        """Return chunk seqs that still need to be sent for a file.

        Args:
            file_index: Zero-based index into the files list.

        Returns:
            Set of sequence numbers NOT yet acknowledged.

        Raises:
            IndexError: If file_index is out of range.
        """
        if file_index < 0 or file_index >= len(self.files):
            raise IndexError(f"file_index {file_index} out of range")
        expected = set(range(self.files[file_index].chunk_count))
        acked = self.ack_bitmaps.get(file_index, set())
        return expected - acked

    def total_remaining(self) -> int:
        """Total number of chunks still needed across all files."""
        total = 0
        for i in range(len(self.files)):
            total += len(self.chunks_to_send(i))
        return total


class ResumeManager:
    """Stores and retrieves ResumeTokens for interrupted transfers.

    Tokens are keyed by transfer_id and expire after RESUME_TIMEOUT_S.

    Attributes:
        tokens: Active resume tokens indexed by transfer_id.
    """

    def __init__(self) -> None:
        self._tokens: dict[str, ResumeToken] = {}

    def save(self, token: ResumeToken) -> None:
        """Store a resume token."""
        self._tokens[token.transfer_id] = token

    def get(self, transfer_id: str) -> ResumeToken | None:
        """Retrieve a resume token if it exists and hasn't expired.

        Expired tokens are removed automatically.

        Returns:
            The ResumeToken, or None if not found or expired.
        """
        token = self._tokens.get(transfer_id)
        if token is None:
            return None
        if token.is_expired:
            del self._tokens[transfer_id]
            return None
        return token

    def remove(self, transfer_id: str) -> None:
        """Remove a resume token (e.g. after successful resumption)."""
        self._tokens.pop(transfer_id, None)

    def cleanup_expired(self) -> int:
        """Remove all expired tokens and return the count removed."""
        expired = [
            tid for tid, tok in self._tokens.items()
            if tok.is_expired
        ]
        for tid in expired:
            del self._tokens[tid]
        return len(expired)

    @property
    def active_count(self) -> int:
        """Number of non-expired tokens currently stored."""
        return sum(1 for t in self._tokens.values() if not t.is_expired)
