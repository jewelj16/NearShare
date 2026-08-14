"""Tests for FakeTransport/FakeConnection and FakeDiscovery.

Exercises configurable drop, delay, and duplicate behavior as well as
basic connection lifecycle and discovery peer injection/eviction.
"""

import asyncio
import time

import pytest

from engine.discovery.fake import FakeDiscovery
from engine.protocol.codec import Frame, MessageType
from engine.transport.fake import FakeConnection, FakeTransport, FaultConfig
from engine.types import DEFAULT_PORT, DeviceId, PeerInfo


# FakeTransport and FakeConnection

class TestFakeTransportBasic:
    """Basic connect/accept and send/recv round-trip."""

    @pytest.mark.asyncio
    async def test_connect_and_accept(self) -> None:
        transport = FakeTransport()
        client = await transport.connect("10.0.0.1", DEFAULT_PORT)
        server = await transport.accept()
        assert client.peer_address == ("10.0.0.1", DEFAULT_PORT)
        assert not client.closed
        assert not server.closed

    @pytest.mark.asyncio
    async def test_send_recv_round_trip(self) -> None:
        transport = FakeTransport()
        client = await transport.connect("10.0.0.1", DEFAULT_PORT)
        server = await transport.accept()

        await client.send_frame(MessageType.HELLO, b"ping")
        frame = await server.recv_frame()
        assert frame.msg_type is MessageType.HELLO
        assert frame.payload == b"ping"

    @pytest.mark.asyncio
    async def test_bidirectional(self) -> None:
        transport = FakeTransport()
        client = await transport.connect("10.0.0.1", DEFAULT_PORT)
        server = await transport.accept()

        await client.send_frame(MessageType.HELLO, b"from-client")
        await server.send_frame(MessageType.ACCEPT, b"from-server")

        server_got = await server.recv_frame()
        client_got = await client.recv_frame()

        assert server_got.payload == b"from-client"
        assert client_got.payload == b"from-server"

    @pytest.mark.asyncio
    async def test_multiple_frames_in_order(self) -> None:
        transport = FakeTransport()
        client = await transport.connect("10.0.0.1", DEFAULT_PORT)
        server = await transport.accept()

        for i in range(5):
            await client.send_frame(MessageType.CHUNK, f"chunk-{i}".encode())

        for i in range(5):
            frame = await server.recv_frame()
            assert frame.payload == f"chunk-{i}".encode()

    @pytest.mark.asyncio
    async def test_close_prevents_send(self) -> None:
        transport = FakeTransport()
        client = await transport.connect("10.0.0.1", DEFAULT_PORT)
        await client.close()
        assert client.closed
        with pytest.raises(OSError, match="closed"):
            await client.send_frame(MessageType.HELLO)

    @pytest.mark.asyncio
    async def test_close_prevents_recv(self) -> None:
        transport = FakeTransport()
        client = await transport.connect("10.0.0.1", DEFAULT_PORT)
        await client.close()
        with pytest.raises(OSError, match="closed"):
            await client.recv_frame()

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self) -> None:
        transport = FakeTransport()
        client = await transport.connect("10.0.0.1", DEFAULT_PORT)
        await client.close()
        await client.close()  # should not raise
        assert client.closed

    @pytest.mark.asyncio
    async def test_transport_close_prevents_connect(self) -> None:
        transport = FakeTransport()
        await transport.close()
        with pytest.raises(OSError, match="closed"):
            await transport.connect("10.0.0.1", DEFAULT_PORT)


# Fault injection: drop

class TestFakeTransportDrop:
    """Frame drop behavior."""

    @pytest.mark.asyncio
    async def test_full_drop_rate(self) -> None:
        """With drop_rate=1.0, nothing should arrive."""
        fault = FaultConfig(drop_rate=1.0, rng_seed=42)
        transport = FakeTransport(fault_config=fault)
        client = await transport.connect("10.0.0.1", DEFAULT_PORT)
        server = await transport.accept()

        for _ in range(10):
            await client.send_frame(MessageType.CHUNK, b"data")

        # give a moment for anything to propagate
        await asyncio.sleep(0.01)
        assert server._recv_queue.empty()

    @pytest.mark.asyncio
    async def test_zero_drop_rate(self) -> None:
        """With drop_rate=0, everything should arrive."""
        fault = FaultConfig(drop_rate=0.0, rng_seed=42)
        transport = FakeTransport(fault_config=fault)
        client = await transport.connect("10.0.0.1", DEFAULT_PORT)
        server = await transport.accept()

        for _ in range(5):
            await client.send_frame(MessageType.CHUNK, b"data")

        for _ in range(5):
            frame = await server.recv_frame()
            assert frame.payload == b"data"

    @pytest.mark.asyncio
    async def test_partial_drop_is_deterministic(self) -> None:
        """Same seed should produce the same drop pattern."""
        results: list[list[bool]] = []
        for _ in range(2):
            fault = FaultConfig(drop_rate=0.5, rng_seed=99)
            transport = FakeTransport(fault_config=fault)
            client = await transport.connect("10.0.0.1", DEFAULT_PORT)
            server = await transport.accept()

            for i in range(10):
                await client.send_frame(MessageType.CHUNK, f"{i}".encode())

            await asyncio.sleep(0.01)
            received = []
            while not server._recv_queue.empty():
                await server.recv_frame()
                received.append(True)
            results.append(received)

        assert results[0] == results[1], "Same seed should give same drop pattern"


# Fault injection: duplicate

class TestFakeTransportDuplicate:
    """Frame duplication behavior."""

    @pytest.mark.asyncio
    async def test_full_duplicate_rate(self) -> None:
        """With duplicate_rate=1.0, every frame should arrive twice."""
        fault = FaultConfig(duplicate_rate=1.0, rng_seed=42)
        transport = FakeTransport(fault_config=fault)
        client = await transport.connect("10.0.0.1", DEFAULT_PORT)
        server = await transport.accept()

        await client.send_frame(MessageType.HELLO, b"dup")
        await asyncio.sleep(0.01)

        frame1 = await server.recv_frame()
        frame2 = await server.recv_frame()
        assert frame1.payload == b"dup"
        assert frame2.payload == b"dup"


# Fault injection: delay

class TestFakeTransportDelay:
    """Frame delay behavior."""

    @pytest.mark.asyncio
    async def test_delay_adds_latency(self) -> None:
        """A delay_s should add measurable latency to send."""
        fault = FaultConfig(delay_s=0.05)
        transport = FakeTransport(fault_config=fault)
        client = await transport.connect("10.0.0.1", DEFAULT_PORT)
        server = await transport.accept()

        start = asyncio.get_event_loop().time()
        await client.send_frame(MessageType.HELLO, b"slow")
        elapsed = asyncio.get_event_loop().time() - start

        assert elapsed >= 0.04, f"Expected >= 40ms delay, got {elapsed:.3f}s"
        frame = await server.recv_frame()
        assert frame.payload == b"slow"


# FakeDiscovery

class TestFakeDiscovery:
    """Discovery service peer injection and liveness."""

    def _make_peer(self, device_id: str = "dev-1", name: str = "Test") -> PeerInfo:
        return PeerInfo(
            device_id=DeviceId(device_id),
            display_name=name,
            ip="192.168.1.10",
            port=DEFAULT_PORT,
        )

    @pytest.mark.asyncio
    async def test_start_stop(self) -> None:
        disc = FakeDiscovery()
        await disc.start()
        await disc.stop()
        # idempotent
        await disc.stop()

    @pytest.mark.asyncio
    async def test_inject_peer_shows_in_peers(self) -> None:
        disc = FakeDiscovery()
        await disc.start()
        peer = self._make_peer()
        await disc.inject_peer(peer)
        assert len(disc.peers()) == 1
        assert disc.peers()[0].device_id == DeviceId("dev-1")

    @pytest.mark.asyncio
    async def test_inject_stale_peer_not_visible(self) -> None:
        disc = FakeDiscovery()
        await disc.start()
        peer = self._make_peer("stale-dev")
        await disc.inject_stale_peer(peer)
        assert len(disc.peers()) == 0

    @pytest.mark.asyncio
    async def test_peers_returns_copy(self) -> None:
        disc = FakeDiscovery()
        await disc.start()
        await disc.inject_peer(self._make_peer("a"))
        snapshot = disc.peers()
        await disc.inject_peer(self._make_peer("b"))
        assert len(snapshot) == 1  # snapshot should not change

    @pytest.mark.asyncio
    async def test_inject_peer_sync(self) -> None:
        disc = FakeDiscovery()
        await disc.start()
        disc.inject_peer_sync(self._make_peer("sync-dev"))
        assert len(disc.peers()) == 1

    @pytest.mark.asyncio
    async def test_clear_removes_all(self) -> None:
        disc = FakeDiscovery()
        await disc.start()
        await disc.inject_peer(self._make_peer("a"))
        await disc.inject_peer(self._make_peer("b"))
        assert len(disc.peers()) == 2
        disc.clear()
        assert len(disc.peers()) == 0

    @pytest.mark.asyncio
    async def test_peer_events_yields_injected(self) -> None:
        disc = FakeDiscovery()
        await disc.start()

        peer = self._make_peer("event-dev")

        async def inject_after_delay() -> None:
            await asyncio.sleep(0.01)
            await disc.inject_peer(peer)
            await asyncio.sleep(0.05)
            await disc.stop()

        asyncio.create_task(inject_after_delay())

        received = []
        async for p in disc.peer_events():
            received.append(p)

        assert len(received) == 1
        assert received[0].device_id == DeviceId("event-dev")

    @pytest.mark.asyncio
    async def test_multiple_peers_unique_by_device_id(self) -> None:
        disc = FakeDiscovery()
        await disc.start()
        await disc.inject_peer(self._make_peer("dev-1", "First"))
        await disc.inject_peer(self._make_peer("dev-1", "Updated"))
        # same device_id, should be 1 peer with updated name
        peers = disc.peers()
        assert len(peers) == 1
        assert peers[0].display_name == "Updated"
