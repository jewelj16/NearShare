"""SHA-256 hashing utilities for the NearShare transfer engine.

Provides helpers for computing per-chunk and whole-file hashes used
throughout the protocol for integrity verification (see protocol.md §3.6
and §3.9).
"""

from __future__ import annotations

import hashlib


def chunk_hash(data: bytes) -> str:
    """Compute the hex-encoded SHA-256 of a single chunk's data.

    Args:
        data: Raw chunk bytes.

    Returns:
        64-character lowercase hex string.
    """
    return hashlib.sha256(data).hexdigest()


def file_hash(data: bytes) -> str:
    """Compute the hex-encoded SHA-256 of a complete file.

    Args:
        data: The entire file contents.

    Returns:
        64-character lowercase hex string.
    """
    return hashlib.sha256(data).hexdigest()


def file_hash_incremental() -> _IncrementalHasher:
    """Create an incremental hasher for streaming whole-file hash computation.

    Useful when the file is being reassembled chunk by chunk and we want
    to compute the whole-file hash without buffering the entire file.

    Returns:
        An _IncrementalHasher instance.
    """
    return _IncrementalHasher()


class _IncrementalHasher:
    """Feeds data in order and produces a final whole-file SHA-256.

    Chunks must be fed in sequential order (seq 0, 1, 2, ...) for the
    resulting hash to match the whole-file hash.
    """

    def __init__(self) -> None:
        self._hasher = hashlib.sha256()
        self._bytes_fed: int = 0

    def update(self, data: bytes) -> None:
        """Feed the next sequential chunk of data.

        Args:
            data: Raw bytes of the next chunk in order.
        """
        self._hasher.update(data)
        self._bytes_fed += len(data)

    @property
    def bytes_fed(self) -> int:
        """Total number of bytes fed so far."""
        return self._bytes_fed

    def hexdigest(self) -> str:
        """Return the hex-encoded SHA-256 of all data fed so far.

        Returns:
            64-character lowercase hex string.
        """
        return self._hasher.hexdigest()

    def verify(self, expected: str) -> bool:
        """Check whether the current digest matches an expected hash.

        Args:
            expected: Hex-encoded SHA-256 to compare against.

        Returns:
            True if the hashes match.
        """
        return self.hexdigest() == expected
