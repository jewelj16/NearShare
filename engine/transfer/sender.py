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

from engine.logging_.setup import get_logger, set_session_id
from engine.protocol.chunk_msg import build_chunk_payload
from engine.protocol.codec import MessageType
from engine.protocol.complete import build_transfer_complete_payload
from engine.protocol.handshake import perform_handshake, HandshakeError
from engine.protocol.metadata import send_metadata, wait_for_accept_or_reject
from engine.protocol.transfer_start import build_transfer_start_payload
from engine.transfer.ack import parse_ack_payload
from engine.storage.file_io import file_metadata_from_path, read_file_bytes
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
        chunk_size:         Chunk size in bytes (default 256 KB).
        window_capacity:    Sliding window size (default 32).
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

    async def run(
        self,
        conn: object,
        files: list[Path],
    ) -> TransferResult:
        """Execute the full sender flow on an open connection.

        Args:
            conn:  A Connection-like object with send_frame/recv_frame/close.
            files: Paths to the files to send (must all exist).

        Returns:
            A TransferResult describing the outcome.
        """
        start = time.monotonic()
        metadatas: list[FileMetadata] = []
        file_data: list[bytes] = []

        # read files up front (v1 keeps everything in memory)
        try:
            for p in files:
                meta = file_metadata_from_path(p, self._chunk_size)
                data = read_file_bytes(p)
                metadatas.append(meta)
                file_data.append(data)
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

            # ── chunk loop ────────────────────────────────────────────────
            bytes_sent = await self._send_all_files(
                conn, session, metadatas, file_data
            )

            # ── TRANSFER_COMPLETE ─────────────────────────────────────────
            await conn.send_frame(  # type: ignore[attr-defined]
                MessageType.TRANSFER_COMPLETE,
                build_transfer_complete_payload(session.session_id),
            )
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

    # ── internal ──────────────────────────────────────────────────────────────

    async def _send_all_files(
        self,
        conn: object,
        session: TransferSession,
        metadatas: list[FileMetadata],
        file_data: list[bytes],
    ) -> int:
        """Send all files one at a time, each with its own sliding window.

        Sends files sequentially.  For each file, two concurrent tasks run:
          - chunk sender: splits the file and sends chunks through the window
          - ack receiver: reads ACK frames, updates session bitmap, frees slots

        The window is per-file because chunk seq numbers restart at 0 for
        each file (per protocol.md §3.6).
        """
        session.init_ack_bitmaps()
        bytes_sent = 0

        for fi, (meta, data) in enumerate(zip(metadatas, file_data)):
            logger.debug("Sending file %d: %s (%d bytes)", fi, meta.name, meta.size)
            window = SendWindow(self._window_capacity)
            file_done = asyncio.Event()

            async def _send_file_chunks(
                _fi: int = fi,
                _meta: FileMetadata = meta,
                _data: bytes = data,
            ) -> None:
                for chunk in split(_data, _meta, session.session_id, _fi):
                    await window.wait_for_space()
                    await conn.send_frame(  # type: ignore[attr-defined]
                        MessageType.CHUNK, build_chunk_payload(chunk)
                    )
                    window.mark_sent(chunk.seq)
                file_done.set()

            async def _recv_file_acks(_fi: int = fi) -> None:
                """Read ACKs until this file is fully acknowledged."""
                while not session.is_file_complete(_fi):
                    try:
                        frame = await asyncio.wait_for(
                            conn.recv_frame(),  # type: ignore[attr-defined]
                            timeout=30.0,
                        )
                    except (asyncio.TimeoutError, TimeoutError):
                        if file_done.is_set():
                            break
                        continue
                    except OSError:
                        break
                    if frame.msg_type is MessageType.ACK:
                        _, ack_fi, seqs = parse_ack_payload(frame.payload)
                        if ack_fi == _fi:
                            for seq in seqs:
                                await session.ack_chunk_safe(_fi, seq)
                                window.ack(seq)

            send_task = asyncio.create_task(_send_file_chunks())
            ack_task = asyncio.create_task(_recv_file_acks())
            try:
                await asyncio.gather(send_task, ack_task)
            except Exception:
                send_task.cancel()
                ack_task.cancel()
                raise

            bytes_sent += meta.size

        return bytes_sent
