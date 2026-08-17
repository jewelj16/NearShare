"""Tests for resume-on-reconnect — resume sends only unacked chunks."""

import time

import pytest

from engine.transfer.resume import (
    ResumeExpiredError,
    ResumeManager,
    ResumeToken,
)
from engine.types import DEFAULT_CHUNK_SIZE, FileMetadata, RESUME_TIMEOUT_S


# Helpers

def _file(name: str = "f.bin", chunks: int = 4) -> FileMetadata:
    return FileMetadata(name=name, size=DEFAULT_CHUNK_SIZE * chunks)


# ResumeToken

class TestResumeToken:
    """ResumeToken tracks what still needs sending."""

    def test_chunks_to_send_all(self) -> None:
        token = ResumeToken(
            transfer_id="s",
            files=[_file(chunks=3)],
            ack_bitmaps={},
        )
        assert token.chunks_to_send(0) == {0, 1, 2}

    def test_chunks_to_send_partial(self) -> None:
        token = ResumeToken(
            transfer_id="s",
            files=[_file(chunks=5)],
            ack_bitmaps={0: {0, 2, 4}},
        )
        assert token.chunks_to_send(0) == {1, 3}

    def test_chunks_to_send_none(self) -> None:
        token = ResumeToken(
            transfer_id="s",
            files=[_file(chunks=3)],
            ack_bitmaps={0: {0, 1, 2}},
        )
        assert token.chunks_to_send(0) == set()

    def test_total_remaining(self) -> None:
        token = ResumeToken(
            transfer_id="s",
            files=[_file("a", 3), _file("b", 4)],
            ack_bitmaps={0: {0, 1}, 1: {0, 1, 2}},
        )
        # file 0: 1 remaining, file 1: 1 remaining
        assert token.total_remaining() == 2

    def test_total_remaining_zero(self) -> None:
        token = ResumeToken(
            transfer_id="s",
            files=[_file(chunks=2)],
            ack_bitmaps={0: {0, 1}},
        )
        assert token.total_remaining() == 0

    def test_out_of_range_raises(self) -> None:
        token = ResumeToken(
            transfer_id="s",
            files=[_file(chunks=2)],
            ack_bitmaps={},
        )
        with pytest.raises(IndexError):
            token.chunks_to_send(5)

    def test_not_expired_initially(self) -> None:
        token = ResumeToken(
            transfer_id="s",
            files=[_file()],
            ack_bitmaps={},
        )
        assert not token.is_expired

    def test_expired_after_timeout(self) -> None:
        token = ResumeToken(
            transfer_id="s",
            files=[_file()],
            ack_bitmaps={},
            timeout_s=0.0,
            created_at=time.monotonic() - 1.0,
        )
        assert token.is_expired

    def test_multi_file_resume(self) -> None:
        """Resume with multiple files — each has its own bitmap."""
        files = [_file("a", 3), _file("b", 2), _file("c", 4)]
        bitmaps = {
            0: {0, 1, 2},  # complete
            1: {0},         # 1 missing
            2: {0, 2},      # 2 missing
        }
        token = ResumeToken(
            transfer_id="multi",
            files=files,
            ack_bitmaps=bitmaps,
        )

        assert token.chunks_to_send(0) == set()
        assert token.chunks_to_send(1) == {1}
        assert token.chunks_to_send(2) == {1, 3}
        assert token.total_remaining() == 3


# ResumeManager

class TestResumeManager:
    """ResumeManager stores and retrieves tokens."""

    def test_save_and_get(self) -> None:
        mgr = ResumeManager()
        token = ResumeToken("s1", [_file()], {})
        mgr.save(token)
        assert mgr.get("s1") is token

    def test_get_nonexistent(self) -> None:
        mgr = ResumeManager()
        assert mgr.get("nope") is None

    def test_remove(self) -> None:
        mgr = ResumeManager()
        token = ResumeToken("s1", [_file()], {})
        mgr.save(token)
        mgr.remove("s1")
        assert mgr.get("s1") is None

    def test_remove_nonexistent_is_noop(self) -> None:
        mgr = ResumeManager()
        mgr.remove("nope")  # should not raise

    def test_expired_token_auto_removed(self) -> None:
        mgr = ResumeManager()
        token = ResumeToken(
            "s1", [_file()], {},
            timeout_s=0.0,
            created_at=time.monotonic() - 1.0,
        )
        mgr.save(token)
        assert mgr.get("s1") is None

    def test_cleanup_expired(self) -> None:
        mgr = ResumeManager()
        expired = ResumeToken(
            "expired", [_file()], {},
            timeout_s=0.0,
            created_at=time.monotonic() - 1.0,
        )
        active = ResumeToken("active", [_file()], {})
        mgr.save(expired)
        mgr.save(active)

        removed = mgr.cleanup_expired()
        assert removed == 1
        assert mgr.get("active") is active

    def test_active_count(self) -> None:
        mgr = ResumeManager()
        mgr.save(ResumeToken("s1", [_file()], {}))
        mgr.save(ResumeToken("s2", [_file()], {}))
        assert mgr.active_count == 2

    def test_overwrite_token(self) -> None:
        mgr = ResumeManager()
        t1 = ResumeToken("s1", [_file()], {0: {0}})
        t2 = ResumeToken("s1", [_file()], {0: {0, 1}})
        mgr.save(t1)
        mgr.save(t2)
        assert mgr.get("s1") is t2
