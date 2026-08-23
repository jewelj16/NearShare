"""Core domain types for the NearShare engine.

These are pure data types with no I/O or framework dependencies.
They are shared across all engine submodules.
"""

from __future__ import annotations

import enum
import math
import uuid
from dataclasses import dataclass, field
from typing import NewType

# Constants

DEFAULT_CHUNK_SIZE: int = 65_536  # 64 KB — Android's platform-channel transport caps near 1MB, keep well under it
DEFAULT_PORT: int = 47_321
DEFAULT_SEND_WINDOW: int = 128
PROTOCOL_VERSION: int = 1
DISCOVERY_TIMEOUT_S: float = 15.0
RESUME_TIMEOUT_S: float = 30.0
MAX_RETRY_ATTEMPTS: int = 6

# Simple value types

DeviceId = NewType("DeviceId", str)


def new_device_id() -> DeviceId:
    """Generate a new random DeviceId (UUID-4)."""
    return DeviceId(str(uuid.uuid4()))


# Enums

class TransferState(enum.Enum):
    """States of the transfer state machine.

    See docs/architecture/protocol.md for the full state transition diagram.
    """

    IDLE = "idle"
    CONNECTING = "connecting"
    HANDSHAKING = "handshaking"
    METADATA_SENT = "metadata_sent"
    AWAITING_ACCEPT = "awaiting_accept"
    TRANSFERRING = "transferring"
    INTERRUPTED = "interrupted"
    COMPLETE = "complete"
    FAILED = "failed"


class TransferDirection(enum.Enum):
    """Whether this device is sending or receiving in a transfer."""

    SEND = "send"
    RECEIVE = "receive"


# Data classes

@dataclass
class PeerInfo:
    """Information about a discovered peer on the network.

    Attributes:
        device_id:    Unique identifier for the peer device.
        display_name: Human-readable name shown in the UI.
        ip:           IPv4/IPv6 address of the peer.
        port:         TCP port the peer is listening on.
        proto_version: Protocol version the peer supports.
        last_seen:    Unix timestamp of the last heartbeat received.
    """

    device_id: DeviceId
    display_name: str
    ip: str
    port: int
    proto_version: int = PROTOCOL_VERSION
    last_seen: float = 0.0


@dataclass(frozen=True)
class FileMetadata:
    """Metadata describing a file to be transferred.

    ``chunk_count`` is auto-computed from ``size`` and ``chunk_size`` when
    left at the default value of 0.

    Attributes:
        name:       File name (basename, no path separators).
        size:       Total file size in bytes.
        mime_type:  Best-guess MIME type, or None if unknown.
        sha256:     Hex-encoded SHA-256 of the whole file, or None if not
                    yet computed.
        chunk_size: Negotiated chunk size in bytes (default 64 KB).
        chunk_count: Number of chunks (auto-computed if omitted).
    """

    name: str
    size: int
    mime_type: str | None = None
    sha256: str | None = None
    chunk_size: int = DEFAULT_CHUNK_SIZE
    chunk_count: int = 0

    def __post_init__(self) -> None:
        if self.chunk_count == 0 and self.size > 0:
            # Auto-compute; use object.__setattr__ because the class is frozen.
            computed = math.ceil(self.size / self.chunk_size)
            object.__setattr__(self, "chunk_count", computed)


@dataclass(frozen=True)
class Chunk:
    """A single chunk of file data in transit.

    Attributes:
        transfer_id: Session this chunk belongs to.
        file_index:  Index of the file within the transfer's file list.
        seq:         Zero-based sequence number within that file.
        data:        Raw bytes of this chunk.
        sha256:      Hex-encoded SHA-256 of ``data``.
    """

    transfer_id: str
    file_index: int
    seq: int
    data: bytes
    sha256: str
