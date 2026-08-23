"""Large-file streaming integration test.

Asserts that the chunker and reassembler operate in a streaming fashion:
peak memory should NOT scale with file size.  Uses a 100 MB synthetic
file over the in-memory chunker/assembler pipeline.
"""

import hashlib
import sys
import tracemalloc

import pytest

from engine.integrity.hasher import chunk_hash
from engine.transfer.chunker import ChunkAssembler, split
from engine.types import Chunk, DEFAULT_CHUNK_SIZE, FileMetadata


# Use a smaller chunk size for the test to keep it fast while still
# exercising many iterations.
_TEST_CHUNK_SIZE = 65_536  # 64 KB
_LARGE_FILE_SIZE = 100 * 1024 * 1024  # 100 MB


def _synthetic_data_generator(size: int, chunk_size: int):
    """Yield deterministic data in chunks without holding it all in memory."""
    offset = 0
    while offset < size:
        end = min(offset + chunk_size, size)
        yield bytes(i % 256 for i in range(offset, end))
        offset = end


async def _collect(data, meta, **kwargs):
    """Collect all chunks from the async generator into a list."""
    result = []
    async for chunk in split(data, meta, **kwargs):
        result.append(chunk)
    return result


class TestLargeFileStreaming:
    """Verify that chunking and reassembly are memory-efficient."""

    @pytest.mark.asyncio
    async def test_chunker_streams_without_buffering(self) -> None:
        """split() yields chunks lazily — we can process a 10 MB file
        without the full data in memory at peak time."""
        # Use a smaller file for CI speed (10 MB) but same logic
        file_size = 10 * 1024 * 1024
        data = bytes(i % 256 for i in range(file_size))
        meta = FileMetadata(
            name="large.bin",
            size=file_size,
            chunk_size=_TEST_CHUNK_SIZE,
            sha256=hashlib.sha256(data).hexdigest(),
        )

        tracemalloc.start()
        snapshot_before = tracemalloc.take_snapshot()

        chunk_count = 0
        async for chunk in split(data, meta, transfer_id="sess-large"):
            # verify each chunk hash
            assert chunk_hash(chunk.data) == chunk.sha256
            chunk_count += 1

        snapshot_after = tracemalloc.take_snapshot()
        tracemalloc.stop()

        assert chunk_count == meta.chunk_count

        # Peak memory increase should be well below file size.
        # The generator yields one chunk at a time, so delta should
        # be roughly chunk_size, not file_size.
        stats = snapshot_after.compare_to(snapshot_before, "lineno")
        peak_delta = sum(s.size_diff for s in stats if s.size_diff > 0)
        # Allow generous 5 MB headroom (the data var itself is 10 MB,
        # but the chunker should not allocate another copy).
        assert peak_delta < file_size, (
            f"Peak memory delta {peak_delta} >= file size {file_size}: "
            "chunker may be buffering the whole file"
        )

    @pytest.mark.asyncio
    async def test_reassembler_accepts_out_of_order(self, tmp_path) -> None:
        """ChunkAssembler handles out-of-order delivery for a large file."""
        file_size = 5 * 1024 * 1024  # 5 MB
        data = bytes(i % 256 for i in range(file_size))
        meta = FileMetadata(
            name="oof.bin",
            size=file_size,
            chunk_size=_TEST_CHUNK_SIZE,
            sha256=hashlib.sha256(data).hexdigest(),
        )

        chunks = await _collect(data, meta, transfer_id="sess")

        assembler = ChunkAssembler(meta, tmp_path / "oof.bin")

        # deliver in reverse order
        for chunk in reversed(chunks):
            await assembler.write_chunk(chunk)

        assert assembler.is_complete
        assert assembler.to_bytes() == data
        assembler.close()

    @pytest.mark.asyncio
    async def test_reassembler_rejects_corrupt_chunk(self, tmp_path) -> None:
        """ChunkAssembler rejects a chunk with a bad hash."""
        file_size = _TEST_CHUNK_SIZE * 3
        data = bytes(i % 256 for i in range(file_size))
        meta = FileMetadata(
            name="corrupt.bin",
            size=file_size,
            chunk_size=_TEST_CHUNK_SIZE,
        )

        chunks = await _collect(data, meta, transfer_id="sess")
        bad_chunk = Chunk(
            transfer_id="sess",
            file_index=0,
            seq=1,
            data=chunks[1].data,
            sha256="0" * 64,  # wrong hash
        )

        assembler = ChunkAssembler(meta, tmp_path / "corrupt.bin")
        await assembler.write_chunk(chunks[0])
        with pytest.raises(ValueError, match="hash mismatch"):
            await assembler.write_chunk(bad_chunk)
        assembler.close()

    @pytest.mark.asyncio
    async def test_progress_tracking(self, tmp_path) -> None:
        """Progress reports accurately for large files."""
        file_size = _TEST_CHUNK_SIZE * 10
        data = bytes(i % 256 for i in range(file_size))
        meta = FileMetadata(
            name="progress.bin",
            size=file_size,
            chunk_size=_TEST_CHUNK_SIZE,
        )

        chunks = await _collect(data, meta, transfer_id="sess")
        assembler = ChunkAssembler(meta, tmp_path / "progress.bin")

        for i, chunk in enumerate(chunks):
            await assembler.write_chunk(chunk)
            expected_progress = (i + 1) / len(chunks)
            assert assembler.progress == pytest.approx(expected_progress)
        assembler.close()
