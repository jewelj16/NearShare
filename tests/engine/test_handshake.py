"""Tests for HELLO handshake — version negotiation over FakeTransport."""

import asyncio
import struct

import pytest

from engine.protocol.codec import MessageType
from engine.protocol.handshake import (
    HandshakeError,
    build_hello_payload,
    parse_hello_payload,
    perform_handshake,
)
from engine.transport.fake import FakeTransport
from engine.types import PROTOCOL_VERSION, DeviceId


# payload build/parse round-trip

class TestHelloPayload:
    """Build and parse HELLO payload round-trip."""

    def test_round_trip(self) -> None:
        device_id = DeviceId("abc-123")
        payload = build_hello_payload(device_id, "My Device", proto_version=1)
        version, parsed_id, name = parse_hello_payload(payload)
        assert version == 1
        assert parsed_id == device_id
        assert name == "My Device"

    def test_unicode_display_name(self) -> None:
        device_id = DeviceId("dev-1")
        payload = build_hello_payload(device_id, "Élève's Laptop 📱")
        version, _, name = parse_hello_payload(payload)
        assert name == "Élève's Laptop 📱"

    def test_empty_display_name(self) -> None:
        device_id = DeviceId("dev-1")
        payload = build_hello_payload(device_id, "")
        _, _, name = parse_hello_payload(payload)
        assert name == ""

    def test_payload_too_short(self) -> None:
        with pytest.raises(HandshakeError, match="too short"):
            parse_hello_payload(b"\x00")

    def test_truncated_string(self) -> None:
        # version ok, but device_id string length says 100 bytes, only 2 available
        bad = struct.pack("!HH", 1, 100) + b"ab"
        with pytest.raises(HandshakeError, match="Truncated"):
            parse_hello_payload(bad)


# full handshake over FakeTransport

class TestHandshakeFlow:
    """End-to-end HELLO handshake using FakeTransport."""

    @pytest.mark.asyncio
    async def test_successful_handshake(self) -> None:
        transport = FakeTransport()
        client_conn = await transport.connect("10.0.0.1", 47321)
        server_conn = await transport.accept()

        async def server_side() -> None:
            await perform_handshake(
                server_conn,
                local_device_id=DeviceId("server-id"),
                local_display_name="Server",
            )

        async def client_side() -> None:
            peer = await perform_handshake(
                client_conn,
                local_device_id=DeviceId("client-id"),
                local_display_name="Client",
            )
            assert peer.device_id == DeviceId("server-id")
            assert peer.display_name == "Server"

        await asyncio.gather(client_side(), server_side())

    @pytest.mark.asyncio
    async def test_version_mismatch_raises(self) -> None:
        """When peer has a different protocol version, handshake must fail."""
        transport = FakeTransport()
        client_conn = await transport.connect("10.0.0.1", 47321)
        server_conn = await transport.accept()

        async def server_with_v2() -> None:
            # server claims version 2
            try:
                await perform_handshake(
                    server_conn,
                    local_device_id=DeviceId("server-id"),
                    local_display_name="Server",
                    local_proto_version=2,
                )
            except HandshakeError:
                pass  # expected on server side too

        async def client_with_v1() -> None:
            with pytest.raises(HandshakeError, match="version mismatch"):
                await perform_handshake(
                    client_conn,
                    local_device_id=DeviceId("client-id"),
                    local_display_name="Client",
                    local_proto_version=1,
                )

        await asyncio.gather(client_with_v1(), server_with_v2())

    @pytest.mark.asyncio
    async def test_peer_info_populated(self) -> None:
        """Returned PeerInfo should have correct fields from the peer's HELLO."""
        transport = FakeTransport()
        client_conn = await transport.connect("10.0.0.1", 47321)
        server_conn = await transport.accept()

        results = {}

        async def server_side() -> None:
            peer = await perform_handshake(
                server_conn,
                local_device_id=DeviceId("srv"),
                local_display_name="Server Box",
            )
            results["server_got"] = peer

        async def client_side() -> None:
            peer = await perform_handshake(
                client_conn,
                local_device_id=DeviceId("cli"),
                local_display_name="Client Phone",
            )
            results["client_got"] = peer

        await asyncio.gather(client_side(), server_side())

        client_got = results["client_got"]
        server_got = results["server_got"]

        assert client_got.device_id == DeviceId("srv")
        assert client_got.display_name == "Server Box"
        assert client_got.proto_version == PROTOCOL_VERSION

        assert server_got.device_id == DeviceId("cli")
        assert server_got.display_name == "Client Phone"

    @pytest.mark.asyncio
    async def test_error_frame_from_peer(self) -> None:
        """If the peer sends ERROR instead of HELLO, handshake should fail."""
        transport = FakeTransport()
        client_conn = await transport.connect("10.0.0.1", 47321)
        server_conn = await transport.accept()

        # server sends ERROR instead of HELLO
        async def bad_server() -> None:
            # consume client's HELLO first
            await server_conn.recv_frame()
            await server_conn.send_frame(MessageType.ERROR, b"\x00\x04")

        async def client_side() -> None:
            with pytest.raises(HandshakeError, match="ERROR"):
                await perform_handshake(
                    client_conn,
                    local_device_id=DeviceId("cli"),
                    local_display_name="Client",
                )

        await asyncio.gather(client_side(), bad_server())

    @pytest.mark.asyncio
    async def test_unexpected_message_type(self) -> None:
        """If the peer sends METADATA instead of HELLO, handshake should fail."""
        transport = FakeTransport()
        client_conn = await transport.connect("10.0.0.1", 47321)
        server_conn = await transport.accept()

        async def bad_server() -> None:
            await server_conn.recv_frame()
            await server_conn.send_frame(MessageType.METADATA, b"bogus")

        async def client_side() -> None:
            with pytest.raises(HandshakeError, match="Expected HELLO"):
                await perform_handshake(
                    client_conn,
                    local_device_id=DeviceId("cli"),
                    local_display_name="Client",
                )

        await asyncio.gather(client_side(), bad_server())
