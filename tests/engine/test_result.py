"""Tests for engine.transfer.result — TransferResult dataclass."""

import pytest

from engine.transfer.result import TransferResult
from engine.types import DEFAULT_CHUNK_SIZE, FileMetadata, TransferState


# ── helpers ───────────────────────────────────────────────────────────────────

def _file(name: str = "a.bin", size: int = 1_048_576) -> FileMetadata:
    return FileMetadata(name=name, size=size)


# ── convenience constructors ──────────────────────────────────────────────────

class TestConvenienceConstructors:
    """success(), failure(), cancelled() produce the right state."""

    def test_success(self) -> None:
        r = TransferResult.success("s1", [_file()], 1.0, 1_048_576)
        assert r.ok is True
        assert r.state is TransferState.COMPLETE
        assert r.error_msg is None

    def test_failure(self) -> None:
        r = TransferResult.failure("s1", [_file()], 0.5, 0, "connection reset")
        assert r.ok is False
        assert r.state is TransferState.FAILED
        assert r.error_msg == "connection reset"

    def test_cancelled(self) -> None:
        r = TransferResult.cancelled("s1", [_file()], 2.0, 512_000)
        assert r.ok is False
        assert r.state is TransferState.INTERRUPTED
        assert r.error_msg == "Transfer cancelled"

    def test_cancelled_custom_reason(self) -> None:
        r = TransferResult.cancelled("s1", [_file()], 1.0, 0, reason="User pressed Ctrl-C")
        assert r.error_msg == "User pressed Ctrl-C"

    def test_files_stored_as_tuple(self) -> None:
        files = [_file("a"), _file("b"), _file("c")]
        r = TransferResult.success("s", files, 1.0, 0)
        assert isinstance(r.files, tuple)
        assert len(r.files) == 3

    def test_frozen(self) -> None:
        r = TransferResult.success("s", [_file()], 1.0, 100)
        with pytest.raises((AttributeError, TypeError)):
            r.bytes_transferred = 999  # type: ignore[misc]


# ── derived properties ────────────────────────────────────────────────────────

class TestDerivedProperties:
    """ok, total_bytes, throughput_mbps."""

    def test_total_bytes(self) -> None:
        files = [_file("a", 1_000_000), _file("b", 500_000)]
        r = TransferResult.success("s", files, 1.0, 1_500_000)
        assert r.total_bytes == 1_500_000

    def test_throughput_1mbit(self) -> None:
        # 1 MiB in 8 seconds: (1_048_576 * 8) / (8 * 1_000_000) = 1.048576 Mbit/s
        r = TransferResult.success("s", [_file(size=1_048_576)], 8.0, 1_048_576)
        assert r.throughput_mbps == pytest.approx(1.048576, rel=0.001)

    def test_throughput_zero_duration(self) -> None:
        r = TransferResult.success("s", [_file()], 0.0, 1_000)
        assert r.throughput_mbps == 0.0

    def test_ok_false_for_failure(self) -> None:
        r = TransferResult.failure("s", [_file()], 0.0, 0, "err")
        assert r.ok is False

    def test_ok_false_for_interrupted(self) -> None:
        r = TransferResult.cancelled("s", [_file()], 0.0, 0)
        assert r.ok is False


# ── __str__ ───────────────────────────────────────────────────────────────────

class TestStr:
    """String representation is human-readable."""

    def test_success_str_contains_ok(self) -> None:
        r = TransferResult.success("s", [_file()], 1.0, 1_048_576)
        assert "OK" in str(r)

    def test_failure_str_contains_state(self) -> None:
        r = TransferResult.failure("s", [_file()], 0.5, 0, "err")
        assert "FAILED" in str(r)

    def test_str_contains_file_count(self) -> None:
        r = TransferResult.success("s", [_file("a"), _file("b")], 1.0, 0)
        assert "2 file" in str(r)


# ── direct construction ───────────────────────────────────────────────────────

class TestDirectConstruction:
    """TransferResult can also be built directly."""

    def test_direct(self) -> None:
        r = TransferResult(
            session_id="direct-1",
            files=(_file(),),
            state=TransferState.COMPLETE,
            duration_s=2.5,
            bytes_transferred=512,
        )
        assert r.session_id == "direct-1"
        assert r.duration_s == 2.5
