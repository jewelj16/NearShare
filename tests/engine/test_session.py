"""Unit tests for engine.transfer.session — TransferSession construction,
ack bookkeeping, completion checks, and missing-chunk reporting."""

import uuid

import pytest

from engine.types import (
    DEFAULT_CHUNK_SIZE,
    DeviceId,
    FileMetadata,
    TransferDirection,
    TransferState,
)
from engine.transfer.session import TransferSession


# Helpers

def _file(name: str = "f.bin", size: int = DEFAULT_CHUNK_SIZE * 4) -> FileMetadata:
    """Shortcut to build a FileMetadata with predictable chunk_count."""
    return FileMetadata(name=name, size=size)


def _session(
    files: list[FileMetadata] | None = None,
    direction: TransferDirection = TransferDirection.SEND,
) -> TransferSession:
    """Build a TransferSession via the factory method."""
    return TransferSession.create(
        direction=direction,
        files=files if files is not None else [_file()],
    )


# Construction

class TestTransferSessionConstruction:
    """Basic construction and factory method."""

    def test_create_generates_uuid(self) -> None:
        s = _session()
        # Should be valid UUID-4
        parsed = uuid.UUID(s.session_id)
        assert parsed.version == 4

    def test_create_sets_direction(self) -> None:
        s = _session(direction=TransferDirection.RECEIVE)
        assert s.direction is TransferDirection.RECEIVE

    def test_create_defaults_to_idle(self) -> None:
        s = _session()
        assert s.state is TransferState.IDLE

    def test_create_empty_ack_bitmaps(self) -> None:
        s = _session()
        assert s.ack_bitmaps == {}

    def test_files_stored(self) -> None:
        f1 = _file("a.txt", 100)
        f2 = _file("b.txt", 200)
        s = _session(files=[f1, f2])
        assert s.files == [f1, f2]

    def test_state_is_mutable(self) -> None:
        s = _session()
        s.state = TransferState.TRANSFERRING
        assert s.state is TransferState.TRANSFERRING

    def test_direct_construction(self) -> None:
        s = TransferSession(
            session_id="custom-id",
            direction=TransferDirection.SEND,
            files=[_file()],
        )
        assert s.session_id == "custom-id"


# Ack bookkeeping

class TestAckBitmaps:
    """init_ack_bitmaps, ack_chunk, and related helpers."""

    def test_init_ack_bitmaps(self) -> None:
        s = _session(files=[_file("a"), _file("b")])
        s.init_ack_bitmaps()
        assert s.ack_bitmaps == {0: set(), 1: set()}

    def test_ack_chunk_creates_set_lazily(self) -> None:
        s = _session()
        s.ack_chunk(0, 2)
        assert 2 in s.ack_bitmaps[0]

    def test_ack_chunk_idempotent(self) -> None:
        s = _session()
        s.ack_chunk(0, 2)
        s.ack_chunk(0, 2)
        assert s.ack_bitmaps[0] == {2}

    def test_ack_multiple_chunks(self) -> None:
        s = _session()
        for seq in range(4):
            s.ack_chunk(0, seq)
        assert s.ack_bitmaps[0] == {0, 1, 2, 3}


# Completion checks

class TestCompletion:
    """is_file_complete, is_transfer_complete."""

    def test_file_not_complete_initially(self) -> None:
        s = _session()
        assert not s.is_file_complete(0)

    def test_file_complete_when_all_acked(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 3)  # 3 chunks
        s = _session(files=[f])
        for seq in range(3):
            s.ack_chunk(0, seq)
        assert s.is_file_complete(0)

    def test_transfer_not_complete_with_missing_file(self) -> None:
        f1 = _file("a", DEFAULT_CHUNK_SIZE * 2)
        f2 = _file("b", DEFAULT_CHUNK_SIZE * 2)
        s = _session(files=[f1, f2])
        # Ack all of file 0 but not file 1
        for seq in range(2):
            s.ack_chunk(0, seq)
        assert not s.is_transfer_complete()

    def test_transfer_complete_when_all_files_done(self) -> None:
        f1 = _file("a", DEFAULT_CHUNK_SIZE * 2)
        f2 = _file("b", DEFAULT_CHUNK_SIZE * 2)
        s = _session(files=[f1, f2])
        for fi in range(2):
            for seq in range(2):
                s.ack_chunk(fi, seq)
        assert s.is_transfer_complete()

    def test_is_file_complete_raises_on_bad_index(self) -> None:
        s = _session(files=[_file()])
        with pytest.raises(IndexError):
            s.is_file_complete(5)

    def test_is_file_complete_raises_on_negative_index(self) -> None:
        s = _session(files=[_file()])
        with pytest.raises(IndexError):
            s.is_file_complete(-1)


# Missing chunks

class TestMissingChunks:
    """missing_chunks reports the un-acked sequence numbers."""

    def test_all_missing_initially(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 3)
        s = _session(files=[f])
        assert s.missing_chunks(0) == {0, 1, 2}

    def test_partial_ack(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 4)
        s = _session(files=[f])
        s.ack_chunk(0, 0)
        s.ack_chunk(0, 2)
        assert s.missing_chunks(0) == {1, 3}

    def test_none_missing_when_complete(self) -> None:
        f = _file(size=DEFAULT_CHUNK_SIZE * 2)
        s = _session(files=[f])
        s.ack_chunk(0, 0)
        s.ack_chunk(0, 1)
        assert s.missing_chunks(0) == set()

    def test_raises_on_bad_index(self) -> None:
        s = _session(files=[_file()])
        with pytest.raises(IndexError):
            s.missing_chunks(99)

    def test_raises_on_negative_index(self) -> None:
        s = _session(files=[_file()])
        with pytest.raises(IndexError):
            s.missing_chunks(-1)


# Enum coverage within session context

class TestSessionStateTransitions:
    """Verify that session state can be set to every TransferState value."""

    def test_all_states_assignable(self) -> None:
        s = _session()
        for state in TransferState:
            s.state = state
            assert s.state is state
