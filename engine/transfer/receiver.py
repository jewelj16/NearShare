"""TransferReceiver — orchestrates the receiver side of a file transfer.

Full async flow:
  1. HELLO handshake
  2. Receive METADATA, decide to accept or reject
  3. Wait for TRANSFER_START
  4. Chunk loop — receive chunks, verify per-chunk hash,
     write via ChunkAssembler, send ACK bitmaps
  5. On TRANSFER_COMPLETE: verify whole-file hash, write to disk

Uses TransferStateMachine to enforce state transitions and
structured logging with session_id context.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

from engine.logging_.setup import get_logger, set_session_id
from engine.protocol.chunk_msg import parse_chunk_payload
from engine.protocol.codec import MessageType
from engine.protocol.complete import parse_transfer_complete_payload
from engine.protocol.handshake import perform_handshake, HandshakeError
from engine.protocol.metadata import (
    build_accept_payload,
    build_reject_payload,
    parse_metadata_payload,
)
from engine.protocol.transfer_start import parse_transfer_start_payload
from engine.storage.file_io import write_file
from engine.transfer.ack import AckTracker, build_ack_payload
from engine.transfer.chunker import ChunkAssembler
from engine.transfer.result import TransferResult
from engine.transfer.session import TransferSession
from engine.transfer.state_machine import TransferStateMachine
from engine.types import (
    DeviceId,
    FileMetadata,
    TransferDirection,
    TransferState,
)


logger = get_logger("transfer.receiver")


class TransferReceiver:
    """Orchestrates the receiver side of a NearShare file transfer.

    Args:
        local_device_id:    Our DeviceId (announced in HELLO).
        local_display_name: Our display name.
        save_dir:           Directory to save received files.
        auto_accept:        If True, accept all transfers without prompting.
    """

    def __init__(
        self,
        local_device_id: DeviceId,
        local_display_name: str,
        save_dir: Path,
        auto_accept: bool = False,
    ) -> None:
        self._device_id = local_device_id
        self._display_name = local_display_name
        self._save_dir = save_dir
        self._auto_accept = auto_accept

    async def run(
        self,
        conn: object,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> TransferResult:
        """Execute the full receiver flow on an open connection.

        Args:
            conn: A Connection-like object with send_frame/recv_frame.
            on_progress: Optional callback invoked with (bytes_done, total_bytes).

        Returns:
            A TransferResult describing the outcome.
        """
        start = time.monotonic()
        sm = TransferStateMachine(TransferState.CONNECTING)

        session_id = "<unknown>"
        metadatas: list[FileMetadata] = []

        try:
            # ── handshake ────────────────────────────────────────────────────
            sm.transition(TransferState.HANDSHAKING)
            peer = await perform_handshake(conn, self._device_id, self._display_name)
            logger.info("Handshake complete with %s", peer.display_name)

            # ── receive METADATA ──────────────────────────────────────────
            sm.transition(TransferState.METADATA_SENT)
            meta_frame = await conn.recv_frame()  # type: ignore[attr-defined]
            if meta_frame.msg_type is not MessageType.METADATA:
                raise OSError(f"Expected METADATA, got {meta_frame.msg_type.name}")

            session_id, metadatas = parse_metadata_payload(meta_frame.payload)
            set_session_id(session_id)
            total_transfer_bytes = sum(m.size for m in metadatas)

            session = TransferSession(
                session_id=session_id,
                direction=TransferDirection.RECEIVE,
                files=metadatas,
            )

            logger.info(
                "Incoming transfer: %d file(s) from %s",
                len(metadatas),
                peer.display_name,
            )
            for i, f in enumerate(metadatas):
                logger.info("  [%d] %s (%d bytes)", i, f.name, f.size)

            # ── accept / reject ───────────────────────────────────────────
            sm.transition(TransferState.AWAITING_ACCEPT)
            if not self._auto_accept:
                accepted = await self._prompt_accept(metadatas)
            else:
                accepted = True

            if not accepted:
                await conn.send_frame(  # type: ignore[attr-defined]
                    MessageType.REJECT,
                    build_reject_payload(session_id, 0x01, "User declined"),
                )
                sm.transition(TransferState.FAILED)
                return TransferResult.failure(
                    session_id=session_id,
                    files=metadatas,
                    duration_s=time.monotonic() - start,
                    bytes_transferred=0,
                    error_msg="User declined transfer",
                )

            await conn.send_frame(  # type: ignore[attr-defined]
                MessageType.ACCEPT,
                build_accept_payload(session_id),
            )

            # ── wait for TRANSFER_START ───────────────────────────────────
            start_frame = await conn.recv_frame()  # type: ignore[attr-defined]
            if start_frame.msg_type is not MessageType.TRANSFER_START:
                raise OSError(
                    f"Expected TRANSFER_START, got {start_frame.msg_type.name}"
                )
            sm.transition(TransferState.TRANSFERRING)
            logger.info("Transfer started")
            
            if on_progress:
                on_progress(0, total_transfer_bytes)

            # ── chunk loop ────────────────────────────────────────────────
            bytes_received = await self._receive_all_files(
                conn, session, metadatas, on_progress, total_transfer_bytes
            )

            # ── write files to disk ───────────────────────────────────────
            saved_paths = self._write_files(metadatas, session)

            sm.transition(TransferState.COMPLETE)
            duration = time.monotonic() - start
            logger.info(
                "Transfer complete in %.2fs, %d bytes, %d files saved",
                duration,
                bytes_received,
                len(saved_paths),
            )
            
            if on_progress:
                on_progress(total_transfer_bytes, total_transfer_bytes)

            return TransferResult.success(
                session_id=session_id,
                files=metadatas,
                duration_s=duration,
                bytes_transferred=bytes_received,
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
                session_id=session_id,
                files=metadatas,
                duration_s=time.monotonic() - start,
                bytes_transferred=0,
                error_msg=str(exc),
            )
        finally:
            set_session_id(None)

    # ── internal ──────────────────────────────────────────────────────────────

    async def _prompt_accept(self, files: list[FileMetadata]) -> bool:
        """Prompt the user on stdin whether to accept the transfer.

        In a CLI context this blocks on input().  For testing, use
        auto_accept=True.
        """
        total_bytes = sum(f.size for f in files)
        mb = total_bytes / 1_048_576
        print(f"\nIncoming transfer: {len(files)} file(s), {mb:.1f} MB total")
        for f in files:
            print(f"  - {f.name} ({f.size:,} bytes)")
        response = await asyncio.get_event_loop().run_in_executor(
            None, lambda: input("\nAccept? [y/N] ").strip().lower()
        )
        return response in ("y", "yes")

    async def _receive_all_files(
        self,
        conn: object,
        session: TransferSession,
        metadatas: list[FileMetadata],
        on_progress: Callable[[int, int], None] | None,
        total_transfer_bytes: int,
    ) -> int:
        """Receive chunks for all files and send ACKs.

        Returns total bytes received.
        """
        session.init_ack_bitmaps()
        tracker = AckTracker(session.session_id, metadatas)

        # create assemblers for each file
        assemblers = [ChunkAssembler(meta) for meta in metadatas]
        bytes_received = 0

        while True:
            frame = await conn.recv_frame()  # type: ignore[attr-defined]

            if frame.msg_type is MessageType.TRANSFER_COMPLETE:
                logger.debug("Received TRANSFER_COMPLETE")
                break

            if frame.msg_type is MessageType.CANCEL:
                raise OSError("Peer cancelled the transfer")

            if frame.msg_type is not MessageType.CHUNK:
                logger.warning("Unexpected message type: %s", frame.msg_type.name)
                continue

            chunk = parse_chunk_payload(frame.payload)

            # write chunk to assembler (validates per-chunk hash)
            try:
                assemblers[chunk.file_index].write_chunk(chunk)
            except (ValueError, IndexError) as exc:
                logger.warning("Bad chunk %d:%d — %s", chunk.file_index, chunk.seq, exc)
                continue

            # update session tracking
            session.ack_chunk(chunk.file_index, chunk.seq)
            tracker.ack(chunk.file_index, chunk.seq)
            bytes_received += len(chunk.data)
            
            if on_progress:
                on_progress(bytes_received, total_transfer_bytes)

            # send ACK for this file
            ack_payload = build_ack_payload(
                session.session_id,
                chunk.file_index,
                tracker.bitmaps[chunk.file_index].received,
            )
            await conn.send_frame(MessageType.ACK, ack_payload)  # type: ignore[attr-defined]

        # store assembled data in session for writing
        session._assemblers = assemblers  # type: ignore[attr-defined]
        return bytes_received

    def _write_files(
        self,
        metadatas: list[FileMetadata],
        session: TransferSession,
    ) -> list[Path]:
        """Write all assembled files to disk."""
        assemblers: list[ChunkAssembler] = session._assemblers  # type: ignore[attr-defined]
        saved: list[Path] = []

        for meta, assembler in zip(metadatas, assemblers):
            if not assembler.is_complete:
                logger.warning("File %s incomplete, skipping", meta.name)
                continue
            data = assembler.to_bytes()
            dest = write_file(self._save_dir, meta, data)
            logger.info("Saved %s → %s", meta.name, dest)
            saved.append(dest)

        return saved
