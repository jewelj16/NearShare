"""NearShare CLI — command-line interface for sending and receiving files.

Usage:
  nearshare init <file1> [<file2> ...]           # create hotspot + send (auto)
  nearshare send <file1> [<file2> ...] --host <ip> [--port <port>]  # direct send
  nearshare receive [--save-dir <dir>] [--auto-accept]              # auto-discover + receive
  nearshare receive --listen [--port <port>]                        # manual listen mode
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


def get_real_home() -> Path:
    """Return the real user's home directory, even if run with sudo."""
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        import pwd
        try:
            return Path(pwd.getpwnam(sudo_user).pw_dir)
        except KeyError:
            pass
    return Path.home()


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

    # ── init (auto-discovery sender) ─────────────────────────────────────
    init_p = sub.add_parser(
        "init",
        help="Create a hotspot and send files (auto-discovery mode)",
    )
    init_p.add_argument(
        "files",
        nargs="*",
        type=Path,
        help="Files to send",
    )
    init_p.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"TCP port to listen on (default {DEFAULT_PORT})",
    )
    init_p.add_argument(
        "--name",
        default=None,
        help="Display name for this device (default: hostname)",
    )

    # ── send (direct mode) ───────────────────────────────────────────────
    send_p = sub.add_parser("send", help="Send files to a peer (direct mode)")
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

    # ── receive ──────────────────────────────────────────────────────────
    recv_p = sub.add_parser("receive", help="Receive files from a peer")
    recv_p.add_argument(
        "--save-dir",
        type=Path,
        default=get_real_home() / "Downloads" / "NearShare",
        help="Directory to save received files (default ~/Downloads/NearShare)",
    )
    recv_p.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"TCP port to listen on / connect to (default {DEFAULT_PORT})",
    )
    recv_p.add_argument(
        "--auto-accept",
        action="store_true",
        help="Automatically accept all incoming transfers",
    )
    recv_p.add_argument(
        "--listen",
        action="store_true",
        help="Listen mode: wait for direct connections instead of scanning for hotspots",
    )
    recv_p.add_argument(
        "--name",
        default=None,
        help="Display name for this device (default: hostname)",
    )
    recv_p.add_argument(
        "--scan-timeout",
        type=float,
        default=30.0,
        help="Seconds to scan for NearShare hotspots (default 30)",
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


def _validate_files(files: list[Path]) -> bool:
    """Check all files exist and are not directories. Prints errors."""
    ok = True
    for f in files:
        if not f.exists():
            print(f"Error: File not found: {f}", file=sys.stderr)
            ok = False
        elif f.is_dir():
            print(f"Error: Directories not supported: {f}", file=sys.stderr)
            ok = False
    return ok


async def cmd_init(args: argparse.Namespace) -> int:
    """Execute the 'init' subcommand (hotspot + send)."""
    if args.files and not _validate_files(args.files):
        return 1

    from desktop.commands.init_cmd import run_init
    return await run_init(
        files=args.files,
        device_id=_get_device_id(),
        display_name=_get_display_name(args.name),
        port=args.port,
    )


async def cmd_send(args: argparse.Namespace) -> int:
    """Execute the 'send' subcommand (direct mode)."""
    from desktop.adapters.tcp_transport import TcpTransport
    from engine.transfer.sender import TransferSender

    if not _validate_files(args.files):
        return 1

    device_id = _get_device_id()
    display_name = _get_display_name(args.name)

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

    async def _factory() -> object:
        return await transport.connect(args.host, args.port)

    print(f"Sending {len(args.files)} file(s)...")
    result = await sender.run(conn, args.files, conn_factory=_factory)

    if result.ok:
        print(f"\n✓ Transfer complete: {result}")
        return 0
    else:
        print(f"\n✗ Transfer failed: {result.error_msg}", file=sys.stderr)
        return 1


async def cmd_receive(args: argparse.Namespace) -> int:
    """Execute the 'receive' subcommand.

    Two modes:
      --listen: wait for direct incoming connections (original behaviour)
      default:  scan for NearShare hotspots and auto-connect
    """
    device_id = _get_device_id()
    display_name = _get_display_name(args.name)
    save_dir = args.save_dir.resolve()
    save_dir.mkdir(parents=True, exist_ok=True)

    if args.listen:
        return await _receive_listen(args, device_id, display_name, save_dir)
    else:
        return await _receive_auto(args, device_id, display_name, save_dir)


async def _receive_listen(
    args: argparse.Namespace,
    device_id: DeviceId,
    display_name: str,
    save_dir: Path,
) -> int:
    """Listen mode: wait for direct TCP connections."""
    from desktop.adapters.tcp_transport import TcpTransport
    from engine.transfer.receiver import TransferReceiver

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

    async def _acceptor() -> object:
        return await transport.accept()

    result = await receiver.run(conn, conn_acceptor=_acceptor)
    await transport.close()

    if result.ok:
        print(f"\n✓ Transfer complete: {result}")
        return 0
    else:
        print(f"\n✗ Transfer failed: {result.error_msg}", file=sys.stderr)
        return 1


async def _receive_auto(
    args: argparse.Namespace,
    device_id: DeviceId,
    display_name: str,
    save_dir: Path,
) -> int:
    """Auto mode: scan for NearShare hotspots and connect."""
    from desktop.commands.receive_cmd import run_receive_auto

    return await run_receive_auto(
        device_id=device_id,
        display_name=display_name,
        save_dir=save_dir,
        auto_accept=args.auto_accept,
        port=args.port,
        scan_timeout=args.scan_timeout,
    )


def main() -> None:
    """CLI entry point."""
    parser = build_parser()
    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    setup_engine_logging(level=level)

    if args.command == "init":
        exit_code = asyncio.run(cmd_init(args))
    elif args.command == "send":
        exit_code = asyncio.run(cmd_send(args))
    elif args.command == "receive":
        exit_code = asyncio.run(cmd_receive(args))
    else:
        parser.print_help()
        exit_code = 1

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
