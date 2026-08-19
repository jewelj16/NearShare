"""nearshare receive (auto mode) — scan for hotspots and receive files.

When the receiver runs without --host, it enters auto-discovery mode:
  1. Save current Wi-Fi state
  2. Scan for NearShare-* hotspots
  3. Connect to the strongest one
  4. Connect TCP to the sender at the hotspot gateway (10.42.0.1)
  5. Run the standard receiver transfer flow
  6. Disconnect from hotspot and restore original Wi-Fi
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import sys
from pathlib import Path

from desktop.adapters.tcp_transport import TcpTransport
from desktop.network.hotspot import HOTSPOT_GATEWAY_IP
from desktop.network.scanner import disconnect_from_hotspot, scan_and_connect
from desktop.network.wifi_state import (
    WifiSnapshot,
    restore_wifi,
    take_wifi_snapshot,
)
from engine.transfer.receiver import TransferReceiver
from engine.types import DEFAULT_PORT, DeviceId

logger = logging.getLogger("nearshare.engine.commands")


async def run_receive_auto(
    device_id: DeviceId,
    display_name: str,
    save_dir: Path,
    auto_accept: bool = False,
    port: int = DEFAULT_PORT,
    scan_timeout: float = 30.0,
) -> int:
    """Execute auto-discovery receive — find hotspot, connect, receive files.

    Args:
        device_id:    Our device ID.
        display_name: Our display name.
        save_dir:     Directory to save received files.
        auto_accept:  If True, skip the accept/reject prompt.
        port:         TCP port to connect to on the sender.
        scan_timeout: How long to scan for hotspots before giving up.

    Returns:
        Exit code (0 = success, 1 = failure).
    """
    wifi_snapshot = take_wifi_snapshot()
    connected_ssid: str | None = None

    def _cleanup() -> None:
        if connected_ssid is not None:
            print("\nDisconnecting from hotspot...")
            disconnect_from_hotspot(connected_ssid)
        if wifi_snapshot is not None:
            print("Restoring original Wi-Fi...")
            restore_wifi(wifi_snapshot)

    atexit.register(_cleanup)

    try:
        print("Scanning for NearShare hotspots...")
        connected_ssid = scan_and_connect(timeout_s=scan_timeout)

        if connected_ssid is None:
            print(
                "No NearShare hotspots found. Make sure the sender has run "
                "'nearshare init' first.",
                file=sys.stderr,
            )
            return 1

        print(f"Connected to '{connected_ssid}'")
        print(f"Connecting to sender at {HOTSPOT_GATEWAY_IP}:{port}...")

        # give the network a moment to settle after connecting
        await asyncio.sleep(1.0)

        # connect TCP to the sender (which is always at the gateway IP)
        transport = TcpTransport()
        try:
            conn = await asyncio.wait_for(
                transport.connect(HOTSPOT_GATEWAY_IP, port),
                timeout=10.0,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            print(f"Failed to connect to sender: {exc}", file=sys.stderr)
            return 1

        # run the standard receiver flow
        receiver = TransferReceiver(
            local_device_id=device_id,
            local_display_name=display_name,
            save_dir=save_dir,
            auto_accept=auto_accept,
        )

        result = await receiver.run(conn)

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
        atexit.unregister(_cleanup)
        _cleanup()
