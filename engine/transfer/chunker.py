"""Streaming file chunker and reassembler for the NearShare transfer engine.

Splits raw file bytes into Chunk objects suitable for sending over the
wire.  Works as a generator so the entire file never needs to be held
in memory at once.

Also provides ChunkAssembler for the receiving side — chunks can arrive
in any order and are placed at the correct byte offset via seek-based
writes into a pre-allocated buffer.
"""

from __future__ import annotations

import hashlib
from collections.abc import Generator

from engine.types import Chunk, FileMetadata


def split(
    data: bytes,
    metadata: FileMetadata,
    transfer_id: str,
    file_index: int = 0,
) -> Generator[Chunk, None, None]:
    """Split file data into Chunk objects according to metadata.chunk_size.

    Yields one Chunk per slice, computing the per-chunk SHA-256 on the fly.
    The last chunk may be smaller than chunk_size if the file size is not
    an exact multiple.

    Args:
        data:        The complete file contents as bytes.
        metadata:    FileMetadata describing the file (uses chunk_size).
        transfer_id: Session ID to stamp on every chunk.
        file_index:  Zero-based index of this file in the transfer.

    Yields:
        Chunk objects in order from seq=0 to seq=chunk_count-1.
    """
    chunk_size = metadata.chunk_size
    offset = 0
    seq = 0

    while offset < len(data):
        end = min(offset + chunk_size, len(data))
        piece = data[offset:end]
        sha = hashlib.sha256(piece).hexdigest()

        yield Chunk(
            transfer_id=transfer_id,
            file_index=file_index,
            seq=seq,
            data=piece,
            sha256=sha,
        )

        offset = end
        seq += 1


class ChunkAssembler:
    """Reassembles a file from chunks arriving in any order.

    Pre-allocates a bytearray of the expected file size and writes each
    chunk at its correct byte offset (seek-based).  Duplicate writes for
    the same sequence number are silently ignored.

    Attributes:
        metadata:  The FileMetadata describing the file being reassembled.
        buffer:    The pre-allocated bytearray that chunks are written into.
        received:  Set of sequence numbers that have been successfully written.
    """

    def __init__(self, metadata: FileMetadata) -> None:
        self.metadata = metadata
        self.buffer = bytearray(metadata.size)
        self.received: set[int] = set()

    def write_chunk(self, chunk: Chunk) -> None:
        """Write a chunk's data at the correct byte offset.

        Validates the chunk's SHA-256 hash before writing.  Duplicate
        writes (same seq already received) are silently skipped.

        Args:
            chunk: The chunk to write.

        Raises:
            ValueError: If the chunk's SHA-256 doesn't match its data,
                        or if the sequence number is out of range.
        """
        if chunk.seq in self.received:
            return  # idempotent

        if chunk.seq < 0 or chunk.seq >= self.metadata.chunk_count:
            raise ValueError(
                f"Sequence number {chunk.seq} out of range "
                f"[0, {self.metadata.chunk_count})"
            )

        # verify per-chunk hash
        actual_sha = hashlib.sha256(chunk.data).hexdigest()
        if actual_sha != chunk.sha256:
            raise ValueError(
                f"Chunk {chunk.seq} hash mismatch: "
                f"expected {chunk.sha256}, got {actual_sha}"
            )

        # seek-based write: place data at the correct offset
        offset = chunk.seq * self.metadata.chunk_size
        end = offset + len(chunk.data)
        self.buffer[offset:end] = chunk.data
        self.received.add(chunk.seq)

    @property
    def is_complete(self) -> bool:
        """True when all expected chunks have been received."""
        return len(self.received) == self.metadata.chunk_count

    @property
    def missing_seqs(self) -> set[int]:
        """Return the set of sequence numbers not yet received."""
        return set(range(self.metadata.chunk_count)) - self.received

    @property
    def progress(self) -> float:
        """Return reassembly progress as a fraction [0.0, 1.0]."""
        if self.metadata.chunk_count == 0:
            return 1.0
        return len(self.received) / self.metadata.chunk_count

    def to_bytes(self) -> bytes:
        """Return the reassembled file as an immutable bytes object.

        Raises:
            RuntimeError: If not all chunks have been received yet.
        """
        if not self.is_complete:
            raise RuntimeError(
                f"Reassembly incomplete: "
                f"received {len(self.received)}/{self.metadata.chunk_count} chunks"
            )
        return bytes(self.buffer)

