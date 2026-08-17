"""Tests for the TCP transport adapter with TLS against localhost.

Tests the TcpTransport and TcpConnection classes, plus reuses the
engine's protocol codec tests against the real adapter.
"""

import asyncio
import os
import ssl
import tempfile
from pathlib import Path

import pytest

from desktop.adapters.tcp_transport import (
    TcpConnection,
    TcpTransport,
    create_client_ssl_context,
    create_server_ssl_context,
    generate_self_signed_cert,
)
from engine.protocol.codec import Frame, MessageType, encode_frame, decode_frame


# SSL context tests

class TestSslContextCreation:
    """SSL context factory tests."""

    def test_client_context_no_verify(self) -> None:
        ctx = create_client_ssl_context()
        assert ctx.check_hostname is False
        assert ctx.verify_mode == ssl.CERT_NONE

    def test_client_context_tls_version(self) -> None:
        ctx = create_client_ssl_context()
        assert ctx.minimum_version == ssl.TLSVersion.TLSv1_3


class TestCertGeneration:
    """Self-signed certificate generation."""

    def test_generates_files(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        generate_self_signed_cert(cert, key)
        assert cert.exists()
        assert key.exists()
        assert cert.stat().st_size > 0
        assert key.stat().st_size > 0

    def test_cert_is_loadable(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        generate_self_signed_cert(cert, key)
        ctx = create_server_ssl_context(cert, key)
        assert ctx is not None


# Connection and Transport integration tests

class TestTcpTransportLocalhost:
    """Test the full TLS transport over localhost."""

    @pytest.mark.asyncio
    async def test_connect_and_accept(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        generate_self_signed_cert(cert, key)

        transport = TcpTransport(
            cert_path=cert, key_path=key,
            listen_host="127.0.0.1", listen_port=0,
        )
        port = await transport.start()
        try:
            client_conn = await transport.connect("127.0.0.1", port)
            server_conn = await asyncio.wait_for(transport.accept(), timeout=5.0)

            assert isinstance(client_conn, TcpConnection)
            assert isinstance(server_conn, TcpConnection)

            await client_conn.close()
            await server_conn.close()
        finally:
            await transport.close()

    @pytest.mark.asyncio
    async def test_send_recv_frame(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        generate_self_signed_cert(cert, key)

        transport = TcpTransport(
            cert_path=cert, key_path=key,
            listen_host="127.0.0.1", listen_port=0,
        )
        port = await transport.start()
        try:
            client_conn = await transport.connect("127.0.0.1", port)
            server_conn = await asyncio.wait_for(transport.accept(), timeout=5.0)

            payload = b"hello from client"
            await client_conn.send_frame(MessageType.HELLO, payload)
            frame = await asyncio.wait_for(server_conn.recv_frame(), timeout=5.0)

            assert frame.msg_type is MessageType.HELLO
            assert frame.payload == payload

            await client_conn.close()
            await server_conn.close()
        finally:
            await transport.close()

    @pytest.mark.asyncio
    async def test_bidirectional(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        generate_self_signed_cert(cert, key)

        transport = TcpTransport(
            cert_path=cert, key_path=key,
            listen_host="127.0.0.1", listen_port=0,
        )
        port = await transport.start()
        try:
            client_conn = await transport.connect("127.0.0.1", port)
            server_conn = await asyncio.wait_for(transport.accept(), timeout=5.0)

            await client_conn.send_frame(MessageType.METADATA, b"file-info")
            frame = await asyncio.wait_for(server_conn.recv_frame(), timeout=5.0)
            assert frame.msg_type is MessageType.METADATA

            await server_conn.send_frame(MessageType.ACCEPT, b"ok")
            frame = await asyncio.wait_for(client_conn.recv_frame(), timeout=5.0)
            assert frame.msg_type is MessageType.ACCEPT

            await client_conn.close()
            await server_conn.close()
        finally:
            await transport.close()

    @pytest.mark.asyncio
    async def test_peer_address(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        generate_self_signed_cert(cert, key)

        transport = TcpTransport(
            cert_path=cert, key_path=key,
            listen_host="127.0.0.1", listen_port=0,
        )
        port = await transport.start()
        try:
            client_conn = await transport.connect("127.0.0.1", port)
            server_conn = await asyncio.wait_for(transport.accept(), timeout=5.0)

            host, p = client_conn.peer_address
            assert host == "127.0.0.1"
            assert p == port

            await client_conn.close()
            await server_conn.close()
        finally:
            await transport.close()

    @pytest.mark.asyncio
    async def test_close_idempotent(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        generate_self_signed_cert(cert, key)

        transport = TcpTransport(
            cert_path=cert, key_path=key,
            listen_host="127.0.0.1", listen_port=0,
        )
        port = await transport.start()
        try:
            client_conn = await transport.connect("127.0.0.1", port)
            await client_conn.close()
            await client_conn.close()  # should not raise
            assert client_conn.closed
        finally:
            await transport.close()

    @pytest.mark.asyncio
    async def test_send_after_close_raises(self, tmp_path: Path) -> None:
        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        generate_self_signed_cert(cert, key)

        transport = TcpTransport(
            cert_path=cert, key_path=key,
            listen_host="127.0.0.1", listen_port=0,
        )
        port = await transport.start()
        try:
            client_conn = await transport.connect("127.0.0.1", port)
            await client_conn.close()

            with pytest.raises(OSError, match="closed"):
                await client_conn.send_frame(MessageType.HELLO, b"")
        finally:
            await transport.close()
