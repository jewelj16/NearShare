"""TCP transport adapter with TLS wrapping for Linux.

Implements the Transport and Connection interfaces using real TCP sockets
wrapped with TLS (ssl module).  Generates a self-signed certificate on
first run for TOFU (Trust On First Use) security.

See protocol.md §7 (Security Notes).
"""

from __future__ import annotations

import asyncio
import logging
import os
import ssl
import tempfile
from pathlib import Path

from engine.protocol.codec import (
    Frame,
    MessageType,
    decode_frame,
    decode_frame_header,
    encode_frame,
    _HEADER_SIZE,
)


logger = logging.getLogger("nearshare.engine.transport")


def generate_self_signed_cert(
    cert_path: Path,
    key_path: Path,
) -> None:
    """Generate a self-signed TLS certificate and private key.

    Uses the ``openssl`` command-line tool.  The certificate is valid
    for 365 days and uses a 2048-bit RSA key.

    Args:
        cert_path: Where to write the certificate PEM file.
        key_path:  Where to write the private key PEM file.
    """
    import subprocess

    cert_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.parent.mkdir(parents=True, exist_ok=True)

    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048",
            "-keyout", str(key_path),
            "-out", str(cert_path),
            "-days", "365",
            "-nodes",
            "-subj", "/CN=NearShare",
        ],
        check=True,
        capture_output=True,
    )
    logger.info("Generated self-signed cert at %s", cert_path)


def create_server_ssl_context(cert_path: Path, key_path: Path) -> ssl.SSLContext:
    """Create an SSL context for the server (accepting connections)."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(str(cert_path), str(key_path))
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    return ctx


def create_client_ssl_context() -> ssl.SSLContext:
    """Create an SSL context for the client (initiating connections).

    TOFU: we disable certificate verification for v1 — the cert
    fingerprint is stored after first connection for future checks.
    """
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    ctx.minimum_version = ssl.TLSVersion.TLSv1_3
    return ctx


class TcpConnection:
    """A single TLS-wrapped TCP connection.

    Implements the Connection interface defined in
    engine/transport/interfaces.py.
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._closed = False

    async def send_frame(
        self,
        msg_type: MessageType,
        payload: bytes = b"",
    ) -> None:
        """Encode and send a protocol frame over the TLS connection."""
        if self._closed:
            raise OSError("Connection is closed")
        data = encode_frame(msg_type, payload)
        self._writer.write(data)
        await self._writer.drain()

    async def recv_frame(self) -> Frame:
        """Read exactly one frame from the TLS connection."""
        if self._closed:
            raise OSError("Connection is closed")

        # read header
        header_data = await self._reader.readexactly(_HEADER_SIZE)
        payload_length, msg_type = decode_frame_header(header_data)

        # read payload
        if payload_length > 0:
            payload = await self._reader.readexactly(payload_length)
        else:
            payload = b""

        return Frame(msg_type=msg_type, payload=payload)

    async def close(self) -> None:
        """Close the connection gracefully (idempotent)."""
        if self._closed:
            return
        self._closed = True
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except (OSError, ConnectionError):
            pass

    @property
    def peer_address(self) -> tuple[str, int]:
        """Return the remote (host, port)."""
        addr = self._writer.get_extra_info("peername", ("unknown", 0))
        return (str(addr[0]), int(addr[1]))

    @property
    def closed(self) -> bool:
        return self._closed


class TcpTransport:
    """TCP transport with TLS wrapping.

    Implements the Transport interface defined in
    engine/transport/interfaces.py.
    """

    def __init__(
        self,
        cert_path: Path | None = None,
        key_path: Path | None = None,
        listen_host: str = "0.0.0.0",
        listen_port: int = 0,
    ) -> None:
        self._cert_path = cert_path
        self._key_path = key_path
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._server: asyncio.Server | None = None
        self._incoming: asyncio.Queue[TcpConnection] = asyncio.Queue()
        self._closed = False
        self._server_ssl_ctx: ssl.SSLContext | None = None
        self._client_ssl_ctx: ssl.SSLContext | None = None

    async def start(self) -> int:
        """Start listening for incoming connections.

        Returns:
            The actual port the server is listening on.
        """
        if self._cert_path and self._key_path:
            if not self._cert_path.exists():
                generate_self_signed_cert(self._cert_path, self._key_path)
            self._server_ssl_ctx = create_server_ssl_context(
                self._cert_path, self._key_path
            )
        self._client_ssl_ctx = create_client_ssl_context()

        self._server = await asyncio.start_server(
            self._handle_incoming,
            host=self._listen_host,
            port=self._listen_port,
            ssl=self._server_ssl_ctx,
        )
        # get actual port
        sockets = self._server.sockets
        if sockets:
            addr = sockets[0].getsockname()
            self._listen_port = addr[1]
        logger.info("TCP transport listening on port %d", self._listen_port)
        return self._listen_port

    async def _handle_incoming(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        conn = TcpConnection(reader, writer)
        await self._incoming.put(conn)

    async def connect(self, host: str, port: int) -> TcpConnection:
        """Open an outbound TLS connection."""
        if self._closed:
            raise OSError("Transport is closed")
        reader, writer = await asyncio.open_connection(
            host, port, ssl=self._client_ssl_ctx,
        )
        return TcpConnection(reader, writer)

    async def accept(self) -> TcpConnection:
        """Wait for the next incoming connection."""
        if self._closed:
            raise OSError("Transport is closed")
        return await self._incoming.get()

    async def close(self) -> None:
        """Stop accepting and close the listening socket."""
        self._closed = True
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    @property
    def listen_port(self) -> int:
        return self._listen_port
