# ADR-001: Transport Choice

**Date**: 2026-08-12
**Status**: Accepted

## Context
NearShare needs a fast, reliable, and secure mechanism for discovering peers and transferring files between an Ubuntu Linux desktop and an Android device over a local network, without relying on external cloud servers. The transport mechanism is a core component that affects both the speed and the reliability of the system.

## Decision
We will use **same-network TLS-wrapped TCP** for file transfer and **UDP multicast/broadcast** for discovery.

## Rationale
- **UDP Discovery**: Standard mechanism for finding peers on a local network (e.g., mDNS/Bonjour). It is lightweight, supports multicasting, and allows for heartbeat-based failure detection without maintaining persistent connections to all devices. A fixed port (default 47321) with a 15s liveness timeout is sufficient.
- **TLS-wrapped TCP**: TCP provides out-of-the-box reliability (handling packet loss, ordering, congestion control). Wrapping it in TLS with self-signed, per-device certificates (Trust On First Use) provides a baseline layer of encryption against casual eavesdropping on open Wi-Fi networks without the complexity of a full PKI.

## Alternatives Considered & Rejected
- **Wi-Fi Direct**: Rejected for the MVP phase because while it's well-supported on Android, it is historically difficult to configure consistently on all Linux desktop environments without root/NetworkManager intervention. Same-network IP routing is more universally reliable for a cross-platform MVP. We leave the `Transport` interface abstracted enough that a true Wi-Fi Direct adapter could be added later.
- **WebRTC / QUIC**: Rejected to keep the initial engine simple and dependency-free. Implementing a custom protocol over TCP allows us to strictly control chunking, windowing, and resumption logic for educational/portfolio demonstration purposes.
- **HTTP / REST**: Rejected because a custom binary framing protocol avoids HTTP header overhead per chunk and makes bidirectional real-time progress/ACK reporting (via a sliding window) simpler to model as a state machine.
