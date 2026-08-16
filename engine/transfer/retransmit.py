"""Missing-chunk retransmission and corrupted-chunk detection.

Provides the retransmission loop that uses the ACK bitmap from a
TransferSession to identify missing chunks and re-send them, with
exponential backoff via RetryPolicy.  Also detects per-chunk hash
mismatches and triggers targeted retransmission.

See docs/architecture/protocol.md §3.6 (CHUNK) and §3.7 (ACK).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from engine.integrity.hasher import chunk_hash
from engine.transfer.retry import RetryPolicy, RetryExhaustedError
from engine.types import Chunk, FileMetadata


class RetransmitError(Exception):
    """Raised when retransmission ultimately fails."""


class CorruptChunkError(Exception):
    """Raised when a received chunk's SHA-256 does not match its data.

    Attributes:
        file_index: Index of the file the chunk belongs to.
        seq:        Sequence number of the corrupted chunk.
        expected:   The hash declared in the Chunk header.
        actual:     The hash computed from the received data.
    """

    def __init__(
        self,
        file_index: int,
        seq: int,
        expected: str,
        actual: str,
    ) -> None:
        self.file_index = file_index
        self.seq = seq
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Chunk {file_index}:{seq} hash mismatch: "
            f"expected {expected[:16]}…, got {actual[:16]}…"
        )


def verify_chunk(chunk: Chunk) -> bool:
    """Verify a chunk's per-chunk SHA-256.

    Args:
        chunk: The received chunk.

    Returns:
        True if the hash matches.
    """
    return chunk_hash(chunk.data) == chunk.sha256


def detect_corrupt_chunk(chunk: Chunk) -> CorruptChunkError | None:
    """Check a chunk for corruption, returning an error if found.

    Args:
        chunk: The chunk to verify.

    Returns:
        A CorruptChunkError if corruption is detected, else None.
    """
    actual = chunk_hash(chunk.data)
    if actual != chunk.sha256:
        return CorruptChunkError(
            file_index=chunk.file_index,
            seq=chunk.seq,
            expected=chunk.sha256,
            actual=actual,
        )
    return None


@dataclass
class RetransmitRequest:
    """A request to retransmit specific chunks for a file.

    Attributes:
        file_index:   Index of the file needing retransmission.
        missing_seqs: Set of chunk sequence numbers to retransmit.
    """

    file_index: int
    missing_seqs: set[int]


def compute_retransmit_requests(
    files: list[FileMetadata],
    ack_bitmaps: dict[int, set[int]],
) -> list[RetransmitRequest]:
    """Compare expected chunks against ACK bitmaps and return retransmit requests.

    Args:
        files:       The list of files in the transfer.
        ack_bitmaps: Per-file set of acknowledged chunk sequence numbers.

    Returns:
        A list of RetransmitRequest for files with missing chunks.
        Files that are complete are omitted.
    """
    requests: list[RetransmitRequest] = []
    for i, f in enumerate(files):
        expected = set(range(f.chunk_count))
        acked = ack_bitmaps.get(i, set())
        missing = expected - acked
        if missing:
            requests.append(RetransmitRequest(file_index=i, missing_seqs=missing))
    return requests


class RetransmitController:
    """Coordinates retransmission attempts with backoff.

    Tracks per-file retry state and raises RetransmitError when
    retries are exhausted.

    Attributes:
        files:   The file list from the transfer session.
        policy:  The retry policy controlling backoff and max attempts.
    """

    def __init__(
        self,
        files: list[FileMetadata],
        policy: RetryPolicy | None = None,
    ) -> None:
        self._files = files
        self._policy = policy or RetryPolicy()
        self._attempts: dict[int, int] = {}  # file_index -> attempt count

    @property
    def policy(self) -> RetryPolicy:
        return self._policy

    def request_retransmit(
        self,
        ack_bitmaps: dict[int, set[int]],
    ) -> list[RetransmitRequest]:
        """Compute what needs retransmitting and advance the retry counter.

        Returns:
            The list of retransmit requests.

        Raises:
            RetransmitError: If the retry policy is exhausted.
        """
        if self._policy.exhausted:
            raise RetransmitError(
                f"Retransmission failed after {self._policy.max_attempts} attempts"
            )
        requests = compute_retransmit_requests(self._files, ack_bitmaps)
        if requests:
            self._policy.next_delay()  # advance attempt counter
        return requests

    def mark_success(self) -> None:
        """Reset the retry counter (e.g. when new ACKs arrive)."""
        self._policy.reset()

    @property
    def exhausted(self) -> bool:
        return self._policy.exhausted
