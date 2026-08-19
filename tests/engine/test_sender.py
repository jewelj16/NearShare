"""Tests for engine.transfer.sender — TransferSender over FakeTransport.

Uses a fake peer coroutine that acts as the receiver: performs HELLO
exchange, sends ACCEPT, and ACKs every chunk it receives.
"""

import asyncio
import hashlib
from pathlib import Path

import pytest

from engine.protocol.codec import MessageType
from engine.protocol.handshake import build_hello_payload, parse_hello_payload
from engine.protocol.metadata import (
    build_accept_payload,
    build_reject_payload,
    parse_metadata_payload,
)
from engine.protocol.transfer_start import parse_transfer_start_payload
from engine.protocol.chunk_msg import parse_chunk_payload
from engine.protocol.complete import parse_transfer_complete_payload
from engine.transfer.ack import build_ack_payload
from engine.transfer.result import TransferResult
from engine.transfer.sender import TransferSender
from engine.transport.fake import FakeConnection, FakeTransport
from engine.types import (
    DEFAULT_CHUNK_SIZE,
    DeviceId,
    TransferState,
    PROTOCOL_VERSION,
)


# ── helpers ───────────────────────────────────────────────────────────────────

SENDER_ID = DeviceId("sender-device")
PEER_ID = DeviceId("peer-device")


def _make_sender(**kwargs) -> TransferSender:
    return TransferSender(
        local_device_id=SENDER_ID,
        local_display_name="Sender",
        **kwargs,
    )


async def _fake_receiver(
    conn: FakeConnection,
    *,
    accept: bool = True,
    reject_reason: str = "declined",
) -> None:
    """Minimal fake receiver: handshake → accept/reject → ack all chunks."""
    # HELLO exchange
    frame = await conn.recv_frame()
    assert frame.msg_type is MessageType.HELLO
    hello_payload = build_hello_payload(PEER_ID, "Peer", PROTOCOL_VERSION)
    await conn.send_frame(MessageType.HELLO, hello_payload)

    # receive METADATA
    frame = await conn.recv_frame()
    assert frame.msg_type is MessageType.METADATA
    tid, files = parse_metadata_payload(frame.payload)

    if not accept:
        await conn.send_frame(
            MessageType.REJECT,
            build_reject_payload(tid, 0x01, reject_reason),
        )
        return

    # send ACCEPT
    await conn.send_frame(MessageType.ACCEPT, build_accept_payload(tid))

    # receive TRANSFER_START
    frame = await conn.recv_frame()
    assert frame.msg_type is MessageType.TRANSFER_START

    # receive chunks and ACK each one per file
    acked: dict[int, set[int]] = {}
    while True:
        frame = await conn.recv_frame()
        if frame.msg_type is MessageType.TRANSFER_COMPLETE:
            break
        assert frame.msg_type is MessageType.CHUNK
        chunk = parse_chunk_payload(frame.payload)
        acked.setdefault(chunk.file_index, set()).add(chunk.seq)
        # send ACK for this file's received seqs so far
        ack_payload = build_ack_payload(tid, chunk.file_index, acked[chunk.file_index])
        await conn.send_frame(MessageType.ACK, ack_payload)


# ── single file happy path ────────────────────────────────────────────────────

class TestSenderHappyPath:

    @pytest.mark.asyncio
    async def test_single_file_success(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_bytes(b"hello world " * 100)

        transport = FakeTransport()
        client_conn = await transport.connect("127.0.0.1", 47321)
        server_conn = await transport.accept()

        receiver_task = asyncio.create_task(_fake_receiver(server_conn))
        sender = _make_sender()
        result = await asyncio.wait_for(
            sender.run(client_conn, [f]), timeout=10.0
        )
        await receiver_task

        assert result.ok
        assert result.state is TransferState.COMPLETE
        assert len(result.files) == 1
        assert result.files[0].name == "hello.txt"
        assert result.bytes_transferred > 0

    @pytest.mark.asyncio
    async def test_multi_file_success(self, tmp_path: Path) -> None:
        files = []
        for i in range(3):
            f = tmp_path / f"file{i}.bin"
            f.write_bytes(bytes([i] * 1024))
            files.append(f)

        transport = FakeTransport()
        client_conn = await transport.connect("127.0.0.1", 47321)
        server_conn = await transport.accept()

        receiver_task = asyncio.create_task(_fake_receiver(server_conn))
        sender = _make_sender()
        result = await asyncio.wait_for(
            sender.run(client_conn, files), timeout=10.0
        )
        await receiver_task

        assert result.ok
        assert len(result.files) == 3

    @pytest.mark.asyncio
    async def test_result_fields(self, tmp_path: Path) -> None:
        data = b"x" * 4096
        f = tmp_path / "data.bin"
        f.write_bytes(data)

        transport = FakeTransport()
        client_conn = await transport.connect("127.0.0.1", 47321)
        server_conn = await transport.accept()

        asyncio.create_task(_fake_receiver(server_conn))
        result = await asyncio.wait_for(
            _make_sender().run(client_conn, [f]), timeout=10.0
        )

        assert result.session_id != ""
        assert result.duration_s > 0
        assert result.bytes_transferred == len(data)


# ── reject flow ───────────────────────────────────────────────────────────────

class TestSenderReject:

    @pytest.mark.asyncio
    async def test_reject_returns_failure(self, tmp_path: Path) -> None:
        f = tmp_path / "f.txt"
        f.write_bytes(b"data")

        transport = FakeTransport()
        client_conn = await transport.connect("127.0.0.1", 47321)
        server_conn = await transport.accept()

        receiver_task = asyncio.create_task(
            _fake_receiver(server_conn, accept=False, reject_reason="No thanks")
        )
        result = await asyncio.wait_for(
            _make_sender().run(client_conn, [f]), timeout=10.0
        )
        await receiver_task

        assert not result.ok
        assert result.state is TransferState.FAILED
        assert "Rejected" in (result.error_msg or "")

    @pytest.mark.asyncio
    async def test_missing_file_returns_failure(self, tmp_path: Path) -> None:
        transport = FakeTransport()
        client_conn = await transport.connect("127.0.0.1", 47321)

        result = await _make_sender().send_files(
            client_conn, [tmp_path / "nonexistent.bin"]
        )

        assert not result.ok
        assert "File read error" in (result.error_msg or "")
