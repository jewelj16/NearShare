"""Tests for SendWindow — window cap, ack, and backpressure."""

import asyncio

import pytest

from engine.transfer.window import SendWindow
from engine.types import DEFAULT_SEND_WINDOW


# capacity enforcement

class TestWindowCap:
    """Window must respect its capacity limit."""

    def test_default_capacity(self) -> None:
        w = SendWindow()
        assert w.capacity == DEFAULT_SEND_WINDOW

    def test_custom_capacity(self) -> None:
        w = SendWindow(capacity=8)
        assert w.capacity == 8

    def test_mark_sent_up_to_capacity(self) -> None:
        w = SendWindow(capacity=3)
        w.mark_sent(0)
        w.mark_sent(1)
        w.mark_sent(2)
        assert w.size == 3
        assert w.is_full is True

    def test_mark_sent_beyond_capacity_raises(self) -> None:
        w = SendWindow(capacity=2)
        w.mark_sent(0)
        w.mark_sent(1)
        with pytest.raises(ValueError, match="full"):
            w.mark_sent(2)

    def test_available_decreases(self) -> None:
        w = SendWindow(capacity=4)
        assert w.available == 4
        w.mark_sent(0)
        assert w.available == 3
        w.mark_sent(1)
        assert w.available == 2

    def test_in_flight_returns_copy(self) -> None:
        w = SendWindow(capacity=5)
        w.mark_sent(10)
        w.mark_sent(11)
        snapshot = w.in_flight
        w.mark_sent(12)
        assert 12 not in snapshot  # snapshot should not change


# ack behavior

class TestWindowAck:
    """Acknowledging chunks frees window slots."""

    def test_ack_frees_slot(self) -> None:
        w = SendWindow(capacity=2)
        w.mark_sent(0)
        w.mark_sent(1)
        assert w.is_full is True
        w.ack(0)
        assert w.is_full is False
        assert w.size == 1

    def test_ack_unknown_seq_is_noop(self) -> None:
        w = SendWindow(capacity=5)
        w.mark_sent(0)
        w.ack(99)  # not in flight
        assert w.size == 1

    def test_ack_idempotent(self) -> None:
        w = SendWindow(capacity=5)
        w.mark_sent(0)
        w.ack(0)
        w.ack(0)  # already acked
        assert w.size == 0

    def test_ack_many(self) -> None:
        w = SendWindow(capacity=5)
        for i in range(5):
            w.mark_sent(i)
        w.ack_many([0, 2, 4])
        assert w.size == 2
        assert w.in_flight == {1, 3}

    def test_ack_many_with_unknown(self) -> None:
        w = SendWindow(capacity=3)
        w.mark_sent(0)
        w.ack_many([0, 99, 100])  # 99 and 100 not in flight
        assert w.size == 0


# async backpressure

class TestWindowBackpressure:
    """wait_for_space blocks when window is full."""

    @pytest.mark.asyncio
    async def test_wait_returns_immediately_when_space(self) -> None:
        w = SendWindow(capacity=5)
        w.mark_sent(0)
        # should not block
        await asyncio.wait_for(w.wait_for_space(), timeout=0.1)

    @pytest.mark.asyncio
    async def test_wait_blocks_when_full(self) -> None:
        w = SendWindow(capacity=2)
        w.mark_sent(0)
        w.mark_sent(1)

        # wait_for_space should not complete within 50ms
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(w.wait_for_space(), timeout=0.05)

    @pytest.mark.asyncio
    async def test_wait_unblocks_on_ack(self) -> None:
        w = SendWindow(capacity=2)
        w.mark_sent(0)
        w.mark_sent(1)

        async def ack_later() -> None:
            await asyncio.sleep(0.01)
            w.ack(0)

        asyncio.create_task(ack_later())
        # should unblock within 100ms
        await asyncio.wait_for(w.wait_for_space(), timeout=0.5)
        assert w.available == 1


# reset

class TestWindowReset:
    """Reset clears all in-flight state."""

    def test_reset_clears_everything(self) -> None:
        w = SendWindow(capacity=3)
        w.mark_sent(0)
        w.mark_sent(1)
        w.mark_sent(2)
        w.reset()
        assert w.size == 0
        assert w.is_full is False
        assert w.available == 3

    @pytest.mark.asyncio
    async def test_reset_unblocks_wait(self) -> None:
        w = SendWindow(capacity=1)
        w.mark_sent(0)

        async def reset_later() -> None:
            await asyncio.sleep(0.01)
            w.reset()

        asyncio.create_task(reset_later())
        await asyncio.wait_for(w.wait_for_space(), timeout=0.5)
