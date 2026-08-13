"""Interface for the NearShare file-storage layer.

The FileStore abstracts all filesystem I/O away from the transfer engine.
This allows the engine to remain pure-Python with no direct dependency on
the host OS path API, and makes it straightforward to swap in an in-memory
implementation for unit tests.

This is a structural Protocol class (PEP 544); concrete implementations
need not inherit from it explicitly.
"""

from __future__ import annotations

from typing import AsyncIterator, Protocol, runtime_checkable

from engine.types import FileMetadata


@runtime_checkable
class FileStore(Protocol):
    """Abstract file-system operations needed by the transfer engine.

    All read/write methods are coroutines so that the asyncio event loop
    is not blocked by disk I/O.  Concrete implementations are expected to
    run blocking calls in a thread-pool executor.
    """

    async def open_for_read(self, metadata: FileMetadata) -> AsyncIterator[bytes]:
        """Open a local file and yield its chunks sequentially.

        The yielded chunk size MUST match ``metadata.chunk_size``
        for all chunks except the final one, which may be smaller.

        Args:
            metadata: Description of the file to read, including the
                      negotiated ``chunk_size``.

        Yields:
            Raw bytes for each chunk, in order.

        Raises:
            FileNotFoundError: If the file does not exist.
            OSError:           On other I/O errors.
        """
        ...

    async def open_for_write(self, metadata: FileMetadata) -> None:
        """Prepare the store to receive chunks for *metadata*.

        Creates any necessary directory structure and a temporary write
        target.  Must be called before :meth:`write_chunk`.

        Args:
            metadata: Description of the incoming file.

        Raises:
            OSError: If the destination cannot be created (e.g. no space).
        """
        ...

    async def write_chunk(
        self, metadata: FileMetadata, seq: int, data: bytes
    ) -> None:
        """Write a single chunk at the correct offset within the file.

        Chunks may arrive out of order; implementations must handle sparse
        writes or buffer them until the file can be assembled in order.

        Args:
            metadata: The file this chunk belongs to.
            seq:      Zero-based sequence number of the chunk.
            data:     Raw bytes of this chunk.

        Raises:
            OSError: On write failure.
        """
        ...

    async def finalise(self, metadata: FileMetadata) -> None:
        """Complete the write, verify integrity, and make the file visible.

        This is called after all chunks have been written.  The
        implementation MUST verify the whole-file SHA-256 against
        ``metadata.sha256`` (when set) before renaming the temporary file
        into place.

        Args:
            metadata: The file to finalise.

        Raises:
            ValueError: If the SHA-256 does not match.
            OSError:    On filesystem errors during finalisation.
        """
        ...

    async def discard(self, metadata: FileMetadata) -> None:
        """Discard an in-progress write (e.g. after CANCEL or ERROR).

        Removes any partial data written by :meth:`open_for_write` /
        :meth:`write_chunk`.  Must be safe to call even if
        :meth:`open_for_write` was never called (no-op in that case).

        Args:
            metadata: The file whose partial data should be removed.
        """
        ...
