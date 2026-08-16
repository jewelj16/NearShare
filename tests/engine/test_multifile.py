"""Tests for multi-file transfer sessions — independent per-file bitmaps
and completion tracking."""

import pytest

from engine.transfer.ack import AckTracker
from engine.transfer.session import TransferSession
from engine.types import DEFAULT_CHUNK_SIZE, FileMetadata, TransferDirection


# Helpers

def _file(name: str, chunks: int = 3) -> FileMetadata:
    return FileMetadata(name=name, size=DEFAULT_CHUNK_SIZE * chunks)


# Multi-file AckTracker

class TestMultiFileAckTracker:
    """AckTracker manages independent bitmaps per file."""

    def test_independent_bitmaps(self) -> None:
        files = [_file("a.txt", 3), _file("b.txt", 2)]
        tracker = AckTracker("sess-1", files)

        # ack all of file 0
        for s in range(3):
            tracker.ack(0, s)
        assert tracker.is_file_complete(0)
        assert not tracker.is_file_complete(1)

    def test_transfer_complete_requires_all_files(self) -> None:
        files = [_file("a", 2), _file("b", 2)]
        tracker = AckTracker("s", files)

        for s in range(2):
            tracker.ack(0, s)
        assert not tracker.is_transfer_complete()

        for s in range(2):
            tracker.ack(1, s)
        assert tracker.is_transfer_complete()

    def test_missing_per_file(self) -> None:
        files = [_file("a", 4), _file("b", 3)]
        tracker = AckTracker("s", files)

        tracker.ack(0, 0)
        tracker.ack(0, 2)
        tracker.ack(1, 1)

        assert tracker.bitmaps[0].missing == {1, 3}
        assert tracker.bitmaps[1].missing == {0, 2}

    def test_progress_independent(self) -> None:
        files = [_file("a", 4), _file("b", 2)]
        tracker = AckTracker("s", files)

        tracker.ack(0, 0)
        tracker.ack(0, 1)
        # file 0: 50%, file 1: 0%
        assert tracker.bitmaps[0].progress == pytest.approx(0.5)
        assert tracker.bitmaps[1].progress == pytest.approx(0.0)

    def test_overall_progress(self) -> None:
        files = [_file("a", 2), _file("b", 2)]
        tracker = AckTracker("s", files)
        tracker.ack(0, 0)
        tracker.ack(0, 1)
        # 2 of 4 total chunks
        assert tracker.overall_progress == pytest.approx(0.5)

    def test_out_of_range_raises(self) -> None:
        files = [_file("a", 2)]
        tracker = AckTracker("s", files)
        with pytest.raises(IndexError):
            tracker.ack(1, 0)

    def test_ack_out_of_range_seq_raises(self) -> None:
        files = [_file("a", 2)]
        tracker = AckTracker("s", files)
        with pytest.raises(ValueError):
            tracker.ack(0, 5)


# Multi-file TransferSession

class TestMultiFileSession:
    """TransferSession handles multiple files with independent bitmaps."""

    def test_create_multi_file(self) -> None:
        files = [_file("a"), _file("b"), _file("c")]
        sess = TransferSession.create(
            direction=TransferDirection.SEND,
            files=files,
        )
        assert len(sess.files) == 3

    def test_init_bitmaps_for_all_files(self) -> None:
        files = [_file("a"), _file("b")]
        sess = TransferSession.create(
            direction=TransferDirection.SEND,
            files=files,
        )
        sess.init_ack_bitmaps()
        assert set(sess.ack_bitmaps.keys()) == {0, 1}
        assert all(v == set() for v in sess.ack_bitmaps.values())

    def test_per_file_completion(self) -> None:
        files = [_file("a", 2), _file("b", 3)]
        sess = TransferSession.create(
            direction=TransferDirection.RECEIVE,
            files=files,
        )

        # complete file 0
        for s in range(2):
            sess.ack_chunk(0, s)
        assert sess.is_file_complete(0)
        assert not sess.is_file_complete(1)
        assert not sess.is_transfer_complete()

    def test_transfer_complete(self) -> None:
        files = [_file("a", 2), _file("b", 2)]
        sess = TransferSession.create(
            direction=TransferDirection.RECEIVE,
            files=files,
        )

        for fi in range(2):
            for s in range(2):
                sess.ack_chunk(fi, s)
        assert sess.is_transfer_complete()

    def test_missing_chunks_per_file(self) -> None:
        files = [_file("a", 3), _file("b", 4)]
        sess = TransferSession.create(
            direction=TransferDirection.SEND,
            files=files,
        )
        sess.ack_chunk(0, 0)
        sess.ack_chunk(1, 1)
        sess.ack_chunk(1, 3)

        assert sess.missing_chunks(0) == {1, 2}
        assert sess.missing_chunks(1) == {0, 2}

    def test_many_files(self) -> None:
        """Test with 10 files to verify scaling."""
        files = [_file(f"file_{i}.bin", 2) for i in range(10)]
        sess = TransferSession.create(
            direction=TransferDirection.SEND,
            files=files,
        )
        sess.init_ack_bitmaps()
        assert len(sess.ack_bitmaps) == 10

        # complete all
        for fi in range(10):
            for s in range(2):
                sess.ack_chunk(fi, s)
        assert sess.is_transfer_complete()
