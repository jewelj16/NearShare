"""Transfer progress tracking and reporting.

Provides a ProgressTracker that monitors transfer progress across all
files and emits callbacks at configurable intervals.  Used by both
TransferSender and TransferReceiver to report throughput and ETA.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from engine.logging_.setup import get_logger
from engine.types import FileMetadata


logger = get_logger("transfer.progress")


@dataclass(frozen=True)
class ProgressSnapshot:
    """Immutable snapshot of transfer progress at a point in time.

    Attributes:
        total_bytes:       Total bytes expected across all files.
        bytes_transferred: Bytes sent/received so far.
        total_files:       Number of files in the transfer.
        files_complete:    Number of files fully transferred.
        elapsed_s:         Seconds since transfer started.
        active_file:       Name of the file currently being transferred.
    """

    total_bytes: int
    bytes_transferred: int
    total_files: int
    files_complete: int
    elapsed_s: float
    active_file: str = ""

    @property
    def fraction(self) -> float:
        """Progress as a fraction [0.0, 1.0]."""
        if self.total_bytes == 0:
            return 1.0
        return min(self.bytes_transferred / self.total_bytes, 1.0)

    @property
    def percent(self) -> float:
        """Progress as a percentage [0.0, 100.0]."""
        return self.fraction * 100.0

    @property
    def throughput_mbps(self) -> float:
        """Average throughput in megabits per second."""
        if self.elapsed_s <= 0:
            return 0.0
        return (self.bytes_transferred * 8) / (self.elapsed_s * 1_000_000)

    @property
    def throughput_mbs(self) -> float:
        """Average throughput in megabytes per second."""
        if self.elapsed_s <= 0:
            return 0.0
        return self.bytes_transferred / (self.elapsed_s * 1_048_576)

    @property
    def eta_s(self) -> float | None:
        """Estimated seconds remaining, or None if unknown."""
        if self.elapsed_s <= 0 or self.bytes_transferred == 0:
            return None
        rate = self.bytes_transferred / self.elapsed_s
        remaining = self.total_bytes - self.bytes_transferred
        if remaining <= 0:
            return 0.0
        return remaining / rate

    def format_bar(self, width: int = 30) -> str:
        """Render a text progress bar.

        Example: [████████████░░░░░░░░░░░░░░░░░░]  42.0% | 1.2 MB/s | ETA 3s
        """
        filled = int(width * self.fraction)
        bar = "█" * filled + "░" * (width - filled)

        eta = self.eta_s
        eta_str = f"ETA {eta:.0f}s" if eta is not None else "ETA --"

        return (
            f"[{bar}] {self.percent:5.1f}% | "
            f"{self.throughput_mbs:.1f} MB/s | {eta_str}"
        )

    def __str__(self) -> str:
        return self.format_bar()


# Callback type: called with each progress snapshot
ProgressCallback = Callable[[ProgressSnapshot], None]


class ProgressTracker:
    """Tracks transfer progress and emits snapshots.

    Args:
        files:             List of files in the transfer.
        callback:          Called with a ProgressSnapshot on each update.
        report_interval_s: Minimum seconds between callback invocations
                           (default 0.5s to avoid spamming the terminal).
    """

    def __init__(
        self,
        files: list[FileMetadata],
        callback: ProgressCallback | None = None,
        report_interval_s: float = 0.5,
    ) -> None:
        self._files = files
        self._callback = callback
        self._report_interval = report_interval_s
        self._total_bytes = sum(f.size for f in files)
        self._bytes_transferred = 0
        self._files_complete = 0
        self._active_file_index = 0
        self._start_time = time.monotonic()
        self._last_report_time = 0.0

    @property
    def total_bytes(self) -> int:
        return self._total_bytes

    @property
    def bytes_transferred(self) -> int:
        return self._bytes_transferred

    @property
    def files_complete(self) -> int:
        return self._files_complete

    def snapshot(self) -> ProgressSnapshot:
        """Create a ProgressSnapshot of the current state."""
        active = ""
        if 0 <= self._active_file_index < len(self._files):
            active = self._files[self._active_file_index].name
        return ProgressSnapshot(
            total_bytes=self._total_bytes,
            bytes_transferred=self._bytes_transferred,
            total_files=len(self._files),
            files_complete=self._files_complete,
            elapsed_s=time.monotonic() - self._start_time,
            active_file=active,
        )

    def update(self, bytes_delta: int) -> None:
        """Record *bytes_delta* bytes transferred and maybe emit progress.

        Args:
            bytes_delta: Number of new bytes transferred since last update.
        """
        self._bytes_transferred += bytes_delta
        self._maybe_report()

    def file_complete(self) -> None:
        """Mark the current file as complete and advance to the next."""
        self._files_complete += 1
        self._active_file_index += 1
        self._maybe_report(force=True)

    def finish(self) -> ProgressSnapshot:
        """Mark the transfer as finished and emit final progress."""
        snap = self.snapshot()
        if self._callback:
            self._callback(snap)
        return snap

    def _maybe_report(self, force: bool = False) -> None:
        """Emit a progress callback if enough time has elapsed."""
        if self._callback is None:
            return
        now = time.monotonic()
        if not force and (now - self._last_report_time) < self._report_interval:
            return
        self._last_report_time = now
        self._callback(self.snapshot())


def cli_progress_callback(snap: ProgressSnapshot) -> None:
    """Default CLI progress callback — prints a progress bar to stdout.

    Uses carriage return to overwrite the previous line.
    """
    print(f"\r  {snap.format_bar()}", end="", flush=True)
    if snap.fraction >= 1.0:
        print()  # newline when done
