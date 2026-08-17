"""Tests for engine.protocol.transfer_start — TRANSFER_START payload round-trips."""

import pytest

from engine.protocol.transfer_start import (
    TransferStartError,
    build_transfer_start_payload,
    parse_transfer_start_payload,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _round_trip(transfer_id: str) -> str:
    return parse_transfer_start_payload(build_transfer_start_payload(transfer_id))


# ── round-trip tests ──────────────────────────────────────────────────────────

class TestTransferStartRoundTrip:
    """build → parse must yield the original transfer_id."""

    def test_uuid_style(self) -> None:
        tid = "550e8400-e29b-41d4-a716-446655440000"
        assert _round_trip(tid) == tid

    def test_short_id(self) -> None:
        assert _round_trip("abc") == "abc"

    def test_empty_string(self) -> None:
        """Empty transfer_id is unusual but must not crash."""
        assert _round_trip("") == ""

    def test_long_id(self) -> None:
        tid = "x" * 200
        assert _round_trip(tid) == tid

    def test_unicode_id(self) -> None:
        tid = "session-\u00e9l\u00e8ve"
        assert _round_trip(tid) == tid

    def test_payload_length(self) -> None:
        """Payload should be exactly 2 + len(utf-8 bytes) bytes."""
        tid = "hello"
        payload = build_transfer_start_payload(tid)
        assert len(payload) == 2 + len(tid.encode("utf-8"))


# ── malformed-frame rejection ─────────────────────────────────────────────────

class TestTransferStartMalformed:
    """parse_transfer_start_payload rejects truncated payloads."""

    def test_empty_payload(self) -> None:
        with pytest.raises(TransferStartError):
            parse_transfer_start_payload(b"")

    def test_length_only_no_data(self) -> None:
        # length prefix says 5 bytes but payload is empty after header
        with pytest.raises(TransferStartError):
            parse_transfer_start_payload(b"\x00\x05")

    def test_truncated_mid_string(self) -> None:
        payload = build_transfer_start_payload("hello-world")
        # trim the last 3 bytes so the string is incomplete
        with pytest.raises(TransferStartError):
            parse_transfer_start_payload(payload[:-3])
