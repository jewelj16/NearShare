"""Tests for desktop.commands.init_cmd — init command flow."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from desktop.commands.init_cmd import run_init
from engine.types import DeviceId


DEVICE_ID = DeviceId("test-init-device")


class TestRunInit:
    """Tests for the init command orchestration."""

    @pytest.mark.asyncio
    async def test_returns_error_when_no_wifi_interface(self, tmp_path: Path) -> None:
        """If create_hotspot fails (no wifi), init should return 1."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        with patch("desktop.commands.init_cmd.take_wifi_snapshot", return_value=None), \
             patch("desktop.commands.init_cmd.create_hotspot",
                   side_effect=RuntimeError("No Wi-Fi interface found")):
            result = await run_init(
                files=[test_file],
                device_id=DEVICE_ID,
                display_name="TestBox",
            )
            assert result == 1

    @pytest.mark.asyncio
    async def test_cleanup_called_on_success(self, tmp_path: Path) -> None:
        """Verify hotspot teardown and wifi restore happen after transfer."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        mock_hotspot = MagicMock(
            ssid="NearShare-abcd",
            password="NearShare2026",
            gateway_ip="10.42.0.1",
            connection_name="NearShare-abcd",
        )
        mock_conn = MagicMock()
        mock_conn.peer_address = ("10.42.0.2", 12345)

        mock_transport = MagicMock()
        mock_transport.start = AsyncMock(return_value=47321)
        mock_transport.accept = AsyncMock(return_value=mock_conn)
        mock_transport.close = AsyncMock()

        mock_result = MagicMock(ok=True, __str__=lambda s: "OK")

        mock_sender = MagicMock()
        mock_sender.run = AsyncMock(return_value=mock_result)

        with patch("desktop.commands.init_cmd.take_wifi_snapshot", return_value=None), \
             patch("desktop.commands.init_cmd.create_hotspot", return_value=mock_hotspot), \
             patch("desktop.commands.init_cmd.teardown_hotspot") as mock_teardown, \
             patch("desktop.commands.init_cmd.TcpTransport", return_value=mock_transport), \
             patch("desktop.commands.init_cmd.TransferSender", return_value=mock_sender):

            result = await run_init(
                files=[test_file],
                device_id=DEVICE_ID,
                display_name="TestBox",
            )

            assert result == 0
            mock_teardown.assert_called_once_with(mock_hotspot)

    @pytest.mark.asyncio
    async def test_returns_error_on_timeout(self, tmp_path: Path) -> None:
        """If no receiver connects within timeout, init should return 1."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello")

        mock_hotspot = MagicMock(
            ssid="NearShare-abcd",
            password="NearShare2026",
            gateway_ip="10.42.0.1",
            connection_name="NearShare-abcd",
        )

        mock_transport = MagicMock()
        mock_transport.start = AsyncMock(return_value=47321)
        mock_transport.accept = AsyncMock(side_effect=asyncio.TimeoutError)
        mock_transport.close = AsyncMock()

        with patch("desktop.commands.init_cmd.take_wifi_snapshot", return_value=None), \
             patch("desktop.commands.init_cmd.create_hotspot", return_value=mock_hotspot), \
             patch("desktop.commands.init_cmd.teardown_hotspot"), \
             patch("desktop.commands.init_cmd.TcpTransport", return_value=mock_transport):

            result = await run_init(
                files=[test_file],
                device_id=DEVICE_ID,
                display_name="TestBox",
            )
            assert result == 1
