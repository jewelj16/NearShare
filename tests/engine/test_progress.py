"""Tests for engine.transfer.progress — ProgressTracker and ProgressSnapshot."""

import time

import pytest

from engine.transfer.progress import (
    ProgressCallback,
    ProgressSnapshot,
    ProgressTracker,
    cli_progress_callback,
)
from engine.types import FileMetadata


# ── helpers ───────────────────────────────────────────────────────────────────

def _file(name: str = "f.bin", size: int = 1_048_576) -> FileMetadata:
    return FileMetadata(name=name, size=size)


# ── ProgressSnapshot ──────────────────────────────────────────────────────────

class TestProgressSnapshot:
    """Immutable snapshot of transfer progress."""

    def test_fraction_half(self) -> None:
        snap = ProgressSnapshot(
            total_bytes=1000, bytes_transferred=500,
            total_files=1, files_complete=0, elapsed_s=1.0,
        )
        assert snap.fraction == pytest.approx(0.5)

    def test_fraction_zero_total(self) -> None:
        snap = ProgressSnapshot(
            total_bytes=0, bytes_transferred=0,
            total_files=0, files_complete=0, elapsed_s=1.0,
        )
        assert snap.fraction == 1.0

    def test_fraction_capped_at_one(self) -> None:
        snap = ProgressSnapshot(
            total_bytes=100, bytes_transferred=200,
            total_files=1, files_complete=1, elapsed_s=1.0,
        )
        assert snap.fraction == 1.0

    def test_percent(self) -> None:
        snap = ProgressSnapshot(
            total_bytes=1000, bytes_transferred=250,
            total_files=1, files_complete=0, elapsed_s=1.0,
        )
        assert snap.percent == pytest.approx(25.0)

    def test_throughput_mbps(self) -> None:
        # 1 MiB in 1 second = 8.388608 Mbps
        snap = ProgressSnapshot(
            total_bytes=1_048_576, bytes_transferred=1_048_576,
            total_files=1, files_complete=1, elapsed_s=1.0,
        )
        assert snap.throughput_mbps == pytest.approx(8.388608, rel=0.01)

    def test_throughput_zero_elapsed(self) -> None:
        snap = ProgressSnapshot(
            total_bytes=1000, bytes_transferred=500,
            total_files=1, files_complete=0, elapsed_s=0.0,
        )
        assert snap.throughput_mbps == 0.0
        assert snap.throughput_mbs == 0.0

    def test_eta_halfway(self) -> None:
        snap = ProgressSnapshot(
            total_bytes=1000, bytes_transferred=500,
            total_files=1, files_complete=0, elapsed_s=10.0,
        )
        assert snap.eta_s == pytest.approx(10.0)

    def test_eta_none_when_no_progress(self) -> None:
        snap = ProgressSnapshot(
            total_bytes=1000, bytes_transferred=0,
            total_files=1, files_complete=0, elapsed_s=1.0,
        )
        assert snap.eta_s is None

    def test_eta_zero_when_complete(self) -> None:
        snap = ProgressSnapshot(
            total_bytes=1000, bytes_transferred=1000,
            total_files=1, files_complete=1, elapsed_s=5.0,
        )
        assert snap.eta_s == 0.0

    def test_format_bar_contains_percent(self) -> None:
        snap = ProgressSnapshot(
            total_bytes=1000, bytes_transferred=333,
            total_files=1, files_complete=0, elapsed_s=1.0,
        )
        bar = snap.format_bar()
        assert "33.3%" in bar
        assert "MB/s" in bar
        assert "ETA" in bar

    def test_str_returns_bar(self) -> None:
        snap = ProgressSnapshot(
            total_bytes=1000, bytes_transferred=500,
            total_files=1, files_complete=0, elapsed_s=1.0,
        )
        assert "[" in str(snap) and "]" in str(snap)


# ── ProgressTracker ───────────────────────────────────────────────────────────

class TestProgressTracker:
    """Tracks progress and emits callbacks."""

    def test_initial_state(self) -> None:
        tracker = ProgressTracker([_file(size=1000)])
        assert tracker.total_bytes == 1000
        assert tracker.bytes_transferred == 0
        assert tracker.files_complete == 0

    def test_update_accumulates(self) -> None:
        tracker = ProgressTracker([_file(size=1000)])
        tracker.update(100)
        tracker.update(200)
        assert tracker.bytes_transferred == 300

    def test_file_complete_increments(self) -> None:
        tracker = ProgressTracker([_file(), _file()])
        tracker.file_complete()
        assert tracker.files_complete == 1
        tracker.file_complete()
        assert tracker.files_complete == 2

    def test_snapshot_reflects_state(self) -> None:
        tracker = ProgressTracker([_file(name="a.bin", size=500)])
        tracker.update(250)
        snap = tracker.snapshot()
        assert snap.bytes_transferred == 250
        assert snap.total_bytes == 500
        assert snap.active_file == "a.bin"

    def test_callback_invoked(self) -> None:
        received: list[ProgressSnapshot] = []
        tracker = ProgressTracker(
            [_file(size=100)],
            callback=received.append,
            report_interval_s=0.0,  # no throttling
        )
        tracker.update(50)
        tracker.update(50)
        assert len(received) >= 2

    def test_callback_throttled(self) -> None:
        received: list[ProgressSnapshot] = []
        tracker = ProgressTracker(
            [_file(size=100)],
            callback=received.append,
            report_interval_s=10.0,  # long throttle
        )
        tracker.update(10)
        tracker.update(10)
        tracker.update(10)
        # should only fire once (or zero if throttle window not passed)
        assert len(received) <= 1

    def test_finish_emits_final(self) -> None:
        received: list[ProgressSnapshot] = []
        tracker = ProgressTracker(
            [_file(size=100)],
            callback=received.append,
            report_interval_s=10.0,  # long throttle
        )
        tracker.update(100)
        snap = tracker.finish()
        assert snap.bytes_transferred == 100
        # finish always emits
        assert len(received) >= 1

    def test_multi_file_total(self) -> None:
        files = [_file(size=100), _file(size=200), _file(size=300)]
        tracker = ProgressTracker(files)
        assert tracker.total_bytes == 600

    def test_no_callback_no_crash(self) -> None:
        tracker = ProgressTracker([_file()])
        tracker.update(1000)  # should not raise
        tracker.file_complete()
        tracker.finish()


# ── cli_progress_callback ────────────────────────────────────────────────────

class TestCliProgressCallback:
    """CLI callback prints without crashing."""

    def test_does_not_crash(self, capsys) -> None:
        snap = ProgressSnapshot(
            total_bytes=1000, bytes_transferred=500,
            total_files=1, files_complete=0, elapsed_s=1.0,
        )
        cli_progress_callback(snap)
        out = capsys.readouterr().out
        assert "%" in out

    def test_complete_prints_newline(self, capsys) -> None:
        snap = ProgressSnapshot(
            total_bytes=1000, bytes_transferred=1000,
            total_files=1, files_complete=1, elapsed_s=1.0,
        )
        cli_progress_callback(snap)
        out = capsys.readouterr().out
        assert out.endswith("\n")
