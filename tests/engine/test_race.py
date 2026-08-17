"""Tests for the ack-bitmap race condition fix.

Reproduces the race: concurrent tasks calling ack_chunk on the same
TransferSession without a lock can lose updates.  After the fix
(asyncio.Lock), the test passes.
"""

import asyncio

import pytest

from engine.transfer.session import TransferSession
from engine.types import DEFAULT_CHUNK_SIZE, FileMetadata, TransferDirection


# Helpers

def _file(chunks: int = 100) -> FileMetadata:
    return FileMetadata(name="race.bin", size=DEFAULT_CHUNK_SIZE * chunks)


class TestAckBitmapRace:
    """Reproduce and verify the ack-bitmap race condition fix."""

    @pytest.mark.asyncio
    async def test_concurrent_acks_no_lost_updates(self) -> None:
        """Hammer ack_chunk from many concurrent tasks.

        Without a lock, some set.add() calls could be lost if the
        dict/set internal state is corrupted by concurrent mutation.
        With asyncio (single-threaded), the main risk is losing updates
        when a coroutine yields between read and write of the bitmap.

        This test verifies all acks are recorded.
        """
        chunk_count = 200
        f = _file(chunk_count)
        sess = TransferSession.create(
            direction=TransferDirection.RECEIVE,
            files=[f],
        )
        sess.init_ack_bitmaps()

        async def ack_range(start: int, end: int) -> None:
            for seq in range(start, end):
                await sess.ack_chunk_safe(0, seq)

        # split the range across 10 concurrent tasks
        tasks = []
        step = chunk_count // 10
        for i in range(10):
            start = i * step
            end = start + step
            tasks.append(asyncio.create_task(ack_range(start, end)))

        await asyncio.gather(*tasks)

        # ALL chunks should be acked
        assert sess.is_file_complete(0)
        assert len(sess.ack_bitmaps[0]) == chunk_count

    @pytest.mark.asyncio
    async def test_concurrent_acks_multiple_files(self) -> None:
        """Race condition fix works across multiple files."""
        files = [_file(50), _file(50)]
        sess = TransferSession.create(
            direction=TransferDirection.RECEIVE,
            files=files,
        )
        sess.init_ack_bitmaps()

        async def ack_file(fi: int) -> None:
            for seq in range(50):
                await sess.ack_chunk_safe(fi, seq)

        await asyncio.gather(
            asyncio.create_task(ack_file(0)),
            asyncio.create_task(ack_file(1)),
        )

        assert sess.is_transfer_complete()

    @pytest.mark.asyncio
    async def test_lock_is_reentrant_safe(self) -> None:
        """Multiple sequential calls work correctly."""
        f = _file(10)
        sess = TransferSession.create(
            direction=TransferDirection.RECEIVE,
            files=[f],
        )
        sess.init_ack_bitmaps()

        for seq in range(10):
            await sess.ack_chunk_safe(0, seq)

        assert sess.is_file_complete(0)

    def test_sync_ack_still_works(self) -> None:
        """The original synchronous ack_chunk method still works."""
        f = _file(5)
        sess = TransferSession.create(
            direction=TransferDirection.RECEIVE,
            files=[f],
        )
        for seq in range(5):
            sess.ack_chunk(0, seq)
        assert sess.is_file_complete(0)
