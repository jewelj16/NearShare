"""Wi-Fi scanner — scan for NearShare hotspots and auto-connect.

The receiver uses this module to find and connect to a sender's
NearShare hotspot without any manual configuration.
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass

from desktop.network.hotspot import HOTSPOT_SSID_PREFIX

logger = logging.getLogger("nearshare.engine.network")


@dataclass
class ScannedNetwork:
    """A Wi-Fi network found during scanning.

    Attributes:
        ssid:     The network SSID.
        signal:   Signal strength (0-100).
        security: Security type string (e.g. "WPA2").
    """

    ssid: str
    signal: int
    security: str


def scan_wifi_networks() -> list[ScannedNetwork]:
    """Scan for visible Wi-Fi networks using nmcli.

    Triggers a fresh rescan before listing results.

    Returns:
        List of ScannedNetwork objects, sorted by signal strength (strongest first).

    Raises:
        RuntimeError: If nmcli is not available.
    """
    # trigger a fresh scan first
    try:
        subprocess.run(
            ["nmcli", "device", "wifi", "rescan"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        raise RuntimeError("nmcli not found. NetworkManager is required.")

    # short delay for scan results to populate
    time.sleep(1.0)

    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list"],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as exc:
        logger.warning("Wi-Fi scan failed: %s", exc.stderr.strip())
        return []

    networks: list[ScannedNetwork] = []
    seen_ssids: set[str] = set()

    for line in result.stdout.strip().splitlines():
        parts = line.split(":")
        if len(parts) < 3:
            continue
        ssid = parts[0].strip()
        if not ssid or ssid in seen_ssids:
            continue
        seen_ssids.add(ssid)

        try:
            signal = int(parts[1])
        except ValueError:
            signal = 0

        security = parts[2] if len(parts) > 2 else ""
        networks.append(ScannedNetwork(ssid=ssid, signal=signal, security=security))

    networks.sort(key=lambda n: n.signal, reverse=True)
    return networks


def find_nearshare_hotspots(networks: list[ScannedNetwork] | None = None) -> list[ScannedNetwork]:
    """Filter a scan result for NearShare hotspots.

    If no network list is provided, performs a fresh scan.

    Returns:
        NearShare hotspots sorted by signal strength.
    """
    if networks is None:
        networks = scan_wifi_networks()

    return [n for n in networks if n.ssid.startswith(HOTSPOT_SSID_PREFIX)]


def connect_to_hotspot(ssid: str, password: str) -> bool:
    """Connect to a NearShare hotspot using nmcli.

    Args:
        ssid:     The hotspot SSID to connect to.
        password: The WPA2 password.

    Returns:
        True if the connection succeeded.
    """
    logger.info("Connecting to hotspot '%s'...", ssid)
    try:
        subprocess.run(
            ["nmcli", "device", "wifi", "connect", ssid, "password", password],
            capture_output=True, text=True, check=True,
        )
        logger.info("Connected to '%s'", ssid)
        return True
    except subprocess.CalledProcessError as exc:
        logger.warning("Failed to connect to '%s': %s", ssid, exc.stderr.strip())
        return False
    except FileNotFoundError:
        logger.error("nmcli not found")
        return False


def disconnect_from_hotspot(ssid: str) -> None:
    """Disconnect from a NearShare hotspot and delete the saved profile."""
    try:
        subprocess.run(
            ["nmcli", "connection", "down", ssid],
            capture_output=True, text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    # clean up the auto-created connection profile
    try:
        subprocess.run(
            ["nmcli", "connection", "delete", ssid],
            capture_output=True, text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass

    logger.info("Disconnected from '%s'", ssid)


