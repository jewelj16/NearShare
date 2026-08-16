"""Tests for CANCEL handling — both sides clean up partial files."""

import pytest

from engine.protocol.cancel import (
    CancelError,
    CancelHandler,
    build_cancel_payload,
    parse_cancel_payload,
)
from engine.types import DEFAULT_CHUNK_SIZE, FileMetadata


# Helpers

def _file(name: str, chunks: int = 4) -> FileMetadata:
    return FileMetadata(name=name, size=DEFAULT_CHUNK_SIZE * chunks)


# Payload round-trip

class TestCancelPayload:
    """CANCEL payload encode/decode."""

    def test_round_trip(self) -> None:
        tid = "cancel-session-1"
        reason = "User pressed cancel"
        payload = build_cancel_payload(tid, reason)
        parsed_tid, parsed_reason = parse_cancel_payload(payload)
        assert parsed_tid == tid
        assert parsed_reason == reason

    def test_empty_reason(self) -> None:
        payload = build_cancel_payload("sess-1")
        tid, reason = parse_cancel_payload(payload)
        assert tid == "sess-1"
        assert reason == ""

    def test_malformed_raises(self) -> None:
        with pytest.raises(CancelError):
            parse_cancel_payload(b"")


# CancelHandler

class TestCancelHandler:
    """Cancel workflow management."""

    def test_initial_state(self) -> None:
        handler = CancelHandler("sess-1", [_file("a")])
        assert not handler.cancelled
        assert not handler.cleanup_done
        assert handler.partial_files == []

    def test_cancel_marks_cancelled(self) -> None:
        handler = CancelHandler("sess-1", [_file("a")])
        handler.cancel()
        assert handler.cancelled

    def test_cancel_identifies_partial_files(self) -> None:
        files = [_file("a", 4), _file("b", 3)]
        handler = CancelHandler("sess-1", files)

        # file 0: partially received (2 of 4 chunks)
        # file 1: no chunks received
        bitmaps = {0: {0, 1}}
        partials = handler.cancel(bitmaps)

        assert len(partials) == 1
        assert partials[0].name == "a"

    def test_cancel_no_partials_when_nothing_received(self) -> None:
        files = [_file("a"), _file("b")]
        handler = CancelHandler("sess-1", files)
        partials = handler.cancel(ack_bitmaps={})
        assert partials == []

    def test_cancel_no_partials_when_complete(self) -> None:
        files = [_file("a", 2)]
        handler = CancelHandler("sess-1", files)
        bitmaps = {0: {0, 1}}  # fully received
        partials = handler.cancel(bitmaps)
        assert partials == []

    def test_cancel_both_partial(self) -> None:
        files = [_file("a", 3), _file("b", 2)]
        handler = CancelHandler("sess-1", files)
        bitmaps = {0: {0}, 1: {0}}  # both partial
        partials = handler.cancel(bitmaps)
        assert len(partials) == 2

    def test_mark_cleanup_done(self) -> None:
        handler = CancelHandler("sess-1", [_file("a")])
        handler.cancel({0: {0}})
        assert not handler.cleanup_done
        handler.mark_cleanup_done()
        assert handler.cleanup_done

    def test_transfer_id(self) -> None:
        handler = CancelHandler("my-session", [_file("a")])
        assert handler.transfer_id == "my-session"

    def test_cancel_without_bitmaps(self) -> None:
        handler = CancelHandler("sess-1", [_file("a")])
        partials = handler.cancel()
        assert partials == []
        assert handler.cancelled
