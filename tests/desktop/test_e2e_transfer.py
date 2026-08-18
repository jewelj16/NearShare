"""End-to-end transfer tests — Sender ↔ Receiver over FakeTransport.

These tests run the full TransferSender and TransferReceiver against each
other on a FakeTransport, verifying the complete protocol flow end-to-end
without any real sockets.
"""

import asyncio
import hashlib
from pathlib import Path

import pytest

from engine.transfer.receiver import TransferReceiver
from engine.transfer.sender import TransferSender
from engine.transport.fake import FakeTransport
from engine.types import DeviceId, TransferState


SENDER_ID = DeviceId("sender-e2e")
RECEIVER_ID = DeviceId("receiver-e2e")


def _make_sender() -> TransferSender:
    return TransferSender(
        local_device_id=SENDER_ID,
        local_display_name="SenderBox",
    )


def _make_receiver(save_dir: Path) -> TransferReceiver:
    return TransferReceiver(
        local_device_id=RECEIVER_ID,
        local_display_name="ReceiverBox",
        save_dir=save_dir,
        auto_accept=True,
    )


class TestE2ETransfer:
    """Full sender ↔ receiver round trip."""

    @pytest.mark.asyncio
    async def test_single_file_e2e(self, tmp_path: Path) -> None:
        """Send one file and verify it arrives byte-for-byte."""
        src_dir = tmp_path / "src"
        dst_dir = tmp_path / "dst"
        src_dir.mkdir()
        dst_dir.mkdir()

        data = b"End-to-end transfer test! " * 1000
        src_file = src_dir / "e2e_test.txt"
        src_file.write_bytes(data)

        transport = FakeTransport()
        sender_conn = await transport.connect("127.0.0.1", 47321)
        receiver_conn = await transport.accept()

        sender = _make_sender()
        receiver = _make_receiver(dst_dir)

        # run both concurrently
        sender_result, receiver_result = await asyncio.wait_for(
            asyncio.gather(
                sender.run(sender_conn, [src_file]),
                receiver.run(receiver_conn),
            ),
            timeout=15.0,
        )

        # both should succeed
        assert sender_result.ok, f"Sender failed: {sender_result.error_msg}"
        assert receiver_result.ok, f"Receiver failed: {receiver_result.error_msg}"

        # verify file content
        received_file = dst_dir / "e2e_test.txt"
        assert received_file.exists()
        assert received_file.read_bytes() == data

        # verify SHA-256 match
        expected_hash = hashlib.sha256(data).hexdigest()
        actual_hash = hashlib.sha256(received_file.read_bytes()).hexdigest()
        assert actual_hash == expected_hash

    @pytest.mark.asyncio
    async def test_multi_file_e2e(self, tmp_path: Path) -> None:
        """Send three files and verify all arrive correctly."""
        src_dir = tmp_path / "src"
        dst_dir = tmp_path / "dst"
        src_dir.mkdir()
        dst_dir.mkdir()

        files_data = {
            "readme.txt": b"This is the readme\n" * 500,
            "photo.jpg": bytes(range(256)) * 100,
            "config.json": b'{"key": "value"}\n' * 300,
        }

        src_files = []
        for name, data in files_data.items():
            f = src_dir / name
            f.write_bytes(data)
            src_files.append(f)

        transport = FakeTransport()
        sender_conn = await transport.connect("127.0.0.1", 47321)
        receiver_conn = await transport.accept()

        sender_result, receiver_result = await asyncio.wait_for(
            asyncio.gather(
                _make_sender().run(sender_conn, src_files),
                _make_receiver(dst_dir).run(receiver_conn),
            ),
            timeout=15.0,
        )

        assert sender_result.ok
        assert receiver_result.ok
        assert len(sender_result.files) == 3
        assert len(receiver_result.files) == 3

        for name, data in files_data.items():
            received = dst_dir / name
            assert received.exists(), f"{name} not found"
            assert received.read_bytes() == data, f"{name} content mismatch"

    @pytest.mark.asyncio
    async def test_large_file_e2e(self, tmp_path: Path) -> None:
        """Send a file larger than one chunk and verify integrity."""
        src_dir = tmp_path / "src"
        dst_dir = tmp_path / "dst"
        src_dir.mkdir()
        dst_dir.mkdir()

        # ~500 KB = 2 chunks at default 256 KB chunk size
        data = os.urandom(500_000)
        src_file = src_dir / "large.bin"
        src_file.write_bytes(data)

        transport = FakeTransport()
        sender_conn = await transport.connect("127.0.0.1", 47321)
        receiver_conn = await transport.accept()

        sender_result, receiver_result = await asyncio.wait_for(
            asyncio.gather(
                _make_sender().run(sender_conn, [src_file]),
                _make_receiver(dst_dir).run(receiver_conn),
            ),
            timeout=15.0,
        )

        assert sender_result.ok
        assert receiver_result.ok
        assert (dst_dir / "large.bin").read_bytes() == data

    @pytest.mark.asyncio
    async def test_bytes_transferred_consistent(self, tmp_path: Path) -> None:
        """Sender and receiver agree on bytes transferred."""
        src_dir = tmp_path / "src"
        dst_dir = tmp_path / "dst"
        src_dir.mkdir()
        dst_dir.mkdir()

        data = b"consistency check " * 200
        src_file = src_dir / "check.bin"
        src_file.write_bytes(data)

        transport = FakeTransport()
        sender_conn = await transport.connect("127.0.0.1", 47321)
        receiver_conn = await transport.accept()

        sender_result, receiver_result = await asyncio.wait_for(
            asyncio.gather(
                _make_sender().run(sender_conn, [src_file]),
                _make_receiver(dst_dir).run(receiver_conn),
            ),
            timeout=15.0,
        )

        assert sender_result.bytes_transferred == len(data)
        assert receiver_result.bytes_transferred == len(data)


import os
