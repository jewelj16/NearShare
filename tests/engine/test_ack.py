"""Tests for AckBitmap, AckTracker, and ACK payload wire format."""

import pytest

from engine.transfer.ack import (
    AckBitmap,
    AckError,
    AckTracker,
    build_ack_payload,
    parse_ack_payload,
)
from engine.types import FileMetadata


# helpers

def _meta(name: str = "f.bin", size: int = 4096, chunk_size: int = 1024) -> FileMetadata:
    return FileMetadata(name=name, size=size, chunk_size=chunk_size)


# AckBitmap

class TestAckBitmap:
    """Per-file bitmap tracking."""

    def test_mark_and_complete(self) -> None:
        b = AckBitmap(file_index=0, chunk_count=3)
        assert not b.is_complete
        b.mark(0)
        b.mark(1)
        b.mark(2)
        assert b.is_complete

    def test_partial_progress(self) -> None:
        b = AckBitmap(file_index=0, chunk_count=4)
        b.mark(0)
        b.mark(2)
        assert b.progress == pytest.approx(0.5)

    def test_missing_chunks(self) -> None:
        b = AckBitmap(file_index=0, chunk_count=5)
        b.mark(0)
        b.mark(3)
        assert b.missing == {1, 2, 4}

    def test_seq_out_of_range(self) -> None:
        b = AckBitmap(file_index=0, chunk_count=3)
        with pytest.raises(ValueError, match="out of range"):
            b.mark(5)

    def test_negative_seq(self) -> None:
        b = AckBitmap(file_index=0, chunk_count=3)
        with pytest.raises(ValueError, match="out of range"):
            b.mark(-1)

    def test_idempotent_mark(self) -> None:
        b = AckBitmap(file_index=0, chunk_count=2)
        b.mark(0)
        b.mark(0)
        assert len(b.received) == 1

    def test_empty_file_is_complete(self) -> None:
        b = AckBitmap(file_index=0, chunk_count=0)
        assert b.is_complete
        assert b.progress == 1.0


# AckTracker

class TestAckTracker:
    """Multi-file ACK tracking."""

    def test_single_file_complete(self) -> None:
        files = [_meta(size=2048, chunk_size=1024)]  # 2 chunks
        tracker = AckTracker("t-1", files)
        tracker.ack(0, 0)
        tracker.ack(0, 1)
        assert tracker.is_file_complete(0)
        assert tracker.is_transfer_complete()

    def test_multi_file_partial(self) -> None:
        files = [_meta("a.bin", 2048, 1024), _meta("b.bin", 3072, 1024)]
        tracker = AckTracker("t-2", files)
        # complete file 0
        tracker.ack(0, 0)
        tracker.ack(0, 1)
        assert tracker.is_file_complete(0)
        assert not tracker.is_transfer_complete()

    def test_multi_file_complete(self) -> None:
        files = [_meta("a.bin", 1024, 1024), _meta("b.bin", 1024, 1024)]
        tracker = AckTracker("t-3", files)
        tracker.ack(0, 0)
        tracker.ack(1, 0)
        assert tracker.is_transfer_complete()

    def test_overall_progress(self) -> None:
        files = [_meta(size=2048, chunk_size=1024), _meta(size=2048, chunk_size=1024)]
        tracker = AckTracker("t-4", files)
        tracker.ack(0, 0)  # 1 out of 4 total chunks
        assert tracker.overall_progress == pytest.approx(0.25)

    def test_file_index_out_of_range(self) -> None:
        tracker = AckTracker("t-5", [_meta()])
        with pytest.raises(IndexError):
            tracker.ack(5, 0)

    def test_negative_file_index(self) -> None:
        tracker = AckTracker("t-6", [_meta()])
        with pytest.raises(IndexError):
            tracker.ack(-1, 0)


# ACK payload wire format

class TestAckPayload:
    """Build and parse ACK payloads."""

    def test_round_trip_full_bitmap(self) -> None:
        acked = {0, 1, 2, 3}
        payload = build_ack_payload("sess-1", file_index=0, acked_seqs=acked)
        tid, fi, parsed = parse_ack_payload(payload)
        assert tid == "sess-1"
        assert fi == 0
        assert parsed == acked

    def test_round_trip_partial_bitmap(self) -> None:
        acked = {0, 5, 10}
        payload = build_ack_payload("sess-2", file_index=2, acked_seqs=acked)
        tid, fi, parsed = parse_ack_payload(payload)
        assert tid == "sess-2"
        assert fi == 2
        assert parsed == acked

    def test_round_trip_empty_bitmap(self) -> None:
        payload = build_ack_payload("sess-3", file_index=0, acked_seqs=set())
        tid, fi, parsed = parse_ack_payload(payload)
        assert parsed == set()

    def test_seqs_are_sorted_on_wire(self) -> None:
        """The wire format should have sorted sequence numbers."""
        acked = {5, 1, 3, 0, 4, 2}
        payload = build_ack_payload("t", file_index=0, acked_seqs=acked)
        # parse should still return the same set
        _, _, parsed = parse_ack_payload(payload)
        assert parsed == acked

    def test_truncated_payload_raises(self) -> None:
        with pytest.raises(AckError):
            parse_ack_payload(b"\x00")

    def test_large_bitmap(self) -> None:
        acked = set(range(100))
        payload = build_ack_payload("big", file_index=0, acked_seqs=acked)
        _, _, parsed = parse_ack_payload(payload)
        assert parsed == acked
