"""Transfer state machine — enforces legal state transitions.

Implements the state transition diagram from protocol.md §5.
Only transitions defined in _TRANSITIONS are allowed; all others
raise IllegalTransitionError.
"""

from __future__ import annotations

from engine.types import TransferState


class IllegalTransitionError(Exception):
    """Raised when a state transition is not allowed."""

    def __init__(self, from_state: TransferState, to_state: TransferState) -> None:
        self.from_state = from_state
        self.to_state = to_state
        super().__init__(
            f"Illegal transition: {from_state.value} -> {to_state.value}"
        )


# legal transitions (from protocol.md §5)
_TRANSITIONS: dict[TransferState, set[TransferState]] = {
    TransferState.IDLE: {
        TransferState.CONNECTING,
    },
    TransferState.CONNECTING: {
        TransferState.HANDSHAKING,
        TransferState.FAILED,
    },
    TransferState.HANDSHAKING: {
        TransferState.METADATA_SENT,
        TransferState.FAILED,
    },
    TransferState.METADATA_SENT: {
        TransferState.AWAITING_ACCEPT,
        TransferState.FAILED,
    },
    TransferState.AWAITING_ACCEPT: {
        TransferState.TRANSFERRING,
        TransferState.FAILED,
    },
    TransferState.TRANSFERRING: {
        TransferState.COMPLETE,
        TransferState.INTERRUPTED,
    },
    TransferState.INTERRUPTED: {
        TransferState.TRANSFERRING,
        TransferState.FAILED,
    },
    TransferState.COMPLETE: set(),  # terminal
    TransferState.FAILED: set(),    # terminal
}


class TransferStateMachine:
    """Enforces the transfer state machine transitions.

    Attributes:
        state: The current state.
    """

    def __init__(self, initial: TransferState = TransferState.IDLE) -> None:
        self._state = initial

    @property
    def state(self) -> TransferState:
        return self._state

    def transition(self, to: TransferState) -> None:
        """Move to a new state if the transition is legal.

        Args:
            to: The target state.

        Raises:
            IllegalTransitionError: If the transition is not allowed.
        """
        allowed = _TRANSITIONS.get(self._state, set())
        if to not in allowed:
            raise IllegalTransitionError(self._state, to)
        self._state = to

    def can_transition(self, to: TransferState) -> bool:
        """Check if a transition to *to* would be legal without performing it."""
        return to in _TRANSITIONS.get(self._state, set())

    @property
    def is_terminal(self) -> bool:
        """True if the current state has no outgoing transitions."""
        return len(_TRANSITIONS.get(self._state, set())) == 0

    @staticmethod
    def legal_transitions() -> dict[TransferState, set[TransferState]]:
        """Return a copy of the full transition table (useful for testing)."""
        return {k: set(v) for k, v in _TRANSITIONS.items()}
