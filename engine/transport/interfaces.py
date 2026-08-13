"""Interfaces for the NearShare transport layer.

These are structural Protocol classes (PEP 544).  Any concrete implementation
(e.g. TcpConnection, TlsTcpTransport) only needs to satisfy the method
signatures — no explicit inheritance required.

See docs/architecture/protocol.md §4 for the connection and session flow that
these interfaces support.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from engine.protocol.codec import Frame, MessageType


@runtime_checkable
class Connection(Protocol):
    """A single bidirectional connection to a remote peer.

    Wraps one accepted or initiated TCP/TLS socket.  All methods are
    coroutines so that the asyncio event loop is never blocked.
    """

    async def send_frame(self, msg_type: MessageType, payload: bytes = b"") -> None:
        """Encode and send a single protocol frame.

        Args:
            msg_type: The message type code for the frame header.
            payload:  Raw payload bytes (empty by default).

        Raises:
            OSError:      If the underlying socket write fails.
            CodecError:   If the payload exceeds MAX_PAYLOAD_SIZE.
        """
        ...

    async def recv_frame(self) -> Frame:
        """Read exactly one protocol frame from the peer.

        Reads the 5-byte header first, then the payload, so the event loop
        yields between the two reads on slow connections.

        Returns:
            The decoded Frame.

        Raises:
            OSError:       If the underlying socket read fails or the
                           connection was closed cleanly (EOF).
            CodecError:    If the frame is malformed.
        """
        ...

    async def close(self) -> None:
        """Close the connection gracefully.

        Must be safe to call more than once (idempotent).
        """
        ...

    @property
    def peer_address(self) -> tuple[str, int]:
        """The remote (host, port) as reported by the OS.

        Returns:
            A ``(host, port)`` tuple, e.g. ``('192.168.1.10', 47321)``.
        """
        ...


@runtime_checkable
class Transport(Protocol):
    """Factory for outgoing connections and listener for incoming ones.

    A Transport owns a listening socket and can also initiate outbound
    connections.  Concrete implementations handle TLS wrapping, certificate
    pinning, etc.
    """

    async def connect(self, host: str, port: int) -> Connection:
        """Open an outbound connection to *host*:*port*.

        Args:
            host: IPv4/IPv6 address or hostname of the remote peer.
            port: TCP port to connect to.

        Returns:
            An established :class:`Connection`.

        Raises:
            OSError: If the connection cannot be established.
        """
        ...

    async def accept(self) -> Connection:
        """Wait for and accept one incoming connection.

        Callers typically run this in a loop inside a task:

        .. code-block:: python

            while True:
                conn = await transport.accept()
                asyncio.create_task(handle(conn))

        Returns:
            The accepted :class:`Connection`.

        Raises:
            OSError: If the listening socket is closed or encounters an error.
        """
        ...

    async def close(self) -> None:
        """Stop accepting new connections and release the listening socket.

        Must be safe to call more than once (idempotent).
        """
        ...
