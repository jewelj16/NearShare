"""Tests for METADATA/ACCEPT/REJECT message build/parse and flow."""

import asyncio

import pytest

from engine.protocol.codec import MessageType
from engine.protocol.metadata import (
    MetadataError,
    build_accept_payload,
    build_metadata_payload,
    build_reject_payload,
    parse_accept_payload,
    parse_metadata_payload,
    parse_reject_payload,
    send_metadata,
    wait_for_accept_or_reject,
)
from engine.transport.fake import FakeTransport
from engine.types import DEFAULT_CHUNK_SIZE, FileMetadata


# helpers

def _file(name: str = "photo.jpg", size: int = 1024) -> FileMetadata:
    return FileMetadata(name=name, size=size)


# METADATA payload round-trip

class TestMetadataPayload:
    """Build and parse METADATA payloads."""

    def test_single_file_round_trip(self) -> None:
        files = [_file("a.txt", 500)]
        payload = build_metadata_payload("t-1", files)
        tid, parsed = parse_metadata_payload(payload)
        assert tid == "t-1"
        assert len(parsed) == 1
        assert parsed[0].name == "a.txt"
        assert parsed[0].size == 500

    def test_multiple_files(self) -> None:
        files = [_file("a.txt", 100), _file("b.png", 2000), _file("c.zip", 50000)]
        payload = build_metadata_payload("sess-42", files)
        tid, parsed = parse_metadata_payload(payload)
        assert tid == "sess-42"
        assert len(parsed) == 3
        assert [f.name for f in parsed] == ["a.txt", "b.png", "c.zip"]
        assert [f.size for f in parsed] == [100, 2000, 50000]

    def test_preserves_chunk_size(self) -> None:
        f = FileMetadata(name="big.bin", size=1_000_000, chunk_size=4096)
        payload = build_metadata_payload("t", [f])
        _, parsed = parse_metadata_payload(payload)
        assert parsed[0].chunk_size == 4096

    def test_preserves_sha256(self) -> None:
        sha = "a" * 64
        f = FileMetadata(name="f.bin", size=100, sha256=sha)
        payload = build_metadata_payload("t", [f])
        _, parsed = parse_metadata_payload(payload)
        assert parsed[0].sha256 == sha

    def test_none_sha256_round_trips(self) -> None:
        f = FileMetadata(name="f.bin", size=100)
        payload = build_metadata_payload("t", [f])
        _, parsed = parse_metadata_payload(payload)
        assert parsed[0].sha256 is None

    def test_mime_type_preserved(self) -> None:
        f = FileMetadata(name="img.jpg", size=500, mime_type="image/jpeg")
        payload = build_metadata_payload("t", [f])
        _, parsed = parse_metadata_payload(payload)
        assert parsed[0].mime_type == "image/jpeg"

    def test_empty_mime_becomes_none(self) -> None:
        f = FileMetadata(name="f.bin", size=100)
        payload = build_metadata_payload("t", [f])
        _, parsed = parse_metadata_payload(payload)
        assert parsed[0].mime_type is None

    def test_truncated_payload_raises(self) -> None:
        with pytest.raises(MetadataError):
            parse_metadata_payload(b"\x00")


# ACCEPT payload

class TestAcceptPayload:
    """Build and parse ACCEPT payloads."""

    def test_round_trip(self) -> None:
        payload = build_accept_payload("transfer-99")
        tid = parse_accept_payload(payload)
        assert tid == "transfer-99"

    def test_truncated_raises(self) -> None:
        with pytest.raises(MetadataError):
            parse_accept_payload(b"\x00")


# REJECT payload

class TestRejectPayload:
    """Build and parse REJECT payloads."""

    def test_round_trip(self) -> None:
        payload = build_reject_payload("t-1", reason_code=0x01, reason_msg="No thanks")
        tid, code, msg = parse_reject_payload(payload)
        assert tid == "t-1"
        assert code == 0x01
        assert msg == "No thanks"

    def test_no_space_reason(self) -> None:
        payload = build_reject_payload("t-2", reason_code=0x02, reason_msg="Disk full")
        _, code, msg = parse_reject_payload(payload)
        assert code == 0x02
        assert msg == "Disk full"

    def test_empty_reason_msg(self) -> None:
        payload = build_reject_payload("t-3", reason_code=0x03)
        _, _, msg = parse_reject_payload(payload)
        assert msg == ""


# full flow over FakeTransport

class TestMetadataFlow:
    """Send METADATA and receive ACCEPT/REJECT over FakeTransport."""

    @pytest.mark.asyncio
    async def test_send_metadata_accept_flow(self) -> None:
        transport = FakeTransport()
        sender = await transport.connect("10.0.0.1", 47321)
        receiver = await transport.accept()

        files = [_file("report.pdf", 5000)]

        async def sender_side() -> None:
            await send_metadata(sender, "xfer-1", files)
            tid, accepted, _, _ = await wait_for_accept_or_reject(sender)
            assert tid == "xfer-1"
            assert accepted is True

        async def receiver_side() -> None:
            frame = await receiver.recv_frame()
            assert frame.msg_type is MessageType.METADATA
            tid, parsed_files = parse_metadata_payload(frame.payload)
            assert tid == "xfer-1"
            assert parsed_files[0].name == "report.pdf"
            # send ACCEPT
            await receiver.send_frame(
                MessageType.ACCEPT, build_accept_payload(tid)
            )

        await asyncio.gather(sender_side(), receiver_side())

    @pytest.mark.asyncio
    async def test_send_metadata_reject_flow(self) -> None:
        transport = FakeTransport()
        sender = await transport.connect("10.0.0.1", 47321)
        receiver = await transport.accept()

        files = [_file("huge.iso", 10_000_000)]

        async def sender_side() -> None:
            await send_metadata(sender, "xfer-2", files)
            tid, accepted, code, msg = await wait_for_accept_or_reject(sender)
            assert tid == "xfer-2"
            assert accepted is False
            assert code == 0x02
            assert msg == "Not enough space"

        async def receiver_side() -> None:
            frame = await receiver.recv_frame()
            tid, _ = parse_metadata_payload(frame.payload)
            await receiver.send_frame(
                MessageType.REJECT,
                build_reject_payload(tid, 0x02, "Not enough space"),
            )

        await asyncio.gather(sender_side(), receiver_side())

    @pytest.mark.asyncio
    async def test_unexpected_message_raises(self) -> None:
        transport = FakeTransport()
        sender = await transport.connect("10.0.0.1", 47321)
        receiver = await transport.accept()

        async def sender_side() -> None:
            await send_metadata(sender, "xfer-3", [_file()])
            with pytest.raises(MetadataError, match="Expected ACCEPT or REJECT"):
                await wait_for_accept_or_reject(sender)

        async def receiver_side() -> None:
            await receiver.recv_frame()  # consume METADATA
            await receiver.send_frame(MessageType.CHUNK, b"wrong")

        await asyncio.gather(sender_side(), receiver_side())
