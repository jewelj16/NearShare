"""Interfaces for the NearShare discovery layer.

The discovery sub-system is responsible for finding peers on the local
network via UDP multicast/broadcast (see docs/architecture/protocol.md §6
and ADR-001).

These are structural Protocol classes (PEP 544); concrete implementations
need not inherit from them explicitly.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from engine.types import PeerInfo


@runtime_checkable
class DiscoveryService(Protocol):
    """Discovers and tracks peers on the local network.

    A DiscoveryService sends periodic UDP beacons announcing this device
    and listens for beacons from other NearShare peers.  Peers that have
    not been seen within ``DISCOVERY_TIMEOUT_S`` (15 s) are evicted from
    the active peer list.
    """

    async def start(self) -> None:
        """Start broadcasting beacons and listening for peer beacons.

        Must be called before :meth:`peers` or :meth:`peer_events` are
        used.  Calling ``start`` on an already-running service is a no-op.
        """
        ...

    async def stop(self) -> None:
        """Stop the discovery service and release the UDP socket.

        Must be safe to call more than once (idempotent).
        """
        ...

    def peers(self) -> list[PeerInfo]:
        """Return a snapshot of the currently visible peers.

        The returned list is a copy; subsequent peer additions or timeouts
        do not affect it.

        Returns:
            All peers whose last beacon arrived within
            ``DISCOVERY_TIMEOUT_S``.
        """
        ...

    def peer_events(self) -> AsyncIterator[PeerInfo]:
        """Yield a :class:`~engine.types.PeerInfo` whenever a peer appears or refreshes.

        This is an async generator / async iterable that yields once per
        received beacon (new peer or heartbeat from existing peer).  The
        caller is responsible for breaking out of the loop when done.

        Usage::

            async for peer in service.peer_events():
                ui.update_peer_list(peer)

        Yields:
            :class:`~engine.types.PeerInfo` for each beacon received.
        """
        ...
