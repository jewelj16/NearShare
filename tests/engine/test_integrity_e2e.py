"""End-to-end integrity tests — full session with hash verification."""

import hashlib

import pytest

from engine.integrity.hasher import chunk_hash, file_hash, file_hash_incremental
from engine.protocol.complete import (
    IntegrityError,
    build_transfer_complete_payload,
    create_file_hasher,
    parse_transfer_complete_payload,
    verify_file_integrity,
)
from engine.transfer.chunker import split
from engine.types import DEFAULT_CHUNK_SIZE, FileMetadata


# Helpers

def _make_file_data(size: int = DEFAULT_CHUNK_SIZE * 3) -> bytes:
    """Generate deterministic test data."""
    return bytes(i % 256 for i in range(size))


def _metadata_for(data: bytes, name: str = "test.bin") -> FileMetadata:
    sha = hashlib.sha256(data).hexdigest()
    return FileMetadata(name=name, size=len(data), sha256=sha)


# TRANSFER_COMPLETE payload

class TestTransferCompletePayload:
    """Round-trip encode/decode for TRANSFER_COMPLETE."""

    def test_round_trip(self) -> None:
        tid = "550e8400-e29b-41d4-a716-446655440000"
        payload = build_transfer_complete_payload(tid)
        assert parse_transfer_complete_payload(payload) == tid

    def test_malformed_raises(self) -> None:
        with pytest.raises(IntegrityError):
            parse_transfer_complete_payload(b"")


# Whole-file verification

class TestVerifyFileIntegrity:
    """verify_file_integrity against expected SHA-256."""

    def test_valid_file(self) -> None:
        data = _make_file_data()
        meta = _metadata_for(data)
        assert verify_file_integrity(data, meta) is True

    def test_corrupt_file_raises(self) -> None:
        data = _make_file_data()
        meta = _metadata_for(data)
        corrupt = data[:-1] + bytes([data[-1] ^ 0xFF])  # flip last byte
        with pytest.raises(IntegrityError, match="hash mismatch"):
            verify_file_integrity(corrupt, meta)

    def test_no_sha256_always_passes(self) -> None:
        data = _make_file_data()
        meta = FileMetadata(name="no_hash.bin", size=len(data))
        assert verify_file_integrity(data, meta) is True

    def test_empty_file(self) -> None:
        data = b""
        meta = _metadata_for(data)
        assert verify_file_integrity(data, meta) is True


# Full session: chunk → reassemble → verify

class TestFullSessionIntegrity:
    """Simulate a complete transfer session with hash verification."""

    async def test_chunk_reassemble_verify(self) -> None:
        """Split, reassemble in order, and verify whole-file hash."""
        data = _make_file_data(DEFAULT_CHUNK_SIZE * 4)
        meta = _metadata_for(data)
        tid = "test-session-1"

        # split into chunks
        chunks = [c async for c in split(data, meta, tid)]
        assert len(chunks) == 4

        # verify each chunk hash
        for c in chunks:
            assert chunk_hash(c.data) == c.sha256

        # reassemble
        reassembled = b"".join(c.data for c in chunks)
        assert reassembled == data

        # whole-file verify
        assert verify_file_integrity(reassembled, meta) is True

    async def test_incremental_hasher_matches(self) -> None:
        """Incremental hasher should produce the same result as full hash."""
        data = _make_file_data(DEFAULT_CHUNK_SIZE * 3)
        meta = _metadata_for(data)

        hasher = create_file_hasher()
        chunks = [c async for c in split(data, meta, "sess")]
        for c in chunks:
            hasher.update(c.data)

        assert hasher.hexdigest() == meta.sha256
        assert hasher.verify(meta.sha256)

    async def test_out_of_order_incremental_differs(self) -> None:
        """Feeding chunks out of order produces a different hash."""
        # Use distinct per-chunk content so reversal is detectable.
        chunk_a = b"A" * DEFAULT_CHUNK_SIZE
        chunk_b = b"B" * DEFAULT_CHUNK_SIZE
        chunk_c = b"C" * DEFAULT_CHUNK_SIZE
        data = chunk_a + chunk_b + chunk_c
        meta = _metadata_for(data)

        chunks = [c async for c in split(data, meta, "sess")]
        hasher = create_file_hasher()
        # feed in reverse order (C, B, A instead of A, B, C)
        for c in reversed(chunks):
            hasher.update(c.data)

        # hash must differ because the byte sequence changed
        assert hasher.hexdigest() != meta.sha256
