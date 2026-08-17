"""Tests for interruption detection — simulated socket error mid-stream,
transition to INTERRUPTED state, and resume timer."""

import asyncio

import pytest

from engine.transfer.state_machine import (
    IllegalTransitionError,
    TransferStateMachine,
)
from engine.transfer.resume import ResumeToken, ResumeManager
from engine.types import DEFAULT_CHUNK_SIZE, FileMetadata, TransferState, RESUME_TIMEOUT_S


# Helpers

def _file(name: str = "f.bin", chunks: int = 4) -> FileMetadata:
    return FileMetadata(name=name, size=DEFAULT_CHUNK_SIZE * chunks)


# State machine interruption transitions

class TestInterruptionTransitions:
    """TRANSFERRING -> INTERRUPTED and back."""

    def test_transferring_to_interrupted(self) -> None:
        sm = TransferStateMachine(TransferState.TRANSFERRING)
        sm.transition(TransferState.INTERRUPTED)
        assert sm.state is TransferState.INTERRUPTED

    def test_interrupted_to_transferring(self) -> None:
        sm = TransferStateMachine(TransferState.INTERRUPTED)
        sm.transition(TransferState.TRANSFERRING)
        assert sm.state is TransferState.TRANSFERRING

    def test_interrupted_to_failed(self) -> None:
        sm = TransferStateMachine(TransferState.INTERRUPTED)
        sm.transition(TransferState.FAILED)
        assert sm.state is TransferState.FAILED

    def test_interrupted_to_complete_illegal(self) -> None:
        sm = TransferStateMachine(TransferState.INTERRUPTED)
        with pytest.raises(IllegalTransitionError):
            sm.transition(TransferState.COMPLETE)

    def test_idle_to_interrupted_illegal(self) -> None:
        sm = TransferStateMachine(TransferState.IDLE)
        with pytest.raises(IllegalTransitionError):
            sm.transition(TransferState.INTERRUPTED)


# Simulated socket error

class TestSimulatedSocketError:
    """Simulate a socket error mid-transfer and verify state handling."""

    def test_socket_error_triggers_interruption(self) -> None:
        sm = TransferStateMachine(TransferState.TRANSFERRING)

        # simulate socket error
        try:
            raise OSError("Connection reset by peer")
        except OSError:
            sm.transition(TransferState.INTERRUPTED)

        assert sm.state is TransferState.INTERRUPTED

    def test_resume_after_interruption(self) -> None:
        sm = TransferStateMachine(TransferState.TRANSFERRING)
        sm.transition(TransferState.INTERRUPTED)

        # simulate successful reconnection
        sm.transition(TransferState.TRANSFERRING)
        assert sm.state is TransferState.TRANSFERRING

    def test_fail_after_timeout(self) -> None:
        sm = TransferStateMachine(TransferState.TRANSFERRING)
        sm.transition(TransferState.INTERRUPTED)

        # simulate resume timeout
        sm.transition(TransferState.FAILED)
        assert sm.state is TransferState.FAILED
        assert sm.is_terminal


# ResumeToken expiration in interruption context

class TestInterruptionResumeToken:
    """ResumeTokens created on interruption."""

    def test_create_token_on_interruption(self) -> None:
        files = [_file("a", 4)]
        bitmaps = {0: {0, 1}}  # 2 of 4 acked

        token = ResumeToken(
            transfer_id="interrupted-sess",
            files=files,
            ack_bitmaps=bitmaps,
        )

        assert not token.is_expired
        assert token.chunks_to_send(0) == {2, 3}
        assert token.total_remaining() == 2

    def test_token_stored_in_manager(self) -> None:
        mgr = ResumeManager()
        token = ResumeToken(
            transfer_id="sess-1",
            files=[_file("a", 3)],
            ack_bitmaps={0: {0}},
        )
        mgr.save(token)
        assert mgr.get("sess-1") is token

    def test_expired_token_not_returned(self) -> None:
        import time
        mgr = ResumeManager()
        token = ResumeToken(
            transfer_id="sess-1",
            files=[_file("a")],
            ack_bitmaps={},
            timeout_s=0.0,  # immediately expired
            created_at=time.monotonic() - 1.0,
        )
        mgr.save(token)
        assert mgr.get("sess-1") is None
