"""UDP discovery adapter for Linux — real sockets.

Implements the DiscoveryService interface using real UDP sockets for
peer discovery on the local network.  Sends periodic beacons and listens
for beacons from other NearShare peers.

See protocol.md §6 (Discovery Protocol).
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import struct
import time
from collections.abc import AsyncIterator
from typing import AsyncGenerator

from engine.types import (
    DEFAULT_PORT,
    DISCOVERY_TIMEOUT_S,
    DeviceId,
    PeerInfo,
    PROTOCOL_VERSION,
)


logger = logging.getLogger("nearshare.engine.discovery")

_BEACON_INTERVAL_S = 5.0
_MULTICAST_GROUP = "224.0.0.167"
_MULTICAST_TTL = 2


class UdpDiscovery:
    """Real UDP multicast discovery service.

    Broadcasts beacon packets on the multicast group and listens for
    beacons from other peers.  Peers not seen within DISCOVERY_TIMEOUT_S
    are evicted automatically.

    Attributes:
        device_id:     Our device ID announced in beacons.
        display_name:  Our display name.
        port:          TCP port to advertise for incoming connections.
    """

    def __init__(
        self,
        device_id: DeviceId,
        display_name: str,
        tcp_port: int = DEFAULT_PORT,
        beacon_interval: float = _BEACON_INTERVAL_S,
        timeout: float = DISCOVERY_TIMEOUT_S,
        listen_port: int = DEFAULT_PORT,
    ) -> None:
        self._device_id = device_id
        self._display_name = display_name
        self._tcp_port = tcp_port
        self._beacon_interval = beacon_interval
        self._timeout = timeout
        self._listen_port = listen_port

        self._peers: dict[str, PeerInfo] = {}
        self._event_queue: asyncio.Queue[PeerInfo] = asyncio.Queue()
        self._running = False
        self._send_task: asyncio.Task[None] | None = None
        self._recv_task: asyncio.Task[None] | None = None
        self._sock: socket.socket | None = None

    async def start(self) -> None:
        """Start broadcasting and listening for beacons."""
        if self._running:
            return

        self._running = True
        self._sock = self._create_socket()
        self._send_task = asyncio.create_task(self._beacon_loop())
        self._recv_task = asyncio.create_task(self._listen_loop())
        logger.info("UDP discovery started on port %d", self._listen_port)

    async def stop(self) -> None:
        """Stop discovery and release the socket."""
        self._running = False
        if self._send_task:
            self._send_task.cancel()
            try:
                await self._send_task
            except asyncio.CancelledError:
                pass
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._sock:
            self._sock.close()
            self._sock = None
        logger.info("UDP discovery stopped")

    def peers(self) -> list[PeerInfo]:
        """Return a snapshot of currently visible peers."""
        now = time.monotonic()
        return [
            p for p in self._peers.values()
            if (now - p.last_seen) < self._timeout
        ]

    async def peer_events(self) -> AsyncGenerator[PeerInfo, None]:
        """Yield PeerInfo for each beacon received."""
        while self._running:
            try:
                peer = await asyncio.wait_for(
                    self._event_queue.get(), timeout=0.5
                )
                yield peer
            except asyncio.TimeoutError:
                continue

    def _create_socket(self) -> socket.socket:
        """Create and configure the multicast UDP socket."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        # allow multiple processes on same host (for testing)
        if hasattr(socket, "SO_REUSEPORT"):
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)

        sock.bind(("", self._listen_port))

        # join multicast group
        mreq = struct.pack(
            "4sL",
            socket.inet_aton(_MULTICAST_GROUP),
            socket.INADDR_ANY,
        )
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        # set TTL for outgoing multicast
        sock.setsockopt(
            socket.IPPROTO_IP,
            socket.IP_MULTICAST_TTL,
            _MULTICAST_TTL,
        )

        sock.setblocking(False)
        return sock

    def _build_beacon(self) -> bytes:
        """Build a JSON beacon payload."""
        data = {
            "proto_version": PROTOCOL_VERSION,
            "device_id": self._device_id,
            "display_name": self._display_name,
            "tcp_port": self._tcp_port,
        }
        return json.dumps(data).encode("utf-8")

    @staticmethod
    def _parse_beacon(data: bytes) -> PeerInfo | None:
        """Parse a beacon payload, returning PeerInfo or None on failure."""
        try:
            obj = json.loads(data.decode("utf-8"))
            return PeerInfo(
                device_id=DeviceId(obj["device_id"]),
                display_name=obj["display_name"],
                ip="",  # filled in by the listen loop from recvfrom
                port=obj["tcp_port"],
                proto_version=obj["proto_version"],
                last_seen=time.monotonic(),
            )
        except (json.JSONDecodeError, KeyError, UnicodeDecodeError):
            return None

    async def _beacon_loop(self) -> None:
        """Periodically broadcast our beacon."""
        loop = asyncio.get_event_loop()
        while self._running and self._sock:
            try:
                beacon = self._build_beacon()
                await loop.sock_sendto(
                    self._sock,
                    beacon,
                    (_MULTICAST_GROUP, self._listen_port),
                )
            except OSError as e:
                logger.warning("Beacon send failed: %s", e)
            await asyncio.sleep(self._beacon_interval)

    async def _listen_loop(self) -> None:
        """Listen for incoming beacons."""
        loop = asyncio.get_event_loop()
        while self._running and self._sock:
            try:
                data, addr = await loop.sock_recvfrom(self._sock, 4096)
            except OSError:
                if self._running:
                    await asyncio.sleep(0.1)
                continue

            peer = self._parse_beacon(data)
            if peer is None:
                continue

            # skip our own beacons
            if peer.device_id == self._device_id:
                continue

            peer.ip = addr[0]
            peer.last_seen = time.monotonic()
            self._peers[peer.device_id] = peer
            await self._event_queue.put(peer)
