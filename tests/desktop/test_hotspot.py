"""Tests for desktop.network.hotspot — hotspot create/teardown."""

import subprocess
from unittest.mock import patch, MagicMock, call

import pytest

from desktop.network.hotspot import (
    HOTSPOT_SSID_PREFIX,
    HOTSPOT_GATEWAY_IP,
    HotspotInfo,
    create_hotspot,
    is_hotspot_active,
    teardown_hotspot,
)

class TestCreateHotspot:
    """Tests for create_hotspot()."""

    @patch("desktop.network.hotspot.find_wifi_interface", return_value="wlo1")
    @patch("desktop.network.hotspot.subprocess.run")
    def test_calls_nmcli_correctly(self, mock_run: MagicMock, _: MagicMock) -> None:
        mock_run.return_value = MagicMock()
        info = create_hotspot()

        assert info.interface == "wlo1"
        assert info.ssid.startswith(HOTSPOT_SSID_PREFIX)
        assert len(info.password) == 8
        assert info.gateway_ip == HOTSPOT_GATEWAY_IP

        # verify nmcli was called with the right args
        args = mock_run.call_args[0][0]
        assert "hotspot" in args
        assert "wlo1" in args
        assert info.ssid in args

    @patch("desktop.network.hotspot.find_wifi_interface", return_value="wlo1")
    @patch("desktop.network.hotspot.subprocess.run")
    def test_custom_ssid_and_password(self, mock_run: MagicMock, _: MagicMock) -> None:
        mock_run.return_value = MagicMock()
        info = create_hotspot(ssid_suffix="Bob", password="custom_pass")

        assert info.ssid == f"{HOTSPOT_SSID_PREFIX}Bob"
        assert info.password == "custom_pass"

    @patch("desktop.network.hotspot.find_wifi_interface", return_value=None)
    def test_raises_when_no_wifi_interface(self, _: MagicMock) -> None:
        with pytest.raises(RuntimeError, match="No Wi-Fi interface"):
            create_hotspot()

    @patch("desktop.network.hotspot.find_wifi_interface", return_value="wlo1")
    @patch("desktop.network.hotspot.subprocess.run",
           side_effect=subprocess.CalledProcessError(1, "nmcli", stderr="AP mode not supported"))
    def test_raises_on_nmcli_failure(self, _run: MagicMock, _iface: MagicMock) -> None:
        with pytest.raises(RuntimeError, match="Failed to create hotspot"):
            create_hotspot()

    def test_explicit_interface(self) -> None:
        with patch("desktop.network.hotspot.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock()
            info = create_hotspot(interface="wlan0")
            assert info.interface == "wlan0"


class TestTeardownHotspot:
    """Tests for teardown_hotspot()."""

    @patch("desktop.network.hotspot.subprocess.run")
    def test_calls_down_and_delete(self, mock_run: MagicMock) -> None:
        info = HotspotInfo(
            ssid="NearShare-abcd",
            password="test_pass",
            interface="wlo1",
            gateway_ip=HOTSPOT_GATEWAY_IP,
            connection_name="NearShare-abcd",
        )
        teardown_hotspot(info)

        assert mock_run.call_count == 2
        # first call: bring down
        assert "down" in mock_run.call_args_list[0][0][0]
        # second call: delete
        assert "delete" in mock_run.call_args_list[1][0][0]

    @patch("desktop.network.hotspot.subprocess.run",
           side_effect=subprocess.CalledProcessError(1, "nmcli"))
    def test_teardown_ignores_errors(self, _: MagicMock) -> None:
        info = HotspotInfo(
            ssid="NearShare-abcd",
            password="test_pass",
            interface="wlo1",
            gateway_ip=HOTSPOT_GATEWAY_IP,
            connection_name="NearShare-abcd",
        )
        # should not raise
        teardown_hotspot(info)


class TestIsHotspotActive:
    """Tests for is_hotspot_active()."""

    @patch("desktop.network.hotspot.subprocess.run")
    def test_returns_true_when_active(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="NearShare-abcd\nWired\n")
        assert is_hotspot_active("NearShare-abcd") is True

    @patch("desktop.network.hotspot.subprocess.run")
    def test_returns_false_when_inactive(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="Wired\n")
        assert is_hotspot_active("NearShare-abcd") is False
