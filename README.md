# NearShare

**NearShare** is a peer-to-peer file transfer system between an Ubuntu Linux desktop app and an Android app, transferring files directly over the local Wi-Fi network with no cloud server.

## Architecture
- **Desktop:** Python 3, PyQt6, asyncio
- **Mobile:** Flutter (Dart), Kotlin platform channels
- **Transport:** TLS-wrapped TCP
- **Discovery:** UDP multicast/broadcast

## Status
Under active development (Phase 1).
