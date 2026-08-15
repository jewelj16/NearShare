"""Receiver-side ACK bitmap tracking and payload build/parse.

Tracks which chunks have been received per file and produces ACK
message payloads for sending back to the sender.  See protocol.md §3.7.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from engine.protocol.wire import encode_string, decode_string, WireError
from engine.types import FileMetadata


class AckError(Exception):
    """Raised when ACK payload parsing fails."""


@dataclass
class AckBitmap:
    """Per-file bitmap of acknowledged chunk sequence numbers.

    Attributes:
        file_index:  Zero-based index of the file in the transfer.
        chunk_count: Total number of expected chunks for this file.
        received:    Set of sequence numbers received so far.
    """

    file_index: int
    chunk_count: int
    received: set[int] = field(default_factory=set)

    def mark(self, seq: int) -> None:
        """Mark a sequence number as received."""
        if seq < 0 or seq >= self.chunk_count:
            raise ValueError(
                f"seq {seq} out of range [0, {self.chunk_count})"
            )
        self.received.add(seq)

    @property
    def is_complete(self) -> bool:
        return len(self.received) >= self.chunk_count

    @property
    def missing(self) -> set[int]:
        return set(range(self.chunk_count)) - self.received

    @property
    def progress(self) -> float:
        if self.chunk_count == 0:
            return 1.0
        return len(self.received) / self.chunk_count


class AckTracker:
    """Tracks ACK bitmaps for all files in a transfer.

    Attributes:
        transfer_id: The session identifier.
        bitmaps:     Per-file AckBitmap instances.
    """

    def __init__(self, transfer_id: str, files: list[FileMetadata]) -> None:
        self.transfer_id = transfer_id
        self.bitmaps: list[AckBitmap] = [
            AckBitmap(file_index=i, chunk_count=f.chunk_count)
            for i, f in enumerate(files)
        ]

    def ack(self, file_index: int, seq: int) -> None:
        """Mark a chunk as received."""
        if file_index < 0 or file_index >= len(self.bitmaps):
            raise IndexError(f"file_index {file_index} out of range")
        self.bitmaps[file_index].mark(seq)

    def is_file_complete(self, file_index: int) -> bool:
        if file_index < 0 or file_index >= len(self.bitmaps):
            raise IndexError(f"file_index {file_index} out of range")
        return self.bitmaps[file_index].is_complete

    def is_transfer_complete(self) -> bool:
        return all(b.is_complete for b in self.bitmaps)

    @property
    def overall_progress(self) -> float:
        if not self.bitmaps:
            return 1.0
        total_chunks = sum(b.chunk_count for b in self.bitmaps)
        if total_chunks == 0:
            return 1.0
        received_chunks = sum(len(b.received) for b in self.bitmaps)
        return received_chunks / total_chunks


# wire format (protocol.md §3.7)

def build_ack_payload(
    transfer_id: str,
    file_index: int,
    acked_seqs: set[int],
) -> bytes:
    """Build an ACK message payload.

    Layout: [string transfer_id][uint16 file_index]
            [uint32 acked_count][uint32 × N acked_seqs (sorted)]
    """
    sorted_seqs = sorted(acked_seqs)
    parts = [
        encode_string(transfer_id),
        struct.pack("!H", file_index),
        struct.pack("!I", len(sorted_seqs)),
    ]
    for s in sorted_seqs:
        parts.append(struct.pack("!I", s))
    return b"".join(parts)


def parse_ack_payload(payload: bytes) -> tuple[str, int, set[int]]:
    """Parse an ACK payload into (transfer_id, file_index, acked_seqs).

    Raises:
        AckError: If the payload is malformed.
    """
    try:
        offset = 0
        transfer_id, offset = decode_string(payload, offset)

        if offset + 2 > len(payload):
            raise AckError("Truncated file_index in ACK")
        (file_index,) = struct.unpack_from("!H", payload, offset)
        offset += 2

        if offset + 4 > len(payload):
            raise AckError("Truncated acked_count in ACK")
        (acked_count,) = struct.unpack_from("!I", payload, offset)
        offset += 4

        acked_seqs: set[int] = set()
        for _ in range(acked_count):
            if offset + 4 > len(payload):
                raise AckError("Truncated acked seq in ACK")
            (s,) = struct.unpack_from("!I", payload, offset)
            offset += 4
            acked_seqs.add(s)

        return transfer_id, file_index, acked_seqs

    except WireError as e:
        raise AckError(str(e)) from e
