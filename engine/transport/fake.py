"""In-memory fake transport for deterministic testing.

FakeTransport and FakeConnection implement the Transport/Connection
interfaces entirely in-memory using asyncio.Queue, with configurable
fault injection (drop, delay, duplicate frames).
"""

from __future__ import annotations

import asyncio
import copy
import random
from dataclasses import dataclass, field
from typing import Callable

from engine.protocol.codec import Frame, MessageType, encode_frame, decode_frame


# Fault injection configuration

@dataclass
class FaultConfig:
    """Configurable fault injection for a FakeConnection.

    Attributes:
        drop_rate:      Probability [0.0, 1.0] that a sent frame is silently dropped.
        duplicate_rate: Probability [0.0, 1.0] that a sent frame is duplicated.
        delay_s:        Fixed delay in seconds added before delivery (0 = instant).
        rng_seed:       Seed for the random number generator (None = non-deterministic).
    """

    drop_rate: float = 0.0
    duplicate_rate: float = 0.0
    delay_s: float = 0.0
    rng_seed: int | None = None


class FakeConnection:
    """In-memory connection backed by a pair of asyncio.Queues.

    Each connection has a send queue and a receive queue. Two connections
    are linked by making one's send queue the other's receive queue.
    """

    def __init__(
        self,
        send_queue: asyncio.Queue[bytes],
        recv_queue: asyncio.Queue[bytes],
        peer_addr: tuple[str, int],
        fault_config: FaultConfig | None = None,
    ) -> None:
        self._send_queue = send_queue
        self._recv_queue = recv_queue
        self._peer_addr = peer_addr
        self._closed = False
        self._fault = fault_config or FaultConfig()
        self._rng = random.Random(self._fault.rng_seed)

    async def send_frame(self, msg_type: MessageType, payload: bytes = b"") -> None:
        """Encode and enqueue a frame, applying fault injection."""
        if self._closed:
            raise OSError("Connection is closed")

        raw = encode_frame(msg_type, payload)

        # fault: drop
        if self._fault.drop_rate > 0 and self._rng.random() < self._fault.drop_rate:
            return  # silently dropped

        # fault: delay
        if self._fault.delay_s > 0:
            await asyncio.sleep(self._fault.delay_s)

        # fault: duplicate
        if self._fault.duplicate_rate > 0 and self._rng.random() < self._fault.duplicate_rate:
            await self._send_queue.put(raw)

        await self._send_queue.put(raw)

    async def recv_frame(self) -> Frame:
        """Dequeue and decode exactly one frame."""
        if self._closed:
            raise OSError("Connection is closed")

        try:
            raw = await self._recv_queue.get()
        except asyncio.CancelledError:
            raise OSError("Connection read cancelled")

        return decode_frame(raw)

    async def close(self) -> None:
        """Mark the connection as closed (idempotent)."""
        self._closed = True

    @property
    def peer_address(self) -> tuple[str, int]:
        return self._peer_addr

    @property
    def closed(self) -> bool:
        return self._closed


class FakeTransport:
    """In-memory transport that creates linked FakeConnection pairs.

    Call connect() on the client side; the server side gets connections
    via accept(). All connections are local asyncio.Queue pairs.
    """

    def __init__(self, fault_config: FaultConfig | None = None) -> None:
        self._incoming: asyncio.Queue[FakeConnection] = asyncio.Queue()
        self._fault_config = fault_config
        self._closed = False

    async def connect(self, host: str, port: int) -> FakeConnection:
        """Create a connected pair and push the server side to accept()."""
        if self._closed:
            raise OSError("Transport is closed")

        # two queues form the bidirectional channel
        client_to_server: asyncio.Queue[bytes] = asyncio.Queue()
        server_to_client: asyncio.Queue[bytes] = asyncio.Queue()

        client_conn = FakeConnection(
            send_queue=client_to_server,
            recv_queue=server_to_client,
            peer_addr=(host, port),
            fault_config=self._fault_config,
        )
        server_conn = FakeConnection(
            send_queue=server_to_client,
            recv_queue=client_to_server,
            peer_addr=("127.0.0.1", 0),  # fake client address
            fault_config=self._fault_config,
        )

        await self._incoming.put(server_conn)
        return client_conn

    async def accept(self) -> FakeConnection:
        """Wait for the next incoming connection."""
        if self._closed:
            raise OSError("Transport is closed")
        return await self._incoming.get()

    async def close(self) -> None:
        """Stop accepting new connections (idempotent)."""
        self._closed = True
