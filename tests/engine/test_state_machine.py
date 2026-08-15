"""Tests for TransferStateMachine — legal and illegal transitions."""

import pytest

from engine.transfer.state_machine import (
    IllegalTransitionError,
    TransferStateMachine,
)
from engine.types import TransferState


# legal transitions

class TestLegalTransitions:
    """Every legal transition in the protocol state diagram should work."""

    def test_full_happy_path(self) -> None:
        """IDLE -> CONNECTING -> HANDSHAKING -> METADATA_SENT ->
        AWAITING_ACCEPT -> TRANSFERRING -> COMPLETE"""
        sm = TransferStateMachine()
        assert sm.state is TransferState.IDLE

        sm.transition(TransferState.CONNECTING)
        assert sm.state is TransferState.CONNECTING

        sm.transition(TransferState.HANDSHAKING)
        assert sm.state is TransferState.HANDSHAKING

        sm.transition(TransferState.METADATA_SENT)
        assert sm.state is TransferState.METADATA_SENT

        sm.transition(TransferState.AWAITING_ACCEPT)
        assert sm.state is TransferState.AWAITING_ACCEPT

        sm.transition(TransferState.TRANSFERRING)
        assert sm.state is TransferState.TRANSFERRING

        sm.transition(TransferState.COMPLETE)
        assert sm.state is TransferState.COMPLETE

    def test_connecting_to_failed(self) -> None:
        sm = TransferStateMachine(TransferState.CONNECTING)
        sm.transition(TransferState.FAILED)
        assert sm.state is TransferState.FAILED

    def test_transferring_to_interrupted(self) -> None:
        sm = TransferStateMachine(TransferState.TRANSFERRING)
        sm.transition(TransferState.INTERRUPTED)
        assert sm.state is TransferState.INTERRUPTED

    def test_interrupted_to_transferring_resume(self) -> None:
        sm = TransferStateMachine(TransferState.INTERRUPTED)
        sm.transition(TransferState.TRANSFERRING)
        assert sm.state is TransferState.TRANSFERRING

    def test_interrupted_to_failed_timeout(self) -> None:
        sm = TransferStateMachine(TransferState.INTERRUPTED)
        sm.transition(TransferState.FAILED)
        assert sm.state is TransferState.FAILED

    def test_handshaking_to_failed(self) -> None:
        sm = TransferStateMachine(TransferState.HANDSHAKING)
        sm.transition(TransferState.FAILED)
        assert sm.state is TransferState.FAILED

    def test_metadata_sent_to_failed(self) -> None:
        sm = TransferStateMachine(TransferState.METADATA_SENT)
        sm.transition(TransferState.FAILED)
        assert sm.state is TransferState.FAILED

    def test_awaiting_accept_to_failed(self) -> None:
        sm = TransferStateMachine(TransferState.AWAITING_ACCEPT)
        sm.transition(TransferState.FAILED)
        assert sm.state is TransferState.FAILED

    def test_all_legal_transitions_work(self) -> None:
        """Programmatically verify every entry in the transition table."""
        table = TransferStateMachine.legal_transitions()
        for from_state, targets in table.items():
            for to_state in targets:
                sm = TransferStateMachine(from_state)
                sm.transition(to_state)
                assert sm.state is to_state


# illegal transitions

class TestIllegalTransitions:
    """Transitions not in the table must raise IllegalTransitionError."""

    def test_idle_to_transferring(self) -> None:
        sm = TransferStateMachine()
        with pytest.raises(IllegalTransitionError) as exc:
            sm.transition(TransferState.TRANSFERRING)
        assert exc.value.from_state is TransferState.IDLE
        assert exc.value.to_state is TransferState.TRANSFERRING

    def test_complete_to_anything(self) -> None:
        """COMPLETE is terminal — no transitions allowed."""
        sm = TransferStateMachine(TransferState.COMPLETE)
        for target in TransferState:
            if target is TransferState.COMPLETE:
                continue
            with pytest.raises(IllegalTransitionError):
                sm.transition(target)

    def test_failed_to_anything(self) -> None:
        """FAILED is terminal — no transitions allowed."""
        sm = TransferStateMachine(TransferState.FAILED)
        for target in TransferState:
            if target is TransferState.FAILED:
                continue
            with pytest.raises(IllegalTransitionError):
                sm.transition(target)

    def test_idle_to_complete(self) -> None:
        sm = TransferStateMachine()
        with pytest.raises(IllegalTransitionError):
            sm.transition(TransferState.COMPLETE)

    def test_connecting_to_transferring(self) -> None:
        sm = TransferStateMachine(TransferState.CONNECTING)
        with pytest.raises(IllegalTransitionError):
            sm.transition(TransferState.TRANSFERRING)

    def test_transferring_to_idle(self) -> None:
        sm = TransferStateMachine(TransferState.TRANSFERRING)
        with pytest.raises(IllegalTransitionError):
            sm.transition(TransferState.IDLE)


# helper methods

class TestStateMachineHelpers:
    """can_transition, is_terminal, and legal_transitions."""

    def test_can_transition_true(self) -> None:
        sm = TransferStateMachine()
        assert sm.can_transition(TransferState.CONNECTING) is True

    def test_can_transition_false(self) -> None:
        sm = TransferStateMachine()
        assert sm.can_transition(TransferState.COMPLETE) is False

    def test_is_terminal_complete(self) -> None:
        sm = TransferStateMachine(TransferState.COMPLETE)
        assert sm.is_terminal is True

    def test_is_terminal_failed(self) -> None:
        sm = TransferStateMachine(TransferState.FAILED)
        assert sm.is_terminal is True

    def test_is_not_terminal_idle(self) -> None:
        sm = TransferStateMachine()
        assert sm.is_terminal is False

    def test_legal_transitions_covers_all_states(self) -> None:
        table = TransferStateMachine.legal_transitions()
        for state in TransferState:
            assert state in table

    def test_initial_state_default(self) -> None:
        sm = TransferStateMachine()
        assert sm.state is TransferState.IDLE

    def test_initial_state_custom(self) -> None:
        sm = TransferStateMachine(TransferState.TRANSFERRING)
        assert sm.state is TransferState.TRANSFERRING
