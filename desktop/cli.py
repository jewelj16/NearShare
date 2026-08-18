"""NearShare CLI — command-line interface for sending and receiving files.

Usage:
  nearshare send <file1> [<file2> ...] --host <ip> [--port <port>]
  nearshare receive [--save-dir <dir>] [--port <port>] [--auto-accept]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import uuid
from pathlib import Path

from engine.logging_.setup import setup_engine_logging
from engine.types import DEFAULT_PORT, DeviceId


logger = logging.getLogger("nearshare.cli")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser for the NearShare CLI."""
    parser = argparse.ArgumentParser(
        prog="nearshare",
        description="NearShare — laptop-to-laptop file transfer",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ── send ──────────────────────────────────────────────────────────────
    send_p = sub.add_parser("send", help="Send files to a peer")
    send_p.add_argument(
        "files",
        nargs="+",
        type=Path,
        help="Files to send",
    )
    send_p.add_argument(
        "--host",
        required=True,
        help="IP address of the receiver",
    )
    send_p.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"TCP port of the receiver (default {DEFAULT_PORT})",
    )
    send_p.add_argument(
        "--name",
        default=None,
        help="Display name for this device (default: hostname)",
    )

    # ── receive ───────────────────────────────────────────────────────────
    recv_p = sub.add_parser("receive", help="Wait for incoming transfers")
    recv_p.add_argument(
        "--save-dir",
        type=Path,
        default=Path.home() / "Downloads" / "NearShare",
        help="Directory to save received files (default ~/Downloads/NearShare)",
    )
    recv_p.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"TCP port to listen on (default {DEFAULT_PORT})",
    )
    recv_p.add_argument(
        "--auto-accept",
        action="store_true",
        help="Automatically accept all incoming transfers",
    )
    recv_p.add_argument(
        "--name",
        default=None,
        help="Display name for this device (default: hostname)",
    )

    return parser


def _get_device_id() -> DeviceId:
    """Generate or retrieve a persistent device ID."""
    config_dir = Path.home() / ".config" / "nearshare"
    id_file = config_dir / "device_id"
    if id_file.exists():
        return DeviceId(id_file.read_text().strip())
    config_dir.mkdir(parents=True, exist_ok=True)
    new_id = str(uuid.uuid4())
    id_file.write_text(new_id)
    return DeviceId(new_id)


def _get_display_name(override: str | None) -> str:
    """Return the display name for this device."""
    if override:
        return override
    try:
        import socket as _socket
        return _socket.gethostname()
    except Exception:
        return "NearShare"


async def cmd_send(args: argparse.Namespace) -> int:
    """Execute the 'send' subcommand."""
    from desktop.adapters.tcp_transport import TcpTransport
    from engine.transfer.sender import TransferSender

    device_id = _get_device_id()
    display_name = _get_display_name(args.name)

    # validate files exist
    for f in args.files:
        if not f.exists():
            print(f"Error: File not found: {f}", file=sys.stderr)
            return 1
        if f.is_dir():
            print(f"Error: Directories not supported: {f}", file=sys.stderr)
            return 1

    print(f"Connecting to {args.host}:{args.port}...")
    transport = TcpTransport()
    try:
        conn = await transport.connect(args.host, args.port)
    except OSError as exc:
        print(f"Error: Could not connect: {exc}", file=sys.stderr)
        return 1

    sender = TransferSender(
        local_device_id=device_id,
        local_display_name=display_name,
    )

    print(f"Sending {len(args.files)} file(s)...")
    result = await sender.run(conn, args.files)

    if result.ok:
        print(f"\n✓ Transfer complete: {result}")
        return 0
    else:
        print(f"\n✗ Transfer failed: {result.error_msg}", file=sys.stderr)
        return 1


async def cmd_receive(args: argparse.Namespace) -> int:
    """Execute the 'receive' subcommand."""
    from desktop.adapters.tcp_transport import TcpTransport
    from engine.transfer.receiver import TransferReceiver

    device_id = _get_device_id()
    display_name = _get_display_name(args.name)
    save_dir = args.save_dir.resolve()
    save_dir.mkdir(parents=True, exist_ok=True)

    transport = TcpTransport(listen_port=args.port)
    try:
        port = await transport.start()
    except OSError as exc:
        print(f"Error: Could not start listener: {exc}", file=sys.stderr)
        return 1

    print(f"Listening on port {port}")
    print(f"Saving files to {save_dir}")
    print("Waiting for incoming transfer...\n")

    try:
        conn = await transport.accept()
    except asyncio.CancelledError:
        print("\nCancelled.")
        return 1

    receiver = TransferReceiver(
        local_device_id=device_id,
        local_display_name=display_name,
        save_dir=save_dir,
        auto_accept=args.auto_accept,
    )

    result = await receiver.run(conn)
    await transport.close()

    if result.ok:
        print(f"\n✓ Transfer complete: {result}")
        return 0
    else:
        print(f"\n✗ Transfer failed: {result.error_msg}", file=sys.stderr)
        return 1


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    setup_engine_logging(level=level)

    if args.command == "send":
        exit_code = asyncio.run(cmd_send(args))
    elif args.command == "receive":
        exit_code = asyncio.run(cmd_receive(args))
    else:
        parser.print_help()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
