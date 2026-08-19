"""Tests for engine.transfer.chunker — streaming split with edge cases."""

import hashlib

import pytest

from engine.transfer.chunker import split
from engine.types import Chunk, DEFAULT_CHUNK_SIZE, FileMetadata


# helpers

def _meta(size: int, chunk_size: int = DEFAULT_CHUNK_SIZE) -> FileMetadata:
    """Build a FileMetadata with a given size and chunk_size."""
    return FileMetadata(name="test.bin", size=size, chunk_size=chunk_size)


# basic splitting

class TestSplitBasic:
    """Core round-trip and ordering checks."""

    async def test_single_full_chunk(self) -> None:
        """A file exactly one chunk_size should produce exactly 1 chunk."""
        data = b"x" * 1024
        meta = _meta(len(data), chunk_size=1024)
        chunks = [c async for c in split(data, meta, transfer_id="sess-1")]
        assert len(chunks) == 1
        assert chunks[0].seq == 0
        assert chunks[0].data == data

    async def test_multiple_chunks(self) -> None:
        """A file larger than chunk_size splits into the right number."""
        data = b"a" * 4096
        meta = _meta(len(data), chunk_size=1024)
        chunks = [c async for c in split(data, meta, transfer_id="sess-2")]
        assert len(chunks) == 4
        for i, chunk in enumerate(chunks):
            assert chunk.seq == i

    async def test_reassembly(self) -> None:
        """Concatenating all chunk.data should reproduce the original file."""
        data = bytes(range(256)) * 10  # 2560 bytes of varied data
        meta = _meta(len(data), chunk_size=1000)
        chunks = [c async for c in split(data, meta, transfer_id="sess-3")]
        reassembled = b"".join(c.data for c in chunks)
        assert reassembled == data

    async def test_transfer_id_and_file_index(self) -> None:
        """Each chunk carries the right transfer_id and file_index."""
        data = b"hello"
        meta = _meta(len(data), chunk_size=3)
        chunks = [c async for c in split(data, meta, transfer_id="t-1", file_index=7)]
        for chunk in chunks:
            assert chunk.transfer_id == "t-1"
            assert chunk.file_index == 7

    async def test_is_generator(self) -> None:
        """split() returns an async generator."""
        data = b"x" * 100
        meta = _meta(len(data), chunk_size=10)
        result = split(data, meta, transfer_id="s")
        assert hasattr(result, "__anext__")


# 1KB edge case (single partial chunk)

class TestSplit1KBEdge:
    """The 1KB edge case from the spec — a small file that is less than
    the default chunk size, producing a single partial chunk."""

    async def test_1kb_file_single_chunk(self) -> None:
        """A 1024-byte file with default chunk_size => 1 chunk."""
        data = b"\xAB" * 1024
        meta = _meta(len(data))  # default chunk_size
        chunks = [c async for c in split(data, meta, transfer_id="1kb-test")]
        assert len(chunks) == 1
        assert chunks[0].data == data
        assert len(chunks[0].data) == 1024

    async def test_1kb_chunk_is_smaller_than_chunk_size(self) -> None:
        """The single chunk should be smaller than chunk_size (partial)."""
        data = b"\xCD" * 1024
        meta = _meta(len(data))
        chunks = [c async for c in split(data, meta, transfer_id="1kb-partial")]
        assert len(chunks[0].data) < meta.chunk_size

    async def test_1kb_sha256_is_correct(self) -> None:
        """The chunk's sha256 should match a manual computation."""
        data = b"\xEF" * 1024
        meta = _meta(len(data))
        chunks = [c async for c in split(data, meta, transfer_id="1kb-hash")]
        expected_sha = hashlib.sha256(data).hexdigest()
        assert chunks[0].sha256 == expected_sha


# last chunk size

class TestLastChunkSize:
    """The last chunk may be smaller than chunk_size."""

    async def test_last_chunk_smaller(self) -> None:
        """When file size isn't a multiple of chunk_size, the last chunk is short."""
        data = b"d" * 2500
        meta = _meta(len(data), chunk_size=1000)
        chunks = [c async for c in split(data, meta, transfer_id="last-short")]
        assert len(chunks) == 3
        assert len(chunks[0].data) == 1000
        assert len(chunks[1].data) == 1000
        assert len(chunks[2].data) == 500

    async def test_one_byte_over(self) -> None:
        """chunk_size + 1 bytes should produce 2 chunks: full + 1 byte."""
        data = b"e" * 1025
        meta = _meta(len(data), chunk_size=1024)
        chunks = [c async for c in split(data, meta, transfer_id="over")]
        assert len(chunks) == 2
        assert len(chunks[0].data) == 1024
        assert len(chunks[1].data) == 1


# SHA-256 per chunk

class TestChunkSha256:
    """Each chunk's sha256 must match its own data, not the whole file."""

    async def test_per_chunk_hash(self) -> None:
        data = b"A" * 1000 + b"B" * 1000 + b"C" * 500
        meta = _meta(len(data), chunk_size=1000)
        chunks = [c async for c in split(data, meta, transfer_id="hash-check")]

        assert chunks[0].sha256 == hashlib.sha256(b"A" * 1000).hexdigest()
        assert chunks[1].sha256 == hashlib.sha256(b"B" * 1000).hexdigest()
        assert chunks[2].sha256 == hashlib.sha256(b"C" * 500).hexdigest()

    async def test_different_data_different_hash(self) -> None:
        data = b"\x00" * 512 + b"\xFF" * 512
        meta = _meta(len(data), chunk_size=512)
        chunks = [c async for c in split(data, meta, transfer_id="diff")]
        assert chunks[0].sha256 != chunks[1].sha256


# empty file

class TestEmptyFile:
    """An empty file should produce no chunks."""

    async def test_empty_produces_nothing(self) -> None:
        data = b""
        meta = _meta(0)
        chunks = [c async for c in split(data, meta, transfer_id="empty")]
        assert len(chunks) == 0


# chunk count matches metadata

class TestChunkCountMatchesMetadata:
    """Number of yielded chunks should agree with metadata.chunk_count."""

    async def test_exact_multiple(self) -> None:
        data = b"x" * 4096
        meta = _meta(len(data), chunk_size=1024)
        chunks = [c async for c in split(data, meta, transfer_id="exact")]
        assert len(chunks) == meta.chunk_count == 4

    async def test_partial(self) -> None:
        data = b"x" * 3000
        meta = _meta(len(data), chunk_size=1024)
        chunks = [c async for c in split(data, meta, transfer_id="partial")]
        assert len(chunks) == meta.chunk_count == 3

    async def test_tiny_chunk_size(self) -> None:
        """With chunk_size=1, each byte becomes its own chunk."""
        data = b"hello"
        meta = _meta(len(data), chunk_size=1)
        chunks = [c async for c in split(data, meta, transfer_id="tiny")]
        assert len(chunks) == 5
        assert all(len(c.data) == 1 for c in chunks)
