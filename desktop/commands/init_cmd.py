"""nearshare init — sender-side command that creates a hotspot and waits interactively.

Flow:
  1. Prompt for short SSID name.
  2. Save current Wi-Fi state.
  3. Create a NearShare hotspot (with random password).
  4. Start TCP listener on the hotspot network.
  5. Wait for the receiver to connect.
  6. Perform handshake and show receiver name.
  7. Prompt for files to send.
  8. Run the standard sender transfer flow with progress bar.
  9. Tear down the hotspot and restore original Wi-Fi.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
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
from desktop.ui.logo import print_logo
from desktop.ui.progress import ProgressBar
from engine.transfer.sender import TransferSender
from engine.types import DEFAULT_PORT, DeviceId

logger = logging.getLogger("nearshare.engine.commands")


async def run_init(
    device_id: DeviceId,
    display_name: str,
    port: int = DEFAULT_PORT,
    files: list[Path] | None = None,
) -> int:
    """Execute the interactive 'init' command.

    Args:
        device_id:    Our device ID.
        display_name: Our display name.
        port:         TCP port to listen on.

    Returns:
        Exit code (0 = success, 1 = failure).
    """
    print_logo("SEND")

    ssid_suffix = input("Enter a temporary name for your hotspot (max 6 chars): ").strip()
    if not ssid_suffix:
        ssid_suffix = None

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
        print("\nCreating NearShare hotspot...")
        hotspot_info = create_hotspot(ssid_suffix=ssid_suffix)
        print(f"  SSID:     {hotspot_info.ssid}")
        print(f"  Password: {hotspot_info.password}")
        print()

        # start TCP listener on the hotspot gateway IP
        transport = TcpTransport(
            listen_host=HOTSPOT_GATEWAY_IP,
            listen_port=port,
        )
        actual_port = await transport.start()
        print("Waiting for receivers to connect...\n")

        # wait for receiver
        try:
            conn = await asyncio.wait_for(transport.accept(), timeout=300.0)
        except asyncio.TimeoutError:
            print("Timed out waiting for receiver (5 minutes).", file=sys.stderr)
            return 1

        sender = TransferSender(
            local_device_id=device_id,
            local_display_name=display_name,
        )

        # perform handshake to get peer name
        peer_info = await sender.handshake(conn)
        print(f"\nDevice '{peer_info.display_name}' connected!\n")

        # prompt for files if not provided on the command line
        files_to_send: list[Path] = files or []
        if not files_to_send:
            while True:
                file_path = input("Enter a file path to send (or press Enter to finish): ").strip()
                if not file_path:
                    if files_to_send:
                        break
                    else:
                        print("Please enter at least one file.")
                        continue
                
                p = Path(file_path).expanduser().resolve()
                if not p.exists():
                    print(f"Error: File not found: {p}")
                    continue
                if p.is_dir():
                    print(f"Error: Directories not supported: {p}")
                    continue
                
                files_to_send.append(p)
                print(f"Added {p.name}. Total files: {len(files_to_send)}")

        print(f"\nReady to send {len(files_to_send)} file(s).")
        input("Press Enter to start sending...")

        bar = ProgressBar()
        bar.start()

        result = await sender.send_files(conn, files_to_send, on_progress=bar.update)
        bar.finish()

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
