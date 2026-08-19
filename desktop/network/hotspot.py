"""Wi-Fi hotspot management — create and tear down ephemeral access points.

Uses nmcli to spin up a temporary hotspot on the local wireless interface.
NetworkManager handles DHCP (via dnsmasq) automatically when ipv4.method
is set to 'shared'.

The sender creates the hotspot; the receiver connects to it.
"""

from __future__ import annotations

import logging
import os
import secrets
import string
import subprocess
from dataclasses import dataclass

from desktop.network.wifi_state import find_wifi_interface

logger = logging.getLogger("nearshare.engine.network")

# all NearShare hotspots use this prefix so the receiver can find them
HOTSPOT_SSID_PREFIX = "NearShare-"

# fixed well-known password for v1 (security rationale: hotspot is ephemeral,
# all actual data goes over TLS, and receiver still gets an accept/reject prompt)
HOTSPOT_PASSWORD = "NearShare2026"

# the gateway IP that NetworkManager assigns to the hotspot creator
HOTSPOT_GATEWAY_IP = "10.42.0.1"


@dataclass
class HotspotInfo:
    """Details of a running NearShare hotspot.

    Attributes:
        ssid:           The SSID of the hotspot (e.g. "NearShare-7f3a").
        password:       The WPA2 password.
        interface:      The wireless interface being used.
        gateway_ip:     The IP address of this device on the hotspot network.
        connection_name: The nmcli connection profile name.
    """

    ssid: str
    password: str
    interface: str
    gateway_ip: str
    connection_name: str


def generate_hotspot_password() -> str:
    """Generate a random 8-character alphanumeric password."""
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(8))


def create_hotspot(
    interface: str | None = None,
    ssid_suffix: str | None = None,
    password: str | None = None,
) -> HotspotInfo:
    """Create and activate a Wi-Fi hotspot using nmcli.

    This requires root privileges (sudo). NetworkManager automatically
    starts a DHCP server on the hotspot network.

    Args:
        interface: Wireless interface to use. Auto-detected if None.
        ssid_suffix: Optional short suffix for the SSID (e.g. 'Bob').
        password: WPA2 password. Auto-generated if None.

    Returns:
        HotspotInfo with the details of the running hotspot.

    Raises:
        RuntimeError: If hotspot creation fails or no Wi-Fi interface found.
    """
    if interface is None:
        interface = find_wifi_interface()
    if interface is None:
        raise RuntimeError(
            "No Wi-Fi interface found. Cannot create hotspot. "
            "Check that your wireless adapter is enabled."
        )

    if ssid_suffix:
        ssid = f"{HOTSPOT_SSID_PREFIX}{ssid_suffix[:6]}"
    else:
        ssid = f"{HOTSPOT_SSID_PREFIX}{secrets.token_hex(2)}"

    conn_name = ssid  # use SSID as the connection profile name
    if password is None:
        password = generate_hotspot_password()

    try:
        subprocess.run(
            [
                "nmcli", "device", "wifi", "hotspot",
                "ifname", interface,
                "ssid", ssid,
                "password", password,
                "con-name", conn_name,
            ],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"Failed to create hotspot: {exc.stderr.strip()}"
        ) from exc
    except FileNotFoundError:
        raise RuntimeError(
            "nmcli not found. NetworkManager is required for hotspot mode."
        )

    logger.info("Hotspot '%s' active on %s", ssid, interface)

    return HotspotInfo(
        ssid=ssid,
        password=password,
        interface=interface,
        gateway_ip=HOTSPOT_GATEWAY_IP,
        connection_name=conn_name,
    )


def teardown_hotspot(info: HotspotInfo) -> None:
    """Bring down and delete a NearShare hotspot connection profile.

    Safe to call even if the hotspot has already been torn down.
    """
    # bring down the connection
    try:
        subprocess.run(
            ["nmcli", "connection", "down", info.connection_name],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        pass  # already down

    # delete the profile so it doesn't clutter saved connections
    try:
        subprocess.run(
            ["nmcli", "connection", "delete", info.connection_name],
            capture_output=True, text=True, check=True,
        )
    except subprocess.CalledProcessError:
        pass  # already gone

    logger.info("Hotspot '%s' torn down", info.ssid)


def is_hotspot_active(conn_name: str) -> bool:
    """Check whether a NearShare hotspot connection is currently active."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"],
            capture_output=True, text=True, check=True,
        )
        return conn_name in result.stdout.strip().splitlines()
    except (FileNotFoundError, subprocess.CalledProcessError):
        return False
