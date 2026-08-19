"""Tests for desktop.network.scanner — Wi-Fi scanning and auto-connect."""

import subprocess
from unittest.mock import patch, MagicMock, call

import pytest

from desktop.network.scanner import (
    ScannedNetwork,
    connect_to_hotspot,
    disconnect_from_hotspot,
    find_nearshare_hotspots,
    scan_wifi_networks,
)


MOCK_SCAN_OUTPUT = (
    "NearShare-7f3a:85:WPA2\n"
    "HomeWifi:72:WPA2\n"
    "NearShare-ab12:60:WPA2\n"
    "CoffeeShop:45:WPA1\n"
)


class TestScanWifiNetworks:
    """Tests for scan_wifi_networks()."""

    @patch("desktop.network.scanner.time.sleep")
    @patch("desktop.network.scanner.subprocess.run")
    def test_returns_networks_sorted_by_signal(
        self, mock_run: MagicMock, _sleep: MagicMock
    ) -> None:
        mock_run.side_effect = [
            MagicMock(),  # rescan call
            MagicMock(stdout=MOCK_SCAN_OUTPUT),  # list call
        ]
        networks = scan_wifi_networks()
        assert len(networks) == 4
        assert networks[0].ssid == "NearShare-7f3a"
        assert networks[0].signal == 85
        assert networks[-1].signal == 45

    @patch("desktop.network.scanner.time.sleep")
    @patch("desktop.network.scanner.subprocess.run")
    def test_deduplicates_ssids(
        self, mock_run: MagicMock, _sleep: MagicMock
    ) -> None:
        dupe_output = "MyNet:80:WPA2\nMyNet:75:WPA2\n"
        mock_run.side_effect = [
            MagicMock(),
            MagicMock(stdout=dupe_output),
        ]
        networks = scan_wifi_networks()
        assert len(networks) == 1

    @patch("desktop.network.scanner.subprocess.run", side_effect=FileNotFoundError)
    def test_raises_when_nmcli_missing(self, _: MagicMock) -> None:
        with pytest.raises(RuntimeError, match="nmcli not found"):
            scan_wifi_networks()


class TestFindNearshareHotspots:
    """Tests for find_nearshare_hotspots()."""

    def test_filters_by_prefix(self) -> None:
        networks = [
            ScannedNetwork(ssid="NearShare-7f3a", signal=85, security="WPA2"),
            ScannedNetwork(ssid="HomeWifi", signal=72, security="WPA2"),
            ScannedNetwork(ssid="NearShare-ab12", signal=60, security="WPA2"),
        ]
        result = find_nearshare_hotspots(networks)
        assert len(result) == 2
        assert result[0].ssid == "NearShare-7f3a"
        assert result[1].ssid == "NearShare-ab12"

    def test_returns_empty_when_no_match(self) -> None:
        networks = [
            ScannedNetwork(ssid="HomeWifi", signal=72, security="WPA2"),
        ]
        assert find_nearshare_hotspots(networks) == []


class TestConnectToHotspot:
    """Tests for connect_to_hotspot()."""

    @patch("desktop.network.scanner.subprocess.run")
    def test_connect_success(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock()
        assert connect_to_hotspot("NearShare-7f3a", "pass") is True
        args = mock_run.call_args[0][0]
        assert "connect" in args
        assert "NearShare-7f3a" in args

    @patch("desktop.network.scanner.subprocess.run",
           side_effect=subprocess.CalledProcessError(1, "nmcli", stderr="timeout"))
    def test_connect_failure(self, _: MagicMock) -> None:
        assert connect_to_hotspot("NearShare-7f3a", "pass") is False

    @patch("desktop.network.scanner.subprocess.run", side_effect=FileNotFoundError)
    def test_connect_no_nmcli(self, _: MagicMock) -> None:
        assert connect_to_hotspot("NearShare-7f3a", "pass") is False


class TestDisconnectFromHotspot:
    """Tests for disconnect_from_hotspot()."""

    @patch("desktop.network.scanner.subprocess.run")
    def test_calls_down_and_delete(self, mock_run: MagicMock) -> None:
        disconnect_from_hotspot("NearShare-7f3a")
        assert mock_run.call_count == 2
        assert "down" in mock_run.call_args_list[0][0][0]
        assert "delete" in mock_run.call_args_list[1][0][0]
