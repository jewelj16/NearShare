"""Tests for ChunkAssembler — chunk reassembly including out-of-order writes."""

import hashlib

import pytest

from engine.transfer.chunker import ChunkAssembler, split
from engine.types import Chunk, DEFAULT_CHUNK_SIZE, FileMetadata


# helpers

def _meta(size: int, chunk_size: int = 1024) -> FileMetadata:
    return FileMetadata(name="test.bin", size=size, chunk_size=chunk_size)


def _make_chunk(data: bytes, seq: int, transfer_id: str = "s", file_index: int = 0) -> Chunk:
    return Chunk(
        transfer_id=transfer_id,
        file_index=file_index,
        seq=seq,
        data=data,
        sha256=hashlib.sha256(data).hexdigest(),
    )


# basic reassembly

class TestReassemblyBasic:
    """In-order reassembly should reproduce the original file."""

    def test_single_chunk_reassembly(self) -> None:
        data = b"hello world"
        meta = _meta(len(data), chunk_size=1024)
        assembler = ChunkAssembler(meta)

        chunks = list(split(data, meta, transfer_id="s"))
        assert len(chunks) == 1

        assembler.write_chunk(chunks[0])
        assert assembler.is_complete
        assert assembler.to_bytes() == data

    def test_multi_chunk_in_order(self) -> None:
        data = b"A" * 1000 + b"B" * 1000 + b"C" * 500
        meta = _meta(len(data), chunk_size=1000)
        assembler = ChunkAssembler(meta)

        for chunk in split(data, meta, transfer_id="s"):
            assembler.write_chunk(chunk)

        assert assembler.is_complete
        assert assembler.to_bytes() == data

    def test_split_then_reassemble_round_trip(self) -> None:
        """split + reassemble should be a perfect round-trip."""
        data = bytes(range(256)) * 10  # 2560 bytes
        meta = _meta(len(data), chunk_size=700)
        assembler = ChunkAssembler(meta)

        for chunk in split(data, meta, transfer_id="rt"):
            assembler.write_chunk(chunk)

        assert assembler.to_bytes() == data


# out-of-order reassembly (key requirement)

class TestOutOfOrder:
    """Chunks arriving in non-sequential order must reassemble correctly."""

    def test_reverse_order(self) -> None:
        data = b"A" * 1000 + b"B" * 1000 + b"C" * 1000 + b"D" * 500
        meta = _meta(len(data), chunk_size=1000)
        assembler = ChunkAssembler(meta)

        chunks = list(split(data, meta, transfer_id="rev"))
        # write in reverse
        for chunk in reversed(chunks):
            assembler.write_chunk(chunk)

        assert assembler.is_complete
        assert assembler.to_bytes() == data

    def test_interleaved_order(self) -> None:
        data = b"x" * 5000
        meta = _meta(len(data), chunk_size=1000)
        assembler = ChunkAssembler(meta)

        chunks = list(split(data, meta, transfer_id="inter"))
        # write even seqs first, then odd
        for chunk in chunks:
            if chunk.seq % 2 == 0:
                assembler.write_chunk(chunk)
        for chunk in chunks:
            if chunk.seq % 2 == 1:
                assembler.write_chunk(chunk)

        assert assembler.is_complete
        assert assembler.to_bytes() == data

    def test_random_order(self) -> None:
        import random
        rng = random.Random(42)
        data = bytes(rng.getrandbits(8) for _ in range(3000))
        meta = _meta(len(data), chunk_size=512)
        assembler = ChunkAssembler(meta)

        chunks = list(split(data, meta, transfer_id="rand"))
        rng.shuffle(chunks)

        for chunk in chunks:
            assembler.write_chunk(chunk)

        assert assembler.is_complete
        assert assembler.to_bytes() == data

    def test_last_chunk_first(self) -> None:
        """Writing only the last (short) chunk first, then the rest."""
        data = b"Z" * 2500  # last chunk = 500 bytes
        meta = _meta(len(data), chunk_size=1000)
        assembler = ChunkAssembler(meta)

        chunks = list(split(data, meta, transfer_id="last-first"))
        # write last chunk first
        assembler.write_chunk(chunks[-1])
        assert not assembler.is_complete

        for chunk in chunks[:-1]:
            assembler.write_chunk(chunk)

        assert assembler.is_complete
        assert assembler.to_bytes() == data


# duplicate writes

class TestDuplicateWrites:
    """Writing the same chunk twice should be a no-op."""

    def test_duplicate_is_idempotent(self) -> None:
        data = b"abc" * 500
        meta = _meta(len(data), chunk_size=500)
        assembler = ChunkAssembler(meta)

        chunks = list(split(data, meta, transfer_id="dup"))
        assembler.write_chunk(chunks[0])
        assembler.write_chunk(chunks[0])  # duplicate

        assert len(assembler.received) == 1


# progress tracking

class TestProgress:
    """Progress and missing_seqs properties."""

    def test_progress_starts_at_zero(self) -> None:
        meta = _meta(3000, chunk_size=1000)
        assembler = ChunkAssembler(meta)
        assert assembler.progress == 0.0

    def test_progress_partial(self) -> None:
        meta = _meta(4000, chunk_size=1000)
        assembler = ChunkAssembler(meta)
        data = b"x" * 4000
        chunks = list(split(data, meta, transfer_id="prog"))
        assembler.write_chunk(chunks[0])
        assert assembler.progress == pytest.approx(0.25)

    def test_progress_complete(self) -> None:
        meta = _meta(2000, chunk_size=1000)
        assembler = ChunkAssembler(meta)
        data = b"x" * 2000
        for chunk in split(data, meta, transfer_id="comp"):
            assembler.write_chunk(chunk)
        assert assembler.progress == 1.0

    def test_missing_seqs(self) -> None:
        meta = _meta(3000, chunk_size=1000)
        assembler = ChunkAssembler(meta)
        data = b"x" * 3000
        chunks = list(split(data, meta, transfer_id="miss"))
        assembler.write_chunk(chunks[1])  # only write seq=1
        assert assembler.missing_seqs == {0, 2}

    def test_empty_file_progress(self) -> None:
        meta = _meta(0)
        assembler = ChunkAssembler(meta)
        assert assembler.progress == 1.0
        assert assembler.missing_seqs == set()


# error handling

class TestReassemblyErrors:
    """Validation and error cases."""

    def test_hash_mismatch_rejected(self) -> None:
        meta = _meta(1024, chunk_size=1024)
        bad_chunk = Chunk(
            transfer_id="s", file_index=0, seq=0,
            data=b"x" * 1024,
            sha256="0000000000000000000000000000000000000000000000000000000000000000",
        )
        assembler = ChunkAssembler(meta)
        with pytest.raises(ValueError, match="hash mismatch"):
            assembler.write_chunk(bad_chunk)

    def test_seq_out_of_range(self) -> None:
        meta = _meta(1024, chunk_size=1024)  # chunk_count = 1
        assembler = ChunkAssembler(meta)
        chunk = _make_chunk(b"x", seq=5)
        with pytest.raises(ValueError, match="out of range"):
            assembler.write_chunk(chunk)

    def test_negative_seq(self) -> None:
        meta = _meta(1024, chunk_size=1024)
        assembler = ChunkAssembler(meta)
        chunk = _make_chunk(b"x", seq=-1)
        with pytest.raises(ValueError, match="out of range"):
            assembler.write_chunk(chunk)

    def test_to_bytes_before_complete_raises(self) -> None:
        meta = _meta(2048, chunk_size=1024)
        assembler = ChunkAssembler(meta)
        with pytest.raises(RuntimeError, match="incomplete"):
            assembler.to_bytes()
