"""Sliding-window chunk sender for the NearShare transfer engine.

Limits the number of in-flight (unacknowledged) chunks per the
DEFAULT_SEND_WINDOW constant from protocol.md §4.  The sender must
wait for ACKs before sending more chunks when the window is full.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from engine.types import Chunk, DEFAULT_SEND_WINDOW


class SendWindow:
    """Tracks in-flight chunks and enforces the window cap.

    The window tracks which chunk sequence numbers have been sent but
    not yet acknowledged.  When the window is full, callers must wait
    for acks before sending more.

    Attributes:
        capacity:   Maximum number of in-flight chunks.
        in_flight:  Set of sequence numbers currently in flight.
    """

    def __init__(self, capacity: int = DEFAULT_SEND_WINDOW) -> None:
        self._capacity = capacity
        self._in_flight: set[int] = set()
        self._space_available = asyncio.Event()
        self._space_available.set()  # initially there's space

    @property
    def capacity(self) -> int:
        return self._capacity

    @property
    def in_flight(self) -> set[int]:
        """Copy of the in-flight sequence numbers."""
        return set(self._in_flight)

    @property
    def size(self) -> int:
        """Number of chunks currently in flight."""
        return len(self._in_flight)

    @property
    def is_full(self) -> bool:
        return len(self._in_flight) >= self._capacity

    @property
    def available(self) -> int:
        """Number of additional chunks that can be sent."""
        return max(0, self._capacity - len(self._in_flight))

    def mark_sent(self, seq: int) -> None:
        """Mark a chunk as sent (in-flight).

        Args:
            seq: The sequence number of the sent chunk.

        Raises:
            ValueError: If the window is already full.
        """
        if self.is_full:
            raise ValueError(
                f"Window full ({self._capacity}): cannot send seq {seq}"
            )
        self._in_flight.add(seq)
        if self.is_full:
            self._space_available.clear()

    def ack(self, seq: int) -> None:
        """Acknowledge a chunk, removing it from the in-flight set.

        Args:
            seq: The acknowledged sequence number. If not in flight,
                 this is a no-op (idempotent).
        """
        self._in_flight.discard(seq)
        if not self.is_full:
            self._space_available.set()

    def ack_many(self, seqs: Sequence[int]) -> None:
        """Acknowledge multiple chunks at once."""
        for s in seqs:
            self._in_flight.discard(s)
        if not self.is_full:
            self._space_available.set()

    async def wait_for_space(self) -> None:
        """Block until at least one slot is available in the window."""
        await self._space_available.wait()

    def reset(self) -> None:
        """Clear all in-flight state (e.g. on session reset)."""
        self._in_flight.clear()
        self._space_available.set()
