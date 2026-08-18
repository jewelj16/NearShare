"""Wi-Fi state management — save and restore the current Wi-Fi connection.

Uses nmcli to detect the active wireless connection so we can reconnect
to it after tearing down a NearShare hotspot or disconnecting from one.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

logger = logging.getLogger("nearshare.engine.network")


@dataclass
class WifiSnapshot:
    """Snapshot of the active Wi-Fi connection before NearShare touched it.

    Attributes:
        connection_name: The nmcli connection profile name (e.g. "MyHomeWifi").
        interface:       The wireless interface name (e.g. "wlo1").
        ssid:            The SSID we were connected to.
    """

    connection_name: str
    interface: str
    ssid: str


def find_wifi_interface() -> str | None:
    """Return the name of the first available Wi-Fi interface, or None."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE", "device"],
            capture_output=True, text=True, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    for line in result.stdout.strip().splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[1] == "wifi":
            return parts[0]
    return None


def take_wifi_snapshot() -> WifiSnapshot | None:
    """Capture the current active Wi-Fi connection, if any.

    Returns None if there is no active Wi-Fi connection or if nmcli
    is not available.
    """
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME,DEVICE,TYPE", "connection", "show", "--active"],
            capture_output=True, text=True, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    for line in result.stdout.strip().splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[2] == "802-11-wireless":
            conn_name = parts[0]
            iface = parts[1]
            # get the SSID
            ssid = _get_ssid_for_connection(conn_name)
            return WifiSnapshot(
                connection_name=conn_name,
                interface=iface,
                ssid=ssid or conn_name,
            )
    return None


def restore_wifi(snapshot: WifiSnapshot) -> bool:
    """Reconnect to the Wi-Fi network captured in a snapshot.

    Returns True if the reconnection command succeeded.
    """
    logger.info("Restoring Wi-Fi connection: %s", snapshot.connection_name)
    try:
        subprocess.run(
            ["nmcli", "connection", "up", snapshot.connection_name],
            capture_output=True, text=True, check=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        logger.warning("Failed to restore Wi-Fi: %s", exc.stderr.strip())
        return False


def _get_ssid_for_connection(conn_name: str) -> str | None:
    """Look up the SSID for a named connection profile."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "802-11-wireless.ssid",
             "connection", "show", conn_name],
            capture_output=True, text=True, check=True,
        )
        for line in result.stdout.strip().splitlines():
            if ":" in line:
                return line.split(":", 1)[1]
    except subprocess.CalledProcessError:
        pass
    return None
