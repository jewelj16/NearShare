"""Tests for TransferSessionBuilder — valid and invalid construction."""

import pytest

from engine.transfer.builder import BuilderError, TransferSessionBuilder
from engine.transfer.resume import ResumeToken
from engine.transfer.session import TransferSession
from engine.types import (
    DEFAULT_CHUNK_SIZE,
    FileMetadata,
    TransferDirection,
    TransferState,
)


# Helpers

def _file(name: str = "f.bin", chunks: int = 4) -> FileMetadata:
    return FileMetadata(name=name, size=DEFAULT_CHUNK_SIZE * chunks)


# Valid construction

class TestBuilderValid:
    """Builder produces correct session configs."""

    def test_minimal_build(self) -> None:
        config = (
            TransferSessionBuilder()
            .direction(TransferDirection.SEND)
            .add_file(_file())
            .build()
        )
        assert config["direction"] is TransferDirection.SEND
        assert len(config["files"]) == 1
        assert config["state"] is TransferState.IDLE
        assert isinstance(config["session_id"], str)

    def test_multi_file(self) -> None:
        config = (
            TransferSessionBuilder()
            .direction(TransferDirection.SEND)
            .add_file(_file("a"))
            .add_file(_file("b"))
            .add_file(_file("c"))
            .build()
        )
        assert len(config["files"]) == 3

    def test_add_files_batch(self) -> None:
        files = [_file("a"), _file("b")]
        config = (
            TransferSessionBuilder()
            .direction(TransferDirection.RECEIVE)
            .add_files(files)
            .build()
        )
        assert len(config["files"]) == 2

    def test_explicit_session_id(self) -> None:
        config = (
            TransferSessionBuilder()
            .session_id("my-id")
            .direction(TransferDirection.SEND)
            .add_file(_file())
            .build()
        )
        assert config["session_id"] == "my-id"

    def test_custom_initial_state(self) -> None:
        config = (
            TransferSessionBuilder()
            .direction(TransferDirection.SEND)
            .add_file(_file())
            .initial_state(TransferState.TRANSFERRING)
            .build()
        )
        assert config["state"] is TransferState.TRANSFERRING

    def test_from_resume_token(self) -> None:
        token = ResumeToken(
            transfer_id="resumed-sess",
            files=[_file("a"), _file("b")],
            ack_bitmaps={0: {0, 1}, 1: {0}},
        )
        config = (
            TransferSessionBuilder()
            .from_resume_token(token)
            .direction(TransferDirection.SEND)
            .build()
        )
        assert config["session_id"] == "resumed-sess"
        assert len(config["files"]) == 2
        assert config["ack_bitmaps"] == {0: {0, 1}, 1: {0}}

    def test_build_session_returns_transfer_session(self) -> None:
        sess = (
            TransferSessionBuilder()
            .direction(TransferDirection.SEND)
            .add_file(_file())
            .build_session()
        )
        assert isinstance(sess, TransferSession)
        assert sess.direction is TransferDirection.SEND

    def test_build_session_with_resume(self) -> None:
        token = ResumeToken(
            transfer_id="resume-test",
            files=[_file()],
            ack_bitmaps={0: {0, 1}},
        )
        sess = (
            TransferSessionBuilder()
            .from_resume_token(token)
            .direction(TransferDirection.RECEIVE)
            .build_session()
        )
        assert sess.session_id == "resume-test"
        assert sess.ack_bitmaps == {0: {0, 1}}


# Invalid construction

class TestBuilderInvalid:
    """Builder rejects invalid configurations."""

    def test_missing_direction(self) -> None:
        with pytest.raises(BuilderError, match="direction"):
            TransferSessionBuilder().add_file(_file()).build()

    def test_missing_files(self) -> None:
        with pytest.raises(BuilderError, match="file"):
            (
                TransferSessionBuilder()
                .direction(TransferDirection.SEND)
                .build()
            )

    def test_empty_files_list(self) -> None:
        with pytest.raises(BuilderError, match="file"):
            (
                TransferSessionBuilder()
                .direction(TransferDirection.SEND)
                .add_files([])
                .build()
            )

    def test_missing_direction_with_token(self) -> None:
        token = ResumeToken("s", [_file()], {})
        with pytest.raises(BuilderError, match="direction"):
            TransferSessionBuilder().from_resume_token(token).build()


# Fluent API chaining

class TestBuilderChaining:
    """Builder methods return self for chaining."""

    def test_all_methods_return_self(self) -> None:
        builder = TransferSessionBuilder()
        assert builder.session_id("x") is builder
        assert builder.direction(TransferDirection.SEND) is builder
        assert builder.add_file(_file()) is builder
        assert builder.add_files([_file()]) is builder
        assert builder.initial_state(TransferState.IDLE) is builder

    def test_from_resume_token_returns_self(self) -> None:
        builder = TransferSessionBuilder()
        token = ResumeToken("s", [_file()], {})
        assert builder.from_resume_token(token) is builder
