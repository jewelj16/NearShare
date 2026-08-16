"""Tests for engine.transfer.retransmit — missing-chunk retransmission
with FakeTransport packet loss."""

import pytest

from engine.transfer.retransmit import (
    RetransmitController,
    RetransmitError,
    RetransmitRequest,
    compute_retransmit_requests,
)
from engine.transfer.retry import RetryPolicy
from engine.types import DEFAULT_CHUNK_SIZE, FileMetadata


# Helpers

def _file(name: str = "f.bin", size: int = DEFAULT_CHUNK_SIZE * 4) -> FileMetadata:
    return FileMetadata(name=name, size=size)


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
