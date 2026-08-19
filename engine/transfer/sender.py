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
from typing import Callable

from engine.logging_.setup import get_logger, set_session_id
from engine.protocol.chunk_msg import build_chunk_payload
from engine.protocol.codec import MessageType
from engine.protocol.complete import build_transfer_complete_payload
from engine.protocol.handshake import perform_handshake, HandshakeError, PeerInfo
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
        file_data: list[bytes] = []

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
        file_data: list[bytes],
        on_progress: Callable[[int, int], None] | None,
        total_transfer_bytes: int,
    ) -> int:
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
                                if on_progress:
                                    # Very approximate progress update (assumes chunks are same size)
                                    # Real correct tracking would use total ACKed bytes
                                    # For simplicity here we just do a rough increment if needed, 
                                    # or we can ask session for total acked bytes.
                                    pass

            send_task = asyncio.create_task(_send_file_chunks())
            ack_task = asyncio.create_task(_recv_file_acks())
            
            # Progress loop
            async def _progress_loop():
                if not on_progress:
                    return
                while not session.is_file_complete(fi):
                    # calculate total acked across all files so far plus current file
                    current_acked = bytes_sent + session.get_file_bytes_acked(fi, self._chunk_size)
                    on_progress(current_acked, total_transfer_bytes)
                    await asyncio.sleep(0.1)

            prog_task = asyncio.create_task(_progress_loop())

            try:
                await asyncio.gather(send_task, ack_task)
            except Exception:
                send_task.cancel()
                ack_task.cancel()
                prog_task.cancel()
                raise
            
            prog_task.cancel()
            bytes_sent += meta.size
            if on_progress:
                on_progress(bytes_sent, total_transfer_bytes)

        return bytes_sent

