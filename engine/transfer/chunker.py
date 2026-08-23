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
import asyncio
from collections.abc import AsyncGenerator
from pathlib import Path

from engine.types import Chunk, FileMetadata

def _hash_piece(piece: bytes) -> str:
    return hashlib.sha256(piece).hexdigest()

async def split(
    data: memoryview,
    metadata: FileMetadata,
    transfer_id: str,
    file_index: int = 0,
) -> AsyncGenerator[Chunk, None]:
    """Split file data into Chunk objects according to metadata.chunk_size.

    Yields one Chunk per slice, computing the per-chunk SHA-256 on the fly
    in a background thread.
    The last chunk may be smaller than chunk_size if the file size is not
    an exact multiple.

    Args:
        data:        A memoryview or mmap object containing the file contents.
        metadata:    FileMetadata describing the file (uses chunk_size).
        transfer_id: Session ID to stamp on every chunk.
        file_index:  Zero-based index of this file in the transfer.

    Yields:
        Chunk objects in order from seq=0 to seq=chunk_count-1.
    """
    chunk_size = metadata.chunk_size
    offset = 0
    seq = 0
    loop = asyncio.get_running_loop()

    while offset < len(data):
        end = min(offset + chunk_size, len(data))
        piece = data[offset:end]
        
        # Offload hashing to ThreadPoolExecutor
        sha = await loop.run_in_executor(None, _hash_piece, piece)

        yield Chunk(
            transfer_id=transfer_id,
            file_index=file_index,
            seq=seq,
            data=piece if isinstance(piece, bytes) else bytes(piece),
            sha256=sha,
        )

        offset = end
        seq += 1


class ChunkAssembler:
    """Reassembles a file from chunks arriving in any order.

    Uses MemoryMappedFile to write chunks directly to disk via virtual memory.
    Duplicate writes for the same sequence number are silently ignored.

    Attributes:
        metadata:  The FileMetadata describing the file being reassembled.
        path:      The destination path on disk.
        received:  Set of sequence numbers that have been successfully written.
    """

    def __init__(self, metadata: FileMetadata, path: Path) -> None:
        from engine.storage.file_io import MemoryMappedFile
        self.metadata = metadata
        self.path = path
        self.received: set[int] = set()
        self._mmap_ctx = MemoryMappedFile(path, write=True, size=metadata.size)
        self.buffer = self._mmap_ctx.__enter__()

    async def write_chunk(self, chunk: Chunk) -> None:
        """Write a chunk's data at the correct byte offset.

        Validates the chunk's SHA-256 hash before writing in a background thread.
        Duplicate writes (same seq already received) are silently skipped.

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

        # verify per-chunk hash in a background thread
        loop = asyncio.get_running_loop()
        actual_sha = await loop.run_in_executor(None, _hash_piece, chunk.data)
        
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

    def close(self) -> None:
        """Flush and close the memory-mapped file."""
        if self.is_complete and isinstance(self.buffer, memoryview):
            # Not strictly necessary to flush, but good practice
            pass
        self._mmap_ctx.__exit__(None, None, None)

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
