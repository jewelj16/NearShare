"""Tests for engine.transfer.receiver — TransferReceiver over FakeTransport.

Uses a fake peer coroutine that acts as the sender: performs HELLO
exchange, sends METADATA, waits for ACCEPT, sends TRANSFER_START,
sends chunks, then TRANSFER_COMPLETE.
"""

import asyncio
import hashlib
from pathlib import Path

import pytest

from engine.integrity.hasher import chunk_hash
from engine.protocol.codec import MessageType
from engine.protocol.handshake import build_hello_payload
from engine.protocol.metadata import (
    build_metadata_payload,
    parse_accept_payload,
    parse_reject_payload,
)
from engine.protocol.transfer_start import build_transfer_start_payload
from engine.protocol.chunk_msg import build_chunk_payload
from engine.protocol.complete import build_transfer_complete_payload
from engine.transfer.receiver import TransferReceiver
from engine.transfer.result import TransferResult
from engine.transport.fake import FakeConnection, FakeTransport
from engine.types import (
    Chunk,
    DEFAULT_CHUNK_SIZE,
    DeviceId,
    FileMetadata,
    TransferState,
    PROTOCOL_VERSION,
)


# ── helpers ───────────────────────────────────────────────────────────────────

RECEIVER_ID = DeviceId("receiver-device")
SENDER_ID = DeviceId("sender-device")


def _make_receiver(save_dir: Path, auto_accept: bool = True) -> TransferReceiver:
    return TransferReceiver(
        local_device_id=RECEIVER_ID,
        local_display_name="Receiver",
        save_dir=save_dir,
        auto_accept=auto_accept,
    )


def _meta(name: str, data: bytes) -> FileMetadata:
    return FileMetadata(
        name=name,
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
    )


def _make_chunks(data: bytes, meta: FileMetadata, transfer_id: str, file_index: int = 0) -> list[Chunk]:
    """Split data into Chunk objects manually."""
    chunks = []
    cs = meta.chunk_size
    seq = 0
    offset = 0
    while offset < len(data):
        piece = data[offset : offset + cs]
        chunks.append(Chunk(
            transfer_id=transfer_id,
            file_index=file_index,
            seq=seq,
            data=piece,
            sha256=chunk_hash(piece),
        ))
        offset += cs
        seq += 1
    return chunks


async def _fake_sender(
    conn: FakeConnection,
    transfer_id: str,
    files_data: list[tuple[FileMetadata, bytes]],
) -> None:
    """Minimal fake sender: handshake → metadata → send chunks → complete."""
    # HELLO exchange
    frame = await conn.recv_frame()
    assert frame.msg_type is MessageType.HELLO
    hello = build_hello_payload(SENDER_ID, "Sender", PROTOCOL_VERSION)
    await conn.send_frame(MessageType.HELLO, hello)

    # send METADATA
    metadatas = [m for m, _ in files_data]
    await conn.send_frame(
        MessageType.METADATA,
        build_metadata_payload(transfer_id, metadatas),
    )

    # wait for ACCEPT or REJECT
    frame = await conn.recv_frame()
    if frame.msg_type is MessageType.REJECT:
        return  # done

    assert frame.msg_type is MessageType.ACCEPT

    # send TRANSFER_START
    await conn.send_frame(
        MessageType.TRANSFER_START,
        build_transfer_start_payload(transfer_id),
    )

    # send chunks for all files
    for fi, (meta, data) in enumerate(files_data):
        chunks = _make_chunks(data, meta, transfer_id, fi)
        for chunk in chunks:
            await conn.send_frame(MessageType.CHUNK, build_chunk_payload(chunk))
            # read ACK (receiver sends one per chunk)
            ack_frame = await conn.recv_frame()
            assert ack_frame.msg_type is MessageType.ACK

    # send TRANSFER_COMPLETE
    await conn.send_frame(
        MessageType.TRANSFER_COMPLETE,
        build_transfer_complete_payload(transfer_id),
    )


# ── single file happy path ────────────────────────────────────────────────────

class TestReceiverHappyPath:

    @pytest.mark.asyncio
    async def test_single_file(self, tmp_path: Path) -> None:
        data = b"hello receiver " * 100
        meta = _meta("greeting.txt", data)

        transport = FakeTransport()
        client_conn = await transport.connect("127.0.0.1", 47321)
        server_conn = await transport.accept()

        sender_task = asyncio.create_task(
            _fake_sender(server_conn, "sess-1", [(meta, data)])
        )
        receiver = _make_receiver(tmp_path)
        result = await asyncio.wait_for(
            receiver.run(client_conn), timeout=10.0
        )
        await sender_task

        assert result.ok
        assert result.state is TransferState.COMPLETE
        assert len(result.files) == 1
        assert result.files[0].name == "greeting.txt"
        assert result.bytes_transferred == len(data)

        # verify file was written correctly
        saved = tmp_path / "greeting.txt"
        assert saved.exists()
        assert saved.read_bytes() == data

    @pytest.mark.asyncio
    async def test_multi_file(self, tmp_path: Path) -> None:
        files_data = [
            (_meta("a.bin", b"AAA" * 100), b"AAA" * 100),
            (_meta("b.bin", b"BBB" * 200), b"BBB" * 200),
            (_meta("c.bin", b"CCC" * 50), b"CCC" * 50),
        ]

        transport = FakeTransport()
        client_conn = await transport.connect("127.0.0.1", 47321)
        server_conn = await transport.accept()

        sender_task = asyncio.create_task(
            _fake_sender(server_conn, "sess-multi", files_data)
        )
        receiver = _make_receiver(tmp_path)
        result = await asyncio.wait_for(
            receiver.run(client_conn), timeout=10.0
        )
        await sender_task

        assert result.ok
        assert len(result.files) == 3

        for meta, data in files_data:
            saved = tmp_path / meta.name
            assert saved.exists(), f"{meta.name} not saved"
            assert saved.read_bytes() == data

    @pytest.mark.asyncio
    async def test_result_fields(self, tmp_path: Path) -> None:
        data = b"x" * 4096
        meta = _meta("data.bin", data)

        transport = FakeTransport()
        client_conn = await transport.connect("127.0.0.1", 47321)
        server_conn = await transport.accept()

        asyncio.create_task(_fake_sender(server_conn, "sess-rf", [(meta, data)]))
        result = await asyncio.wait_for(
            _make_receiver(tmp_path).run(client_conn), timeout=10.0
        )

        assert result.session_id == "sess-rf"
        assert result.duration_s > 0
        assert result.bytes_transferred == 4096


# ── file integrity ────────────────────────────────────────────────────────────

class TestReceiverIntegrity:

    @pytest.mark.asyncio
    async def test_file_sha256_matches(self, tmp_path: Path) -> None:
        data = b"integrity test data " * 500
        meta = _meta("check.bin", data)

        transport = FakeTransport()
        client_conn = await transport.connect("127.0.0.1", 47321)
        server_conn = await transport.accept()

        asyncio.create_task(_fake_sender(server_conn, "sess-int", [(meta, data)]))
        result = await asyncio.wait_for(
            _make_receiver(tmp_path).run(client_conn), timeout=10.0
        )

        assert result.ok
        saved = tmp_path / "check.bin"
        actual_hash = hashlib.sha256(saved.read_bytes()).hexdigest()
        assert actual_hash == meta.sha256


# ── conflict rename ───────────────────────────────────────────────────────────

class TestReceiverConflictRename:

    @pytest.mark.asyncio
    async def test_duplicate_filename_renamed(self, tmp_path: Path) -> None:
        # pre-create a file with the same name
        (tmp_path / "dup.txt").write_bytes(b"original")

        data = b"new file content"
        meta = _meta("dup.txt", data)

        transport = FakeTransport()
        client_conn = await transport.connect("127.0.0.1", 47321)
        server_conn = await transport.accept()

        asyncio.create_task(_fake_sender(server_conn, "sess-dup", [(meta, data)]))
        result = await asyncio.wait_for(
            _make_receiver(tmp_path).run(client_conn), timeout=10.0
        )

        assert result.ok
        # original untouched
        assert (tmp_path / "dup.txt").read_bytes() == b"original"
        # new file renamed
        renamed = tmp_path / "dup (1).txt"
        assert renamed.exists()
        assert renamed.read_bytes() == data
