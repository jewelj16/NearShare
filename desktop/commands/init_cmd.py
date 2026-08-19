"""nearshare init — sender-side command that creates a hotspot and waits.

Flow:
  1. Save current Wi-Fi state
  2. Create a NearShare hotspot
  3. Start TCP listener on the hotspot network
  4. Wait for the receiver to connect
  5. Run the standard sender transfer flow
  6. Tear down the hotspot and restore original Wi-Fi
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import signal
import sys
from pathlib import Path

from desktop.adapters.tcp_transport import TcpTransport
from desktop.network.hotspot import (
    HOTSPOT_GATEWAY_IP,
    HotspotInfo,
    create_hotspot,
    teardown_hotspot,
)
from desktop.network.wifi_state import (
    WifiSnapshot,
    restore_wifi,
    take_wifi_snapshot,
)
from engine.transfer.sender import TransferSender
from engine.types import DEFAULT_PORT, DeviceId

logger = logging.getLogger("nearshare.engine.commands")


async def run_init(
    files: list[Path],
    device_id: DeviceId,
    display_name: str,
    port: int = DEFAULT_PORT,
) -> int:
    """Execute the 'init' command — create hotspot, wait for receiver, send files.

    Args:
        files:        Files to send.
        device_id:    Our device ID.
        display_name: Our display name.
        port:         TCP port to listen on.

    Returns:
        Exit code (0 = success, 1 = failure).
    """
    # save current Wi-Fi so we can restore it later
    wifi_snapshot = take_wifi_snapshot()
    hotspot_info: HotspotInfo | None = None

    def _cleanup() -> None:
        """Ensure hotspot is torn down and Wi-Fi is restored on exit."""
        if hotspot_info is not None:
            print("\nCleaning up hotspot...")
            teardown_hotspot(hotspot_info)
        if wifi_snapshot is not None:
            print("Restoring original Wi-Fi...")
            restore_wifi(wifi_snapshot)

    # register cleanup for Ctrl+C and unexpected exits
    atexit.register(_cleanup)

    try:
        # create hotspot
        print("Creating NearShare hotspot...")
        hotspot_info = create_hotspot()
        print(f"  SSID:     {hotspot_info.ssid}")
        print(f"  Password: {hotspot_info.password}")
        print(f"  Gateway:  {hotspot_info.gateway_ip}")
        print()

        # start TCP listener on the hotspot gateway IP
        transport = TcpTransport(
            listen_host=HOTSPOT_GATEWAY_IP,
            listen_port=port,
        )
        actual_port = await transport.start()
        print(f"Listening on {HOTSPOT_GATEWAY_IP}:{actual_port}")
        print("Waiting for receiver to connect...\n")

        # wait for receiver
        try:
            conn = await asyncio.wait_for(transport.accept(), timeout=120.0)
        except asyncio.TimeoutError:
            print("Timed out waiting for receiver (2 minutes).", file=sys.stderr)
            return 1

        peer_host, peer_port = conn.peer_address
        print(f"Receiver connected from {peer_host}:{peer_port}")

        # run the standard sender flow
        sender = TransferSender(
            local_device_id=device_id,
            local_display_name=display_name,
        )

        print(f"Sending {len(files)} file(s)...")
        result = await sender.run(conn, files)
        await transport.close()

        if result.ok:
            print(f"\n✓ Transfer complete: {result}")
            return 0
        else:
            print(f"\n✗ Transfer failed: {result.error_msg}", file=sys.stderr)
            return 1

    except RuntimeError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    finally:
        # always clean up
        atexit.unregister(_cleanup)
        _cleanup()
