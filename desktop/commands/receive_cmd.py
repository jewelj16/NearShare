"""nearshare receive (auto mode) — scan for hotspots and receive files interactively.

Flow:
  1. Print Logo (RECEIVE mode).
  2. Prompt for username.
  3. Scan and display a numbered list of NearShare-* hotspots.
  4. Prompt for hotspot selection and password.
  5. Connect to hotspot and start TCP connection.
  6. Run receiver transfer flow with progress bar.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import sys
from pathlib import Path

from desktop.adapters.tcp_transport import TcpTransport
from desktop.network.hotspot import HOTSPOT_GATEWAY_IP, ANDROID_HOTSPOT_GATEWAY_IP
from desktop.network.scanner import (
    connect_to_hotspot,
    disconnect_from_hotspot,
    find_nearshare_hotspots,
)
from desktop.network.wifi_state import (
    WifiSnapshot,
    restore_wifi,
    take_wifi_snapshot,
)
from desktop.ui.logo import print_logo
from desktop.ui.progress import ProgressBar
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
    """Execute auto-discovery receive interactively."""
    print_logo("RECEIVE")

    user_name = input(f"Enter your display name (default: {display_name}): ").strip()
    if user_name:
        display_name = user_name

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
        print("\nScanning for senders...")
        hotspots = find_nearshare_hotspots()
        
        if not hotspots:
            print(
                "No senders found. Make sure the sender has run 'nearshare init' first.",
                file=sys.stderr,
            )
            return 1

        print("\nAvailable Senders:")
        for i, hs in enumerate(hotspots, 1):
            if hs.is_android:
                # Android SSIDs look like AndroidShare_2651
                sender_name = hs.ssid.replace("AndroidShare_", "Mobile_")
                sender_type = "Mobile"
            else:
                sender_name = hs.ssid.replace("NearShare-", "")
                sender_type = "PC"
            print(f"  {i}. {sender_name} ({sender_type})")
        
        print()
        while True:
            choice = input("Select a sender (number): ").strip()
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(hotspots):
                    selected_hotspot = hotspots[idx]
                    break
            except ValueError:
                pass
            print("Invalid selection.")

        password = input("Enter the password provided by the sender: ").strip()

        print(f"\nConnecting to '{selected_hotspot.ssid}'...")
        if not connect_to_hotspot(selected_hotspot.ssid, password):
            print("Failed to connect to the hotspot. Incorrect password?", file=sys.stderr)
            return 1
            
        connected_ssid = selected_hotspot.ssid

        # give the network a moment to settle after connecting
        await asyncio.sleep(1.0)

        target_ip = ANDROID_HOTSPOT_GATEWAY_IP if selected_hotspot.is_android else HOTSPOT_GATEWAY_IP
        print(f"Connecting to sender at {target_ip}:{port}...")

        transport = TcpTransport()
        try:
            conn = await asyncio.wait_for(
                transport.connect(target_ip, port),
                timeout=10.0,
            )
        except (OSError, asyncio.TimeoutError) as exc:
            print(f"Failed to connect to sender: {exc}", file=sys.stderr)
            return 1

        receiver = TransferReceiver(
            local_device_id=device_id,
            local_display_name=display_name,
            save_dir=save_dir,
            auto_accept=auto_accept,
        )

        bar = ProgressBar()
        
        # intercept the print statements inside TransferReceiver by creating an on_progress callback
        # the receiver will still print to stdout.
        # Ideally, we should suppress the logs/prints in receiver if on_progress is provided, 
        # but for now we just pass it.
        def _on_progress(bytes_done: int, total_bytes: int):
            if bar.start_time is None:
                print()  # Add a newline before the bar starts
                bar.start()
            bar.update(bytes_done, total_bytes)

        result = await receiver.run(conn, on_progress=_on_progress)
        
        if bar.start_time is not None:
            bar.finish()

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
