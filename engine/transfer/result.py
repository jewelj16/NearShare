"""TransferResult — outcome of a completed, failed, or cancelled transfer.

Both TransferSender and TransferReceiver return a TransferResult when
their ``run()`` coroutine finishes, regardless of whether the transfer
succeeded, failed, or was cancelled.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.types import FileMetadata, TransferState


@dataclass(frozen=True)
class TransferResult:
    """Immutable summary of a transfer's outcome.

    Attributes:
        session_id:        Unique identifier for the session.
        files:             Ordered list of files that were part of the transfer.
        state:             Final state (COMPLETE, FAILED, or INTERRUPTED).
        duration_s:        Wall-clock seconds the transfer ran for.
        bytes_transferred: Total bytes of file data sent or received.
        error_msg:         Human-readable error description, or None on success.
    """

    session_id: str
    files: tuple[FileMetadata, ...]
    state: TransferState
    duration_s: float
    bytes_transferred: int
    error_msg: str | None = None

    # ── convenience constructors ──────────────────────────────────────────────

    @staticmethod
    def success(
        session_id: str,
        files: list[FileMetadata],
        duration_s: float,
        bytes_transferred: int,
    ) -> TransferResult:
        """Create a successful result."""
        return TransferResult(
            session_id=session_id,
            files=tuple(files),
            state=TransferState.COMPLETE,
            duration_s=duration_s,
            bytes_transferred=bytes_transferred,
        )

    @staticmethod
    def failure(
        session_id: str,
        files: list[FileMetadata],
        duration_s: float,
        bytes_transferred: int,
        error_msg: str,
    ) -> TransferResult:
        """Create a failed result."""
        return TransferResult(
            session_id=session_id,
            files=tuple(files),
            state=TransferState.FAILED,
            duration_s=duration_s,
            bytes_transferred=bytes_transferred,
            error_msg=error_msg,
        )

    @staticmethod
    def cancelled(
        session_id: str,
        files: list[FileMetadata],
        duration_s: float,
        bytes_transferred: int,
        reason: str = "Transfer cancelled",
    ) -> TransferResult:
        """Create a cancelled (interrupted) result."""
        return TransferResult(
            session_id=session_id,
            files=tuple(files),
            state=TransferState.INTERRUPTED,
            duration_s=duration_s,
            bytes_transferred=bytes_transferred,
            error_msg=reason,
        )

    # ── derived properties ────────────────────────────────────────────────────

    @property
    def ok(self) -> bool:
        """True if the transfer completed successfully."""
        return self.state is TransferState.COMPLETE

    @property
    def total_bytes(self) -> int:
        """Total bytes expected across all files."""
        return sum(f.size for f in self.files)

    @property
    def throughput_mbps(self) -> float:
        """Average throughput in megabits per second.

        Returns 0.0 if duration_s is zero.
        """
        if self.duration_s <= 0:
            return 0.0
        return (self.bytes_transferred * 8) / (self.duration_s * 1_000_000)

    def __str__(self) -> str:
        status = "OK" if self.ok else self.state.value.upper()
        mb = self.bytes_transferred / 1_048_576
        return (
            f"[{status}] {len(self.files)} file(s), "
            f"{mb:.1f} MB in {self.duration_s:.2f}s "
            f"({self.throughput_mbps:.1f} Mbit/s)"
        )
