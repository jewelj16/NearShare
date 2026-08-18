"""Tests for engine.protocol.ack_msg — ACK (0x07) payload round-trips."""

import pytest

from engine.protocol.ack_msg import (
    AckMsgError,
    build_ack_msg_payload,
    parse_ack_msg_payload,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _round_trip(
    transfer_id: str, file_index: int, acked_seqs: set[int],
) -> tuple[str, int, set[int]]:
    payload = build_ack_msg_payload(transfer_id, file_index, acked_seqs)
    return parse_ack_msg_payload(payload)


# ── round-trip tests ──────────────────────────────────────────────────────────

class TestAckMsgRoundTrip:
    """build → parse must yield the original values."""

    def test_single_seq(self) -> None:
        tid, fi, seqs = _round_trip("sess-1", 0, {0})
        assert tid == "sess-1"
        assert fi == 0
        assert seqs == {0}

    def test_multiple_seqs(self) -> None:
        tid, fi, seqs = _round_trip("sess-2", 3, {0, 1, 2, 5, 9})
        assert tid == "sess-2"
        assert fi == 3
        assert seqs == {0, 1, 2, 5, 9}

    def test_empty_seqs(self) -> None:
        """Empty ACK (no seqs) should round-trip cleanly."""
        tid, fi, seqs = _round_trip("sess-3", 0, set())
        assert tid == "sess-3"
        assert fi == 0
        assert seqs == set()

    def test_large_seq_numbers(self) -> None:
        big_seqs = {100_000, 200_000, 4_294_967_295}  # max uint32
        tid, fi, seqs = _round_trip("big", 1, big_seqs)
        assert seqs == big_seqs

    def test_high_file_index(self) -> None:
        tid, fi, seqs = _round_trip("hi", 65535, {0})  # max uint16
        assert fi == 65535

    def test_unicode_transfer_id(self) -> None:
        tid, fi, seqs = _round_trip("sési\u00f6n-\u00e9", 0, {1, 2})
        assert tid == "sési\u00f6n-\u00e9"

    def test_seqs_order_independent(self) -> None:
        """The set is unordered; wire format sorts them."""
        payload1 = build_ack_msg_payload("s", 0, {5, 3, 1})
        payload2 = build_ack_msg_payload("s", 0, {1, 3, 5})
        assert payload1 == payload2

    def test_many_seqs(self) -> None:
        seqs = set(range(1000))
        tid, fi, result = _round_trip("bulk", 0, seqs)
        assert result == seqs


# ── payload size ──────────────────────────────────────────────────────────────

class TestAckMsgPayloadSize:
    """Verify payload length formula."""

    def test_payload_length(self) -> None:
        tid = "sess"
        fi = 0
        seqs = {0, 1, 2}
        payload = build_ack_msg_payload(tid, fi, seqs)
        # 2 (tid len) + 4 (tid utf8) + 2 (fi) + 4 (count) + 3*4 (seqs)
        expected = 2 + len(tid.encode()) + 2 + 4 + 3 * 4
        assert len(payload) == expected


# ── malformed-frame rejection ─────────────────────────────────────────────────

class TestAckMsgMalformed:
    """parse_ack_msg_payload rejects truncated payloads."""

    def test_empty_payload(self) -> None:
        with pytest.raises(AckMsgError):
            parse_ack_msg_payload(b"")

    def test_truncated_file_index(self) -> None:
        # valid transfer_id but missing file_index
        good = build_ack_msg_payload("x", 0, {0})
        # cut after the transfer_id string
        tid_end = 2 + len("x".encode())
        with pytest.raises(AckMsgError):
            parse_ack_msg_payload(good[:tid_end])

    def test_truncated_acked_count(self) -> None:
        good = build_ack_msg_payload("x", 0, {0})
        tid_end = 2 + len("x".encode())
        # include file_index (2 bytes) but no acked_count
        with pytest.raises(AckMsgError):
            parse_ack_msg_payload(good[: tid_end + 2])

    def test_truncated_seq_values(self) -> None:
        good = build_ack_msg_payload("x", 0, {0, 1, 2})
        # chop off the last seq
        with pytest.raises(AckMsgError):
            parse_ack_msg_payload(good[:-4])

    def test_truncated_mid_transfer_id(self) -> None:
        good = build_ack_msg_payload("hello-world", 0, {0})
        with pytest.raises(AckMsgError):
            parse_ack_msg_payload(good[:3])
