"""Tests for engine.protocol.chunk_msg — CHUNK payload round-trips."""

import pytest

from engine.integrity.hasher import chunk_hash
from engine.protocol.chunk_msg import ChunkMsgError, build_chunk_payload, parse_chunk_payload
from engine.types import Chunk


# ── helpers ──────────────────────────────────────────────────────────────────

def _chunk(
    transfer_id: str = "sess-1",
    file_index: int = 0,
    seq: int = 0,
    data: bytes = b"hello world",
) -> Chunk:
    return Chunk(
        transfer_id=transfer_id,
        file_index=file_index,
        seq=seq,
        data=data,
        sha256=chunk_hash(data),
    )


def _round_trip(chunk: Chunk) -> Chunk:
    return parse_chunk_payload(build_chunk_payload(chunk))


# ── round-trip tests ──────────────────────────────────────────────────────────

class TestChunkPayloadRoundTrip:
    """build → parse must yield identical Chunk."""

    def test_simple_chunk(self) -> None:
        c = _chunk()
        assert _round_trip(c) == c

    def test_first_chunk_of_large_file(self) -> None:
        data = b"A" * 262_144  # 256 KB
        c = _chunk(seq=0, data=data)
        rt = _round_trip(c)
        assert rt.data == data
        assert rt.seq == 0

    def test_high_seq_number(self) -> None:
        # seq is uint32 — test near max
        c = _chunk(seq=2**32 - 1, data=b"last")
        assert _round_trip(c).seq == 2**32 - 1

    def test_multi_file_index(self) -> None:
        c = _chunk(file_index=7, seq=3, data=b"file 7 chunk 3")
        rt = _round_trip(c)
        assert rt.file_index == 7
        assert rt.seq == 3

    def test_empty_data(self) -> None:
        """Zero-length chunk (edge-case for empty files)."""
        c = _chunk(data=b"")
        rt = _round_trip(c)
        assert rt.data == b""

    def test_unicode_transfer_id(self) -> None:
        c = _chunk(transfer_id="550e8400-e29b-41d4-a716-446655440000")
        assert _round_trip(c).transfer_id == "550e8400-e29b-41d4-a716-446655440000"

    def test_binary_data_preserved(self) -> None:
        data = bytes(range(256)) * 4
        c = _chunk(data=data)
        assert _round_trip(c).data == data

    def test_sha256_field_preserved(self) -> None:
        c = _chunk(data=b"integrity check")
        rt = _round_trip(c)
        assert rt.sha256 == c.sha256
        assert len(rt.sha256) == 64

    def test_all_fields(self) -> None:
        c = Chunk(
            transfer_id="transfer-abc",
            file_index=2,
            seq=99,
            data=b"payload data",
            sha256=chunk_hash(b"payload data"),
        )
        rt = _round_trip(c)
        assert rt == c


# ── payload size ──────────────────────────────────────────────────────────────

class TestChunkPayloadSize:
    """Verify the encoded payload length is deterministic."""

    def test_payload_length_formula(self) -> None:
        data = b"test data"
        c = _chunk(data=data)
        payload = build_chunk_payload(c)
        tid_bytes = c.transfer_id.encode("utf-8")
        expected = (
            2 + len(tid_bytes)  # string header + bytes
            + 2                 # file_index uint16
            + 4                 # seq uint32
            + 4                 # data_length uint32
            + len(data)         # data
            + 64                # sha256 ascii
        )
        assert len(payload) == expected


# ── malformed-frame rejection ─────────────────────────────────────────────────

class TestChunkPayloadMalformed:
    """parse_chunk_payload rejects truncated / corrupt payloads."""

    def test_empty_payload(self) -> None:
        with pytest.raises(ChunkMsgError):
            parse_chunk_payload(b"")

    def test_truncated_transfer_id(self) -> None:
        # length prefix says 100 bytes but only 3 available
        with pytest.raises(ChunkMsgError):
            parse_chunk_payload(b"\x00\x64abc")

    def test_truncated_after_transfer_id(self) -> None:
        payload = build_chunk_payload(_chunk())
        # cut off after transfer_id
        tid_end = 2 + len("sess-1".encode("utf-8"))
        with pytest.raises(ChunkMsgError):
            parse_chunk_payload(payload[:tid_end + 1])

    def test_truncated_data_field(self) -> None:
        payload = build_chunk_payload(_chunk(data=b"hello"))
        # chop off last few bytes so data is incomplete
        with pytest.raises(ChunkMsgError):
            parse_chunk_payload(payload[:-10])

    def test_truncated_sha256(self) -> None:
        payload = build_chunk_payload(_chunk())
        # remove last 10 bytes — sha256 will be incomplete
        with pytest.raises(ChunkMsgError):
            parse_chunk_payload(payload[:-10])
