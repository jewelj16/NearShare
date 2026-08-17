"""TransferSessionBuilder — Builder pattern for session construction.

Provides a fluent API for constructing TransferSession instances,
supporting multi-file and resume-token workflows.
"""

from __future__ import annotations

import uuid
from typing import Sequence

from engine.transfer.resume import ResumeToken
from engine.types import (
    FileMetadata,
    TransferDirection,
    TransferState,
)


class BuilderError(Exception):
    """Raised when the builder is in an invalid state for build()."""


class TransferSessionBuilder:
    """Fluent builder for TransferSession instances.

    Usage::

        session = (
            TransferSessionBuilder()
            .direction(TransferDirection.SEND)
            .add_file(FileMetadata(name="a.txt", size=1024))
            .add_file(FileMetadata(name="b.txt", size=2048))
            .build()
        )

    For resuming an interrupted session::

        session = (
            TransferSessionBuilder()
            .from_resume_token(token)
            .build()
        )
    """

    def __init__(self) -> None:
        self._session_id: str | None = None
        self._direction: TransferDirection | None = None
        self._files: list[FileMetadata] = []
        self._state: TransferState = TransferState.IDLE
        self._ack_bitmaps: dict[int, set[int]] | None = None
        self._from_token: bool = False

    def session_id(self, sid: str) -> TransferSessionBuilder:
        """Set the session ID explicitly (otherwise auto-generated)."""
        self._session_id = sid
        return self

    def direction(self, d: TransferDirection) -> TransferSessionBuilder:
        """Set the transfer direction."""
        self._direction = d
        return self

    def add_file(self, f: FileMetadata) -> TransferSessionBuilder:
        """Add a file to the transfer."""
        self._files.append(f)
        return self

    def add_files(self, files: Sequence[FileMetadata]) -> TransferSessionBuilder:
        """Add multiple files at once."""
        self._files.extend(files)
        return self

    def initial_state(self, state: TransferState) -> TransferSessionBuilder:
        """Override the initial state (default IDLE)."""
        self._state = state
        return self

    def from_resume_token(self, token: ResumeToken) -> TransferSessionBuilder:
        """Populate the builder from a ResumeToken.

        Sets session_id, files, and ack_bitmaps from the token.
        The direction must still be set separately.
        """
        self._session_id = token.transfer_id
        self._files = list(token.files)
        self._ack_bitmaps = {
            k: set(v) for k, v in token.ack_bitmaps.items()
        }
        self._from_token = True
        return self

    def build(self) -> dict:
        """Build and return a session configuration dict.

        The dict contains all fields needed to construct a TransferSession.
        We return a dict rather than importing TransferSession to avoid
        circular imports (session.py imports from types.py).

        Returns:
            Dict with keys: session_id, direction, files, state, ack_bitmaps.

        Raises:
            BuilderError: If required fields are missing.
        """
        if self._direction is None:
            raise BuilderError("direction is required")
        if not self._files:
            raise BuilderError("at least one file is required")

        sid = self._session_id or str(uuid.uuid4())
        ack_bitmaps = self._ack_bitmaps or {}

        return {
            "session_id": sid,
            "direction": self._direction,
            "files": list(self._files),
            "state": self._state,
            "ack_bitmaps": ack_bitmaps,
        }

    def build_session(self):
        """Build and return a TransferSession directly.

        Returns:
            A fully constructed TransferSession.

        Raises:
            BuilderError: If required fields are missing.
        """
        # import here to avoid circular dependency
        from engine.transfer.session import TransferSession

        config = self.build()
        return TransferSession(
            session_id=config["session_id"],
            direction=config["direction"],
            files=config["files"],
            state=config["state"],
            ack_bitmaps=config["ack_bitmaps"],
        )
