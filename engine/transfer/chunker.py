"""Streaming file chunker for the NearShare transfer engine.

Splits raw file bytes into Chunk objects suitable for sending over the
wire.  Works as a generator so the entire file never needs to be held
in memory at once.
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
