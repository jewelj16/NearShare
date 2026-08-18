"""Tests for desktop.network.wifi_state — Wi-Fi snapshot and restore."""

import subprocess
from unittest.mock import patch, MagicMock

import pytest

from desktop.network.wifi_state import (
    find_wifi_interface,
    take_wifi_snapshot,
    restore_wifi,
    WifiSnapshot,
)


class TestFindWifiInterface:
    """Tests for find_wifi_interface()."""

    def test_returns_wifi_interface(self) -> None:
        mock_output = "wlo1:wifi\nenp44s0:ethernet\nlo:loopback\n"
        with patch("desktop.network.wifi_state.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=mock_output)
            assert find_wifi_interface() == "wlo1"

    def test_returns_none_when_no_wifi(self) -> None:
        mock_output = "enp44s0:ethernet\nlo:loopback\n"
        with patch("desktop.network.wifi_state.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=mock_output)
            assert find_wifi_interface() is None

    def test_returns_none_when_nmcli_missing(self) -> None:
        with patch("desktop.network.wifi_state.subprocess.run", side_effect=FileNotFoundError):
            assert find_wifi_interface() is None


class TestTakeWifiSnapshot:
    """Tests for take_wifi_snapshot()."""

    def test_captures_active_wifi(self) -> None:
        active_output = "MyHomeWifi:wlo1:802-11-wireless\nWired:enp44s0:802-3-ethernet\n"
        ssid_output = "802-11-wireless.ssid:MyHomeWifi\n"

        with patch("desktop.network.wifi_state.subprocess.run") as mock_run:
            mock_run.side_effect = [
                MagicMock(stdout=active_output),
                MagicMock(stdout=ssid_output),
            ]
            snap = take_wifi_snapshot()
            assert snap is not None
            assert snap.connection_name == "MyHomeWifi"
            assert snap.interface == "wlo1"
            assert snap.ssid == "MyHomeWifi"

    def test_returns_none_when_no_wifi_active(self) -> None:
        active_output = "Wired:enp44s0:802-3-ethernet\n"
        with patch("desktop.network.wifi_state.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout=active_output)
            assert take_wifi_snapshot() is None


class TestRestoreWifi:
    """Tests for restore_wifi()."""

    def test_restore_calls_nmcli(self) -> None:
        snap = WifiSnapshot(connection_name="MyHomeWifi", interface="wlo1", ssid="MyHomeWifi")
        with patch("desktop.network.wifi_state.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock()
            assert restore_wifi(snap) is True
            mock_run.assert_called_once_with(
                ["nmcli", "connection", "up", "MyHomeWifi"],
                capture_output=True, text=True, check=True,
            )

    def test_restore_returns_false_on_failure(self) -> None:
        snap = WifiSnapshot(connection_name="MyHomeWifi", interface="wlo1", ssid="MyHomeWifi")
        with patch("desktop.network.wifi_state.subprocess.run",
                    side_effect=subprocess.CalledProcessError(1, "nmcli", stderr="error")):
            assert restore_wifi(snap) is False
