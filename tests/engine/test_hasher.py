"""Tests for engine.integrity.hasher — chunk and whole-file hashes."""

import hashlib

import pytest

from engine.integrity.hasher import (
    _IncrementalHasher,
    chunk_hash,
    file_hash,
    file_hash_incremental,
)


# chunk_hash

class TestChunkHash:
    """Per-chunk SHA-256 computation."""

    def test_known_value(self) -> None:
        data = b"hello"
        assert chunk_hash(data) == hashlib.sha256(b"hello").hexdigest()

    def test_empty_data(self) -> None:
        assert chunk_hash(b"") == hashlib.sha256(b"").hexdigest()

    def test_returns_64_hex_chars(self) -> None:
        result = chunk_hash(b"test data")
        assert len(result) == 64
        assert all(c in "0123456789abcdef" for c in result)

    def test_different_data_different_hash(self) -> None:
        assert chunk_hash(b"aaa") != chunk_hash(b"bbb")

    def test_same_data_same_hash(self) -> None:
        data = b"consistent"
        assert chunk_hash(data) == chunk_hash(data)


# file_hash

class TestFileHash:
    """Whole-file SHA-256 computation."""

    def test_known_value(self) -> None:
        data = b"the quick brown fox"
        assert file_hash(data) == hashlib.sha256(data).hexdigest()

    def test_empty_file(self) -> None:
        assert file_hash(b"") == hashlib.sha256(b"").hexdigest()

    def test_large_data(self) -> None:
        data = bytes(range(256)) * 1000  # 256 KB
        assert file_hash(data) == hashlib.sha256(data).hexdigest()

    def test_chunk_hash_equals_file_hash_for_single_chunk(self) -> None:
        """When a file is a single chunk, both functions give the same result."""
        data = b"single chunk file"
        assert chunk_hash(data) == file_hash(data)


# incremental hasher

class TestIncrementalHasher:
    """Streaming whole-file hash via sequential chunk feeding."""

    def test_single_update_matches_file_hash(self) -> None:
        data = b"all at once"
        h = file_hash_incremental()
        h.update(data)
        assert h.hexdigest() == file_hash(data)

    def test_multiple_updates_match_file_hash(self) -> None:
        """Feeding chunks in order should produce the same hash as the whole file."""
        part_a = b"first part "
        part_b = b"second part "
        part_c = b"third part"
        whole = part_a + part_b + part_c

        h = file_hash_incremental()
        h.update(part_a)
        h.update(part_b)
        h.update(part_c)
        assert h.hexdigest() == file_hash(whole)

    def test_bytes_fed_tracking(self) -> None:
        h = file_hash_incremental()
        assert h.bytes_fed == 0
        h.update(b"12345")
        assert h.bytes_fed == 5
        h.update(b"67890")
        assert h.bytes_fed == 10

    def test_verify_correct(self) -> None:
        data = b"verify me"
        h = file_hash_incremental()
        h.update(data)
        expected = hashlib.sha256(data).hexdigest()
        assert h.verify(expected) is True

    def test_verify_incorrect(self) -> None:
        h = file_hash_incremental()
        h.update(b"some data")
        assert h.verify("0" * 64) is False

    def test_empty_hasher(self) -> None:
        h = file_hash_incremental()
        assert h.hexdigest() == hashlib.sha256(b"").hexdigest()
        assert h.bytes_fed == 0

    def test_simulated_chunked_file(self) -> None:
        """Simulate reading a file in 1KB chunks and hashing incrementally."""
        file_data = bytes(range(256)) * 16  # 4 KB
        chunk_size = 1024
        h = file_hash_incremental()

        offset = 0
        while offset < len(file_data):
            piece = file_data[offset:offset + chunk_size]
            h.update(piece)
            offset += chunk_size

        assert h.hexdigest() == file_hash(file_data)
        assert h.bytes_fed == len(file_data)
