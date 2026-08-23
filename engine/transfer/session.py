"""TransferSession — tracks the state of a single file transfer."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field

from engine.types import (
    DEFAULT_CHUNK_SIZE,
    FileMetadata,
    TransferDirection,
    TransferState,
)


@dataclass
class TransferSession:
    """Tracks the complete state of a single transfer (one or more files).

    The ``ack_bitmaps`` dict maps each file index to the set of chunk
    sequence numbers that have been acknowledged.  Call
    :meth:`init_ack_bitmaps` after construction to initialise the maps,
    or let :meth:`ack_chunk` lazily create them.

    Attributes:
        session_id:  Unique identifier for this transfer session.
        direction:   Whether this device is sending or receiving.
        files:       Ordered list of files in this transfer.
        state:       Current state machine state.
        ack_bitmaps: Per-file set of acknowledged chunk sequence numbers.
    """

    session_id: str
    direction: TransferDirection
    files: list[FileMetadata]
    state: TransferState = TransferState.IDLE
    ack_bitmaps: dict[int, set[int]] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    # factories

    @staticmethod
    def create(
        direction: TransferDirection,
        files: list[FileMetadata],
    ) -> TransferSession:
        """Create a new TransferSession with a generated session_id."""
        return TransferSession(
            session_id=str(uuid.uuid4()),
            direction=direction,
            files=files,
        )

    # ack bookkeeping 

    def init_ack_bitmaps(self) -> None:
        """Initialise empty ack bitmaps for every file in the transfer."""
        self.ack_bitmaps = {i: set() for i in range(len(self.files))}

    def ack_chunk(self, file_index: int, seq: int) -> None:
        """Record that chunk *seq* of *file_index* has been acknowledged.

        This is the synchronous version — safe for single-threaded use.
        For concurrent asyncio tasks, use :meth:`ack_chunk_safe` instead.
        """
        if file_index not in self.ack_bitmaps:
            self.ack_bitmaps[file_index] = set()
        self.ack_bitmaps[file_index].add(seq)

    async def ack_chunk_safe(self, file_index: int, seq: int) -> None:
        """Record a chunk ACK under the session lock.

        Use this instead of :meth:`ack_chunk` when multiple asyncio tasks
        may be acknowledging chunks concurrently.  The asyncio.Lock
        serialises access to the bitmap dict.
        """
        async with self._lock:
            self.ack_chunk(file_index, seq)

    def is_file_complete(self, file_index: int) -> bool:
        """Return True if all chunks for *file_index* have been acked."""
        if file_index < 0 or file_index >= len(self.files):
            raise IndexError(f"file_index {file_index} out of range")
        expected = self.files[file_index].chunk_count
        acked = self.ack_bitmaps.get(file_index, set())
        return len(acked) >= expected

    def is_transfer_complete(self) -> bool:
        """Return True if every file in the transfer is complete."""
        return all(self.is_file_complete(i) for i in range(len(self.files)))

    def missing_chunks(self, file_index: int) -> set[int]:
        """Return the set of chunk seqs not yet acknowledged for *file_index*."""
        if file_index < 0 or file_index >= len(self.files):
            raise IndexError(f"file_index {file_index} out of range")
        expected = set(range(self.files[file_index].chunk_count))
        acked = self.ack_bitmaps.get(file_index, set())
        return expected - acked

    def get_file_bytes_acked(self, file_index: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> int:
        """Calculate the total bytes acked for this file so far."""
        if file_index < 0 or file_index >= len(self.files):
            return 0
        meta = self.files[file_index]
        acked_chunks = len(self.ack_bitmaps.get(file_index, set()))
        if acked_chunks == meta.chunk_count:
            return meta.size
        return acked_chunks * chunk_size
