"""TransferSender — orchestrates the sender side of a file transfer.

Full async flow:
  1. HELLO handshake
  2. Send METADATA, wait for ACCEPT or REJECT
  3. Send TRANSFER_START
  4. Chunk loop — split each file, send through SendWindow,
     receive ACKs concurrently per file
  5. Send TRANSFER_COMPLETE once all files are fully ACK'd

Uses TransferStateMachine to enforce state transitions and
structured logging with session_id context.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Callable, Awaitable

from engine.logging_.setup import get_logger, set_session_id
from engine.protocol.chunk_msg import build_chunk_payload
from engine.protocol.codec import MessageType
from engine.protocol.complete import build_transfer_complete_payload
from engine.protocol.handshake import perform_handshake, HandshakeError
from engine.protocol.metadata import send_metadata, wait_for_accept_or_reject
from engine.protocol.transfer_start import build_transfer_start_payload
from engine.protocol.join_session import build_join_session_payload
from engine.transfer.ack import parse_ack_payload
from engine.storage.file_io import file_metadata_from_path, mmap_file
from engine.transfer.result import TransferResult
from engine.transfer.session import TransferSession, ConnectionPool
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
        self._pool = ConnectionPool(4)
        self._frame_queue: asyncio.Queue[tuple[object, object]] = asyncio.Queue()
        self._readers: list[asyncio.Task] = []

    async def _read_worker(self, conn: object) -> None:
        """Continuously reads frames from a connection and puts them in a central queue."""
        try:
            while True:
                frame = await conn.recv_frame()  # type: ignore[attr-defined]
                await self._frame_queue.put((conn, frame))
                if frame.msg_type in (MessageType.TRANSFER_COMPLETE, MessageType.CANCEL, MessageType.ERROR):
                    break
        except Exception as e:
            await self._frame_queue.put((conn, e))

    async def _accept_loop(self, conn_acceptor: Callable[[], Awaitable[object]], expected_session_id: str) -> None:
        """Accept secondary connections and map them via JOIN_SESSION."""
        try:
            while True:
                new_conn = await conn_acceptor()
                logger.info("Accepted new connection, waiting for JOIN_SESSION...")
                asyncio.create_task(self._handle_secondary_conn(new_conn, expected_session_id))
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.warning("Error in accept loop: %s", e)

    async def _handle_secondary_conn(self, conn: object, expected_session_id: str) -> None:
        try:
            frame = await conn.recv_frame()  # type: ignore[attr-defined]
            if frame.msg_type is MessageType.JOIN_SESSION:
                session_id = parse_join_session_payload(frame.payload)
                if session_id == expected_session_id:
                    logger.info("Secondary connection mapped to session %s", session_id)
                    self._pool.add(conn)
                    task = asyncio.create_task(self._read_worker(conn))
                    self._readers.append(task)
                else:
                    logger.warning("Rejected JOIN_SESSION for unknown session: %s", session_id)
            else:
                logger.warning("Expected JOIN_SESSION, got %s", frame.msg_type.name)
        except Exception as e:
            logger.warning("Error handling secondary connection: %s", e)

    async def run(
        self,
        conn: object,
        files: list[Path],
        conn_factory: Callable[[], Awaitable[object]] | None = None,
        conn_acceptor: Callable[[], Awaitable[object]] | None = None,
    ) -> TransferResult:
        """Execute the full sender flow on an open connection.

        Args:
            conn:  A Connection-like object with send_frame/recv_frame/close.
            files: Paths to the files to send (must all exist).
            conn_factory: Optional async factory to create new secondary sockets (if we are TCP client).
            conn_acceptor: Optional async factory to accept new secondary sockets (if we are TCP server).

        Returns:
            A TransferResult describing the outcome.
        """
        start = time.monotonic()
        metadatas: list[FileMetadata] = []
        accept_task = None

        # We assume the caller gave us one valid connection
        self._pool.add(conn)

        # read metadata up front
        try:
            for p in files:
                meta = file_metadata_from_path(p, self._chunk_size)
                metadatas.append(meta)
        except OSError as exc:
            return TransferResult.failure(
                session_id="<pre-session>",
                files=[],
                duration_s=time.monotonic() - start,
                bytes_transferred=0,
                error_msg=f"File read error: {exc}",
            )

        # create session
        session = TransferSession.create(
            direction=TransferDirection.SEND,
            files=metadatas,
        )
        set_session_id(session.session_id)
        sm = TransferStateMachine(TransferState.CONNECTING)
        logger.info("Starting send of %d file(s)", len(files))

        try:
            # ── handshake ────────────────────────────────────────────────────
            sm.transition(TransferState.HANDSHAKING)
            await perform_handshake(conn, self._device_id, self._display_name)
            logger.info("Handshake complete")

            # ── METADATA → ACCEPT/REJECT ──────────────────────────────────
            sm.transition(TransferState.METADATA_SENT)
            await send_metadata(conn, session.session_id, metadatas)

            sm.transition(TransferState.AWAITING_ACCEPT)
            tid, accepted, reason_code, reason_msg = await wait_for_accept_or_reject(conn)
            if not accepted:
                logger.info("Transfer rejected by peer: %s", reason_msg)
                sm.transition(TransferState.FAILED)
                return TransferResult.failure(
                    session_id=session.session_id,
                    files=metadatas,
                    duration_s=time.monotonic() - start,
                    bytes_transferred=0,
                    error_msg=f"Rejected by peer: {reason_msg}",
                )

            # ── TRANSFER_START ────────────────────────────────────────────
            sm.transition(TransferState.TRANSFERRING)
            await conn.send_frame(  # type: ignore[attr-defined]
                MessageType.TRANSFER_START,
                build_transfer_start_payload(session.session_id),
            )
            logger.info("Sending %d file(s)", len(metadatas))

            # ── initiate secondary connections ────────────────────────────
            if conn_factory:
                for _ in range(3):
                    try:
                        c = await conn_factory()
                        await c.send_frame(  # type: ignore[attr-defined]
                            MessageType.JOIN_SESSION,
                            build_join_session_payload(session.session_id)
                        )
                        self._pool.add(c)
                        logger.info("Initiated secondary connection")
                    except Exception as e:
                        logger.warning("Failed to open secondary connection: %s", e)
                        break
            elif conn_acceptor:
                accept_task = asyncio.create_task(self._accept_loop(conn_acceptor, session.session_id))

            # ── start reader tasks ────────────────────────────────────────
            for c in self._pool._conns._queue:
                t = asyncio.create_task(self._read_worker(c))
                self._readers.append(t)

            # ── chunk loop ────────────────────────────────────────────────
            bytes_sent = await self._send_all_files(
                session, metadatas, files
            )

            # ── TRANSFER_COMPLETE ─────────────────────────────────────────
            c = await self._pool.get()
            try:
                await c.send_frame(  # type: ignore[attr-defined]
                    MessageType.TRANSFER_COMPLETE,
                    build_transfer_complete_payload(session.session_id),
                )
            finally:
                self._pool.return_conn(c)

            sm.transition(TransferState.COMPLETE)
            duration = time.monotonic() - start
            logger.info("Transfer complete in %.2fs, %d bytes", duration, bytes_sent)

            return TransferResult.success(
                session_id=session.session_id,
                files=metadatas,
                duration_s=duration,
                bytes_transferred=bytes_sent,
            )

        except (OSError, HandshakeError, asyncio.CancelledError) as exc:
            if sm.state not in (TransferState.FAILED, TransferState.COMPLETE):
                if sm.state is TransferState.TRANSFERRING:
                    sm.transition(TransferState.INTERRUPTED)
                    sm.transition(TransferState.FAILED)
                else:
                    sm.transition(TransferState.FAILED)
            logger.error("Transfer failed: %s", exc)
            return TransferResult.failure(
                session_id=session.session_id,
                files=metadatas,
                duration_s=time.monotonic() - start,
                bytes_transferred=0,
                error_msg=str(exc),
            )
        finally:
            set_session_id(None)
            for t in self._readers:
                t.cancel()
            if accept_task:
                accept_task.cancel()

    # ── internal ──────────────────────────────────────────────────────────────

    async def _send_all_files(
        self,
        session: TransferSession,
        metadatas: list[FileMetadata],
        file_paths: list[Path],
    ) -> int:
        """Send all files one at a time, distributing chunks across the connection pool.

        Sends files sequentially. For each file, we split it into chunks, wait for
        space in the SendWindow, pop a connection from the pool, send the chunk,
        and return the connection. A central ACK reader loop processes ACKs from all
        sockets.
        """
        session.init_ack_bitmaps()
        bytes_sent = 0

        windows = {i: SendWindow(self._window_capacity) for i in range(len(metadatas))}
        file_events = {i: asyncio.Event() for i in range(len(metadatas))}

        async def _recv_acks() -> None:
            while not session.is_transfer_complete():
                conn, frame = await self._frame_queue.get()
                if isinstance(frame, Exception):
                    continue
                if frame.msg_type is MessageType.ACK:
                    _, ack_fi, seqs = parse_ack_payload(frame.payload)
                    for seq in seqs:
                        await session.ack_chunk_safe(ack_fi, seq)
                        windows[ack_fi].ack(seq)
                    if session.is_file_complete(ack_fi):
                        file_events[ack_fi].set()
                elif frame.msg_type is MessageType.CANCEL:
                    # abort
                    break

        ack_task = asyncio.create_task(_recv_acks())

        try:
            for fi, (meta, path) in enumerate(zip(metadatas, file_paths)):
                logger.debug("Sending file %d: %s (%d bytes)", fi, meta.name, meta.size)
                window = windows[fi]

                if meta.size == 0:
                    file_events[fi].set()
                else:
                    with mmap_file(path) as mm:
                        async for chunk in split(mm, meta, session.session_id, fi):
                            await window.wait_for_space()
                            c = await self._pool.get()
                            try:
                                await c.send_frame(  # type: ignore[attr-defined]
                                    MessageType.CHUNK, build_chunk_payload(chunk)
                                )
                            except Exception as e:
                                logger.debug("Failed to send chunk: %s", e)
                            finally:
                                self._pool.return_conn(c)
                            window.mark_sent(chunk.seq)

                # wait for this file to be completely acked
                await file_events[fi].wait()
                bytes_sent += meta.size
        finally:
            ack_task.cancel()

        return bytes_sent
