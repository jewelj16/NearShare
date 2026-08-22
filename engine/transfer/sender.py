"""TransferSender — orchestrates the sender side of a file transfer.

Full async flow:
  1. HELLO handshake
  2. Send METADATA, wait for ACCEPT or REJECT
  3. Send TRANSFER_START
  4. Chunk loop — split each file, send through SendWindow,
     receive ACKs concurrently via a single shared ACK reader
  5. Send TRANSFER_COMPLETE once all files are fully ACK'd

Phase 3 (TCP Multiplexing): all files are sent concurrently on the single
connection — one asyncio task per file, one shared ACK-reader task.

Uses TransferStateMachine to enforce state transitions and
structured logging with session_id context.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import ExitStack
from pathlib import Path
from typing import Callable

from engine.logging_.setup import get_logger, set_session_id
from engine.protocol.chunk_msg import build_chunk_payload
from engine.protocol.codec import MessageType
from engine.protocol.complete import build_transfer_complete_payload
from engine.protocol.handshake import perform_handshake, HandshakeError, PeerInfo
from engine.protocol.metadata import send_metadata, wait_for_accept_or_reject
from engine.protocol.transfer_start import build_transfer_start_payload
from engine.transfer.ack import parse_ack_payload
from engine.storage.file_io import file_metadata_from_path, MemoryMappedFile
from engine.transfer.result import TransferResult
from engine.transfer.session import TransferSession
from engine.transfer.state_machine import TransferStateMachine
from engine.transfer.window import SendWindow
from engine.types import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_SEND_WINDOW,
    DeviceId,
    FileMetadata,
    TransferDirection,
    TransferState,
)
from engine.transfer.chunker import split


logger = get_logger("transfer.sender")


class TransferSender:
    """Orchestrates the sender side of a NearShare file transfer.

    Args:
        local_device_id:    Our DeviceId (announced in HELLO).
        local_display_name: Our display name.
        chunk_size:         Chunk size in bytes (default 1 MB).
        window_capacity:    Sliding window size (default 128).
    """

    def __init__(
        self,
        local_device_id: DeviceId,
        local_display_name: str,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        window_capacity: int = DEFAULT_SEND_WINDOW,
    ) -> None:
        self._device_id = local_device_id
        self._display_name = local_display_name
        self._chunk_size = chunk_size
        self._window_capacity = window_capacity
        self._sm = TransferStateMachine(TransferState.CONNECTING)
        self._start_time = 0.0

    async def handshake(self, conn: object) -> PeerInfo:
        """Perform the HELLO handshake and return peer info."""
        self._start_time = time.monotonic()
        self._sm.transition(TransferState.HANDSHAKING)
        peer_info = await perform_handshake(conn, self._device_id, self._display_name)
        logger.info("Handshake complete")
        return peer_info

    async def send_files(
        self,
        conn: object,
        files: list[Path],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> TransferResult:
        """Execute the metadata and file transfer phase.

        Args:
            conn:  A Connection-like object with send_frame/recv_frame/close.
            files: Paths to the files to send (must all exist).
            on_progress: Optional callback invoked with (bytes_done, total_bytes).

        Returns:
            A TransferResult describing the outcome.
        """
        if self._start_time == 0.0:
            self._start_time = time.monotonic()

        metadatas: list[FileMetadata] = []
        file_data = []
        stack = ExitStack()

        try:
            for p in files:
                meta = file_metadata_from_path(p, self._chunk_size)
                mmap_ctx = MemoryMappedFile(p)
                mmap_obj = stack.enter_context(mmap_ctx)
                metadatas.append(meta)
                file_data.append(mmap_obj)
        except OSError as exc:
            stack.close()
            return TransferResult.failure(
                session_id="<pre-session>",
                files=[],
                duration_s=time.monotonic() - self._start_time,
                bytes_transferred=0,
                error_msg=f"File read error: {exc}",
            )

        session = TransferSession.create(
            direction=TransferDirection.SEND,
            files=metadatas,
        )
        set_session_id(session.session_id)
        logger.info("Starting send of %d file(s)", len(files))
        total_bytes = sum(m.size for m in metadatas)

        try:
            self._sm.transition(TransferState.METADATA_SENT)
            await send_metadata(conn, session.session_id, metadatas)

            self._sm.transition(TransferState.AWAITING_ACCEPT)
            tid, accepted, reason_code, reason_msg = await wait_for_accept_or_reject(conn)
            if not accepted:
                logger.info("Transfer rejected by peer: %s", reason_msg)
                self._sm.transition(TransferState.FAILED)
                return TransferResult.failure(
                    session_id=session.session_id,
                    files=metadatas,
                    duration_s=time.monotonic() - self._start_time,
                    bytes_transferred=0,
                    error_msg=f"Rejected by peer: {reason_msg}",
                )

            self._sm.transition(TransferState.TRANSFERRING)
            await conn.send_frame(  # type: ignore[attr-defined]
                MessageType.TRANSFER_START,
                build_transfer_start_payload(session.session_id),
            )
            logger.info("Sending %d file(s)", len(metadatas))

            if on_progress:
                on_progress(0, total_bytes)

            bytes_sent = await self._send_all_files(
                conn, session, metadatas, file_data, on_progress, total_bytes
            )

            await conn.send_frame(  # type: ignore[attr-defined]
                MessageType.TRANSFER_COMPLETE,
                build_transfer_complete_payload(session.session_id),
            )
            self._sm.transition(TransferState.COMPLETE)
            duration = time.monotonic() - self._start_time
            logger.info("Transfer complete in %.2fs, %d bytes", duration, bytes_sent)

            if on_progress:
                on_progress(total_bytes, total_bytes)

            return TransferResult.success(
                session_id=session.session_id,
                files=metadatas,
                duration_s=duration,
                bytes_transferred=bytes_sent,
            )

        except (OSError, HandshakeError, asyncio.CancelledError) as exc:
            if self._sm.state not in (TransferState.FAILED, TransferState.COMPLETE):
                if self._sm.state is TransferState.TRANSFERRING:
                    self._sm.transition(TransferState.INTERRUPTED)
                    self._sm.transition(TransferState.FAILED)
                else:
                    self._sm.transition(TransferState.FAILED)
            logger.error("Transfer failed: %s", exc)
            return TransferResult.failure(
                session_id=session.session_id,
                files=metadatas,
                duration_s=time.monotonic() - self._start_time,
                bytes_transferred=0,
                error_msg=str(exc),
            )
        finally:
            stack.close()
            set_session_id(None)

    async def run(
        self,
        conn: object,
        files: list[Path],
        on_progress: Callable[[int, int], None] | None = None,
    ) -> TransferResult:
        """Legacy helper for the direct run mode (combines handshake + send)."""
        try:
            await self.handshake(conn)
        except Exception as exc:
            return TransferResult.failure(
                session_id="<pre-session>",
                files=[],
                duration_s=0.0,
                bytes_transferred=0,
                error_msg=f"Handshake failed: {exc}",
            )
        return await self.send_files(conn, files, on_progress)

    async def _send_all_files(
        self,
        conn: object,
        session: TransferSession,
        metadatas: list[FileMetadata],
        file_data: list[memoryview],
        on_progress: Callable[[int, int], None] | None,
        total_transfer_bytes: int,
    ) -> int:
        """Send all files concurrently over the single TCP connection.

        Phase 3 — TCP Multiplexing:
        Each file gets its own chunk-sender coroutine and its own SendWindow.
        A single shared ACK-reader coroutine reads all incoming ACK frames and
        dispatches them to the correct window by file_index.  This keeps the
        TCP pipe saturated even during ACK round-trips for any individual file.

        Returns:
            Total bytes sent across all files.
        """
        session.init_ack_bitmaps()

        # One independent SendWindow per file for per-file backpressure
        windows = [SendWindow(self._window_capacity) for _ in metadatas]

        # Each sender sets this event when its last chunk has been sent
        all_chunks_sent = [asyncio.Event() for _ in metadatas]

        # Serialise frame writes: asyncio doesn't guarantee atomic writes for
        # concurrent coroutines on the same stream, so we mutex send_frame.
        send_lock = asyncio.Lock()

        # ── per-file chunk sender ─────────────────────────────────────────────

        async def _send_one_file(
            fi: int,
            meta: FileMetadata,
            data: memoryview,
        ) -> None:
            logger.debug("Sending file %d: %s (%d bytes)", fi, meta.name, meta.size)
            async for chunk in split(data, meta, session.session_id, fi):
                await windows[fi].wait_for_space()
                async with send_lock:
                    await conn.send_frame(  # type: ignore[attr-defined]
                        MessageType.CHUNK, build_chunk_payload(chunk)
                    )
                windows[fi].mark_sent(chunk.seq)
            all_chunks_sent[fi].set()
            logger.debug("File %d: all chunks sent", fi)

        # ── shared ACK reader ─────────────────────────────────────────────────

        async def _ack_reader() -> None:
            """Read ACK frames and fan them out to the correct file's window."""
            while not session.is_transfer_complete():
                try:
                    frame = await asyncio.wait_for(
                        conn.recv_frame(),  # type: ignore[attr-defined]
                        timeout=30.0,
                    )
                except (asyncio.TimeoutError, TimeoutError):
                    # All chunks have been sent but we timed out waiting for the
                    # final ACKs — give up to avoid hanging indefinitely.
                    if all(e.is_set() for e in all_chunks_sent):
                        logger.warning("ACK timeout after all chunks sent — giving up")
                        break
                    continue
                except OSError:
                    break

                if frame.msg_type is not MessageType.ACK:
                    continue

                _, ack_fi, seqs = parse_ack_payload(frame.payload)
                if 0 <= ack_fi < len(windows):
                    for seq in seqs:
                        await session.ack_chunk_safe(ack_fi, seq)
                        windows[ack_fi].ack(seq)

                # Emit an accurate progress update after every ACK burst
                if on_progress:
                    acked_bytes = sum(
                        session.get_file_bytes_acked(i, metadatas[i].chunk_size)
                        for i in range(len(metadatas))
                    )
                    on_progress(acked_bytes, total_transfer_bytes)

        # ── launch all file senders + the shared ACK reader concurrently ──────

        sender_tasks = [
            asyncio.create_task(_send_one_file(fi, meta, data))
            for fi, (meta, data) in enumerate(zip(metadatas, file_data))
        ]
        ack_task = asyncio.create_task(_ack_reader())

        try:
            await asyncio.gather(*sender_tasks, ack_task)
        except Exception:
            for t in sender_tasks:
                t.cancel()
            ack_task.cancel()
            raise

        if on_progress:
            on_progress(total_transfer_bytes, total_transfer_bytes)

        return total_transfer_bytes
