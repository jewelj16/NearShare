"""In-memory fake discovery service for deterministic testing.

FakeDiscovery implements the DiscoveryService interface entirely in-memory.
Peers can be injected manually and peer_events() yields them via an
asyncio.Queue, making tests fully deterministic without real UDP.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator
from typing import AsyncGenerator

from engine.types import DISCOVERY_TIMEOUT_S, PeerInfo


class FakeDiscovery:
    """In-memory discovery service for testing.

    Instead of broadcasting on the network, peers are added manually
    via inject_peer(). The peer_events() async iterator yields each
    injected peer, and peers() returns a snapshot filtered by liveness.
    """

    def __init__(self, timeout_s: float = DISCOVERY_TIMEOUT_S) -> None:
        self._peers: dict[str, PeerInfo] = {}  # device_id -> PeerInfo
        self._event_queue: asyncio.Queue[PeerInfo] = asyncio.Queue()
        self._running = False
        self._timeout_s = timeout_s

    async def start(self) -> None:
        """Start the fake discovery service (idempotent)."""
        self._running = True

    async def stop(self) -> None:
        """Stop the fake discovery service (idempotent)."""
        self._running = False

    def peers(self) -> list[PeerInfo]:
        """Return currently visible peers (filtered by liveness timeout)."""
        now = time.monotonic()
        return [
            p for p in self._peers.values()
            if (now - p.last_seen) < self._timeout_s
        ]

    async def peer_events(self) -> AsyncGenerator[PeerInfo, None]:
        """Yield peers as they are injected. Stops when the service is stopped."""
        while self._running:
            try:
                peer = await asyncio.wait_for(self._event_queue.get(), timeout=0.1)
                yield peer
            except asyncio.TimeoutError:
                continue

    # test helpers

    async def inject_peer(self, peer: PeerInfo) -> None:
        """Simulate a peer beacon arriving.

        Updates last_seen to now and pushes to peer_events().
        """
        peer.last_seen = time.monotonic()
        self._peers[peer.device_id] = peer
        await self._event_queue.put(peer)

    def inject_peer_sync(self, peer: PeerInfo) -> None:
        """Synchronous version of inject_peer for simple test setups."""
        peer.last_seen = time.monotonic()
        self._peers[peer.device_id] = peer
        self._event_queue.put_nowait(peer)

    async def inject_stale_peer(self, peer: PeerInfo) -> None:
        """Inject a peer with a timestamp old enough to be expired."""
        peer.last_seen = time.monotonic() - self._timeout_s - 1.0
        self._peers[peer.device_id] = peer

    def clear(self) -> None:
        """Remove all known peers."""
        self._peers.clear()
