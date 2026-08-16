"""Tests for structured logging — session_id present in log records."""

import logging

import pytest

from engine.logging_.setup import (
    SessionFilter,
    get_logger,
    get_session_id,
    set_session_id,
    setup_engine_logging,
)


class TestSessionContext:
    """session_id context variable management."""

    def test_default_is_none(self) -> None:
        set_session_id(None)
        assert get_session_id() is None

    def test_set_and_get(self) -> None:
        set_session_id("sess-123")
        assert get_session_id() == "sess-123"
        set_session_id(None)  # cleanup

    def test_clear(self) -> None:
        set_session_id("sess-456")
        set_session_id(None)
        assert get_session_id() is None


class TestSessionFilter:
    """SessionFilter injects session_id into log records."""

    def test_adds_session_id(self) -> None:
        set_session_id("test-session")
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="hello",
            args=None, exc_info=None,
        )
        filt = SessionFilter()
        filt.filter(record)
        assert record.session_id == "test-session"  # type: ignore[attr-defined]
        set_session_id(None)

    def test_dash_when_no_session(self) -> None:
        set_session_id(None)
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="hello",
            args=None, exc_info=None,
        )
        filt = SessionFilter()
        filt.filter(record)
        assert record.session_id == "-"  # type: ignore[attr-defined]

    def test_always_returns_true(self) -> None:
        record = logging.LogRecord(
            name="test", level=logging.INFO,
            pathname="", lineno=0, msg="hello",
            args=None, exc_info=None,
        )
        filt = SessionFilter()
        assert filt.filter(record) is True


class TestSetupEngineLogging:
    """setup_engine_logging configures the logger correctly."""

    def setup_method(self) -> None:
        # reset the logger between tests
        logger = logging.getLogger("nearshare.engine")
        logger.handlers.clear()
        logger.filters.clear()

    def test_returns_logger(self) -> None:
        logger = setup_engine_logging()
        assert logger.name == "nearshare.engine"

    def test_has_handler(self) -> None:
        logger = setup_engine_logging()
        assert len(logger.handlers) >= 1

    def test_has_session_filter(self) -> None:
        logger = setup_engine_logging()
        assert any(isinstance(f, SessionFilter) for f in logger.filters)

    def test_idempotent_handlers(self) -> None:
        setup_engine_logging()
        setup_engine_logging()
        logger = logging.getLogger("nearshare.engine")
        assert len(logger.handlers) == 1

    def test_session_id_in_output(self, caplog: pytest.LogCaptureFixture) -> None:
        set_session_id("capture-test")
        logger = setup_engine_logging(level=logging.DEBUG)

        with caplog.at_level(logging.DEBUG, logger="nearshare.engine"):
            logger.info("test message")

        assert len(caplog.records) >= 1
        record = caplog.records[-1]
        assert hasattr(record, "session_id")
        assert record.session_id == "capture-test"  # type: ignore[attr-defined]
        set_session_id(None)


class TestGetLogger:
    """get_logger creates child loggers under nearshare.engine."""

    def test_child_name(self) -> None:
        logger = get_logger("transfer")
        assert logger.name == "nearshare.engine.transfer"

    def test_nested_child(self) -> None:
        logger = get_logger("protocol.handshake")
        assert logger.name == "nearshare.engine.protocol.handshake"
