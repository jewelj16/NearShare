"""Tests for desktop.commands.receive_cmd — auto-discovery receive flow."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from desktop.commands.receive_cmd import run_receive_auto
from engine.types import DeviceId


DEVICE_ID = DeviceId("test-recv-device")


class TestRunReceiveAuto:
    """Tests for the auto-discovery receive command."""

    @pytest.mark.asyncio
    async def test_returns_error_when_no_hotspot_found(self, tmp_path: Path) -> None:
        """If scan_and_connect returns None, should return 1."""
        with patch("desktop.commands.receive_cmd.take_wifi_snapshot", return_value=None), \
             patch("desktop.commands.receive_cmd.scan_and_connect", return_value=None):

            result = await run_receive_auto(
                device_id=DEVICE_ID,
                display_name="TestBox",
                save_dir=tmp_path,
                scan_timeout=1.0,
            )
            assert result == 1

    @pytest.mark.asyncio
    async def test_returns_error_on_tcp_connect_failure(self, tmp_path: Path) -> None:
        """If TCP connect to gateway fails, should return 1."""
        mock_transport = MagicMock()
        mock_transport.connect = AsyncMock(side_effect=OSError("Connection refused"))

        with patch("desktop.commands.receive_cmd.take_wifi_snapshot", return_value=None), \
             patch("desktop.commands.receive_cmd.scan_and_connect", return_value="NearShare-abcd"), \
             patch("desktop.commands.receive_cmd.disconnect_from_hotspot"), \
             patch("desktop.commands.receive_cmd.TcpTransport", return_value=mock_transport), \
             patch("desktop.commands.receive_cmd.asyncio.sleep", new_callable=AsyncMock):

            result = await run_receive_auto(
                device_id=DEVICE_ID,
                display_name="TestBox",
                save_dir=tmp_path,
            )
            assert result == 1

    @pytest.mark.asyncio
    async def test_successful_receive(self, tmp_path: Path) -> None:
        """Happy path: connect to hotspot, receive files, cleanup."""
        mock_conn = MagicMock()
        mock_transport = MagicMock()
        mock_transport.connect = AsyncMock(return_value=mock_conn)

        mock_result = MagicMock(ok=True, __str__=lambda s: "OK")
        mock_receiver = MagicMock()
        mock_receiver.run = AsyncMock(return_value=mock_result)

        with patch("desktop.commands.receive_cmd.take_wifi_snapshot", return_value=None), \
             patch("desktop.commands.receive_cmd.scan_and_connect", return_value="NearShare-abcd"), \
             patch("desktop.commands.receive_cmd.disconnect_from_hotspot") as mock_disconnect, \
             patch("desktop.commands.receive_cmd.TcpTransport", return_value=mock_transport), \
             patch("desktop.commands.receive_cmd.TransferReceiver", return_value=mock_receiver), \
             patch("desktop.commands.receive_cmd.asyncio.sleep", new_callable=AsyncMock):

            result = await run_receive_auto(
                device_id=DEVICE_ID,
                display_name="TestBox",
                save_dir=tmp_path,
                auto_accept=True,
            )
            assert result == 0
            mock_disconnect.assert_called_once_with("NearShare-abcd")
