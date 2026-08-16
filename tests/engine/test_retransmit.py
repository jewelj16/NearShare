"""Tests for engine.transfer.retransmit — missing-chunk retransmission
with FakeTransport packet loss and corrupted-chunk detection."""

import hashlib

import pytest

from engine.integrity.hasher import chunk_hash
from engine.transfer.retransmit import (
    CorruptChunkError,
    RetransmitController,
    RetransmitError,
    RetransmitRequest,
    compute_retransmit_requests,
    detect_corrupt_chunk,
    verify_chunk,
)
from engine.transfer.retry import RetryPolicy
from engine.types import Chunk, DEFAULT_CHUNK_SIZE, FileMetadata


# Helpers

def _file(name: str = "f.bin", size: int = DEFAULT_CHUNK_SIZE * 4) -> FileMetadata:
    return FileMetadata(name=name, size=size)


def _chunk(seq: int = 0, data: bytes = b"hello", file_index: int = 0) -> Chunk:
    return Chunk(
        transfer_id="sess-1",
        file_index=file_index,
        seq=seq,
        data=data,
        sha256=chunk_hash(data),
    )


def _corrupt_chunk(seq: int = 0, data: bytes = b"hello") -> Chunk:
    """Create a chunk whose sha256 does NOT match its data."""
    return Chunk(
        transfer_id="sess-1",
        file_index=0,
        seq=seq,
        data=data,
        sha256="0" * 64,  # wrong hash
    )


# verify_chunk

class TestVerifyChunk:
    """Per-chunk hash verification."""

    def test_valid_chunk(self) -> None:
        c = _chunk(data=b"good data")
        assert verify_chunk(c) is True

    def test_corrupt_chunk(self) -> None:
        c = _corrupt_chunk(data=b"good data")
        assert verify_chunk(c) is False

    def test_empty_data(self) -> None:
        c = _chunk(data=b"")
        assert verify_chunk(c) is True


# detect_corrupt_chunk

class TestDetectCorruptChunk:
    """Corruption detection returns error details or None."""

    def test_no_corruption(self) -> None:
        c = _chunk(data=b"valid")
        assert detect_corrupt_chunk(c) is None

    def test_corruption_detected(self) -> None:
        c = _corrupt_chunk(data=b"valid")
        err = detect_corrupt_chunk(c)
        assert err is not None
        assert isinstance(err, CorruptChunkError)
        assert err.seq == 0
        assert err.file_index == 0
        assert err.expected == "0" * 64
        assert err.actual == chunk_hash(b"valid")

    def test_corruption_error_message(self) -> None:
        c = _corrupt_chunk(seq=3, data=b"x")
        err = detect_corrupt_chunk(c)
        assert err is not None
        assert "hash mismatch" in str(err)
        assert "0:3" in str(err)


# compute_retransmit_requests

class TestComputeRetransmitRequests:
    """Build retransmit request lists from ACK bitmaps."""

    def test_no_missing(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 2)
        bitmaps = {0: {0, 1}}
        assert compute_retransmit_requests([f], bitmaps) == []

    def test_all_missing(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 3)
        bitmaps: dict[int, set[int]] = {}
        reqs = compute_retransmit_requests([f], bitmaps)
        assert len(reqs) == 1
        assert reqs[0].file_index == 0
        assert reqs[0].missing_seqs == {0, 1, 2}

    def test_partial_missing(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 4)
        bitmaps = {0: {0, 2}}  # missing 1 and 3
        reqs = compute_retransmit_requests([f], bitmaps)
        assert len(reqs) == 1
        assert reqs[0].missing_seqs == {1, 3}

    def test_multiple_files(self) -> None:
        f1 = _file("a", DEFAULT_CHUNK_SIZE * 2)
        f2 = _file("b", DEFAULT_CHUNK_SIZE * 3)
        bitmaps = {0: {0, 1}, 1: {0}}  # f1 complete, f2 missing 1,2
        reqs = compute_retransmit_requests([f1, f2], bitmaps)
        assert len(reqs) == 1
        assert reqs[0].file_index == 1
        assert reqs[0].missing_seqs == {1, 2}

    def test_both_files_missing(self) -> None:
        f1 = _file("a", DEFAULT_CHUNK_SIZE * 2)
        f2 = _file("b", DEFAULT_CHUNK_SIZE * 2)
        bitmaps = {0: {0}, 1: {1}}
        reqs = compute_retransmit_requests([f1, f2], bitmaps)
        assert len(reqs) == 2


# RetransmitController

class TestRetransmitController:
    """Controller coordinates retransmit with backoff."""

    def test_returns_requests_when_missing(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 3)
        ctrl = RetransmitController([f])
        bitmaps = {0: {0}}
        reqs = ctrl.request_retransmit(bitmaps)
        assert len(reqs) == 1
        assert reqs[0].missing_seqs == {1, 2}

    def test_returns_empty_when_complete(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 2)
        ctrl = RetransmitController([f])
        bitmaps = {0: {0, 1}}
        reqs = ctrl.request_retransmit(bitmaps)
        assert reqs == []

    def test_exhaustion_raises(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 2)
        policy = RetryPolicy(max_attempts=2, jitter=False)
        ctrl = RetransmitController([f], policy=policy)
        bitmaps: dict[int, set[int]] = {0: set()}

        ctrl.request_retransmit(bitmaps)
        ctrl.request_retransmit(bitmaps)

        with pytest.raises(RetransmitError, match="failed"):
            ctrl.request_retransmit(bitmaps)

    def test_mark_success_resets(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 2)
        policy = RetryPolicy(max_attempts=2, jitter=False)
        ctrl = RetransmitController([f], policy=policy)
        bitmaps: dict[int, set[int]] = {0: set()}

        ctrl.request_retransmit(bitmaps)
        ctrl.mark_success()
        ctrl.request_retransmit(bitmaps)
        ctrl.request_retransmit(bitmaps)

    def test_no_advance_when_nothing_missing(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 2)
        policy = RetryPolicy(max_attempts=1, jitter=False)
        ctrl = RetransmitController([f], policy=policy)
        bitmaps = {0: {0, 1}}

        for _ in range(5):
            reqs = ctrl.request_retransmit(bitmaps)
            assert reqs == []
        assert not ctrl.exhausted


# FakeTransport packet-loss integration

class TestRetransmitWithPacketLoss:
    """Simulate packet loss and verify retransmission catches the gap."""

    def test_dropped_chunk_detected(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 4)
        ctrl = RetransmitController([f])
        bitmaps = {0: {0, 1, 3}}
        reqs = ctrl.request_retransmit(bitmaps)
        assert len(reqs) == 1
        assert reqs[0].missing_seqs == {2}

    def test_multiple_drops(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 5)
        ctrl = RetransmitController([f])
        bitmaps = {0: {0, 2, 4}}
        reqs = ctrl.request_retransmit(bitmaps)
        assert reqs[0].missing_seqs == {1, 3}

    def test_retransmit_then_complete(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 3)
        ctrl = RetransmitController([f])

        bitmaps = {0: {0, 2}}
        reqs = ctrl.request_retransmit(bitmaps)
        assert reqs[0].missing_seqs == {1}

        ctrl.mark_success()
        bitmaps[0].add(1)
        reqs = ctrl.request_retransmit(bitmaps)
        assert reqs == []
