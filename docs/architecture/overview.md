# NearShare Engine Architecture Overview

> **Status**: Living document — updated as the engine evolves.
> **Last updated**: v1 Orchestrator phase

## Module Map

```
engine/
├── types.py              Core domain types (DeviceId, PeerInfo, FileMetadata, Chunk, enums)
├── protocol/
│   ├── codec.py          Length-prefixed frame encode/decode, MessageType enum
│   ├── wire.py           Shared wire helpers (variable-length string encode/decode)
│   ├── handshake.py      HELLO exchange and version negotiation
│   ├── metadata.py       METADATA/ACCEPT/REJECT build/parse + async flow helpers
│   ├── chunk_msg.py      CHUNK (0x06) payload build/parse
│   ├── ack_msg.py        ACK (0x07) payload build/parse (protocol layer)
│   ├── transfer_start.py TRANSFER_START (0x05) payload build/parse
│   ├── complete.py       TRANSFER_COMPLETE (0x09) payload + whole-file hash
│   └── cancel.py         CANCEL (0x08) payload + CancelHandler
├── transfer/
│   ├── session.py        TransferSession — per-transfer state and ack tracking
│   ├── chunker.py        split() generator + ChunkAssembler for reassembly
│   ├── sender.py         TransferSender orchestrator (full send flow)
│   ├── receiver.py       TransferReceiver orchestrator (full receive flow)
│   ├── result.py         TransferResult — frozen outcome dataclass
│   ├── progress.py       ProgressTracker + ProgressSnapshot + CLI progress bar
│   ├── ack.py            AckBitmap / AckTracker + ACK wire helpers
│   ├── window.py         Sliding-window chunk sender (send-side back-pressure)
│   ├── retransmit.py     Missing-chunk retransmission controller
│   ├── resume.py         ResumeToken + ResumeManager
│   ├── state_machine.py  TransferStateMachine — enforces legal transitions
│   └── builder.py        TransferSessionBuilder (fluent API)
├── integrity/
│   └── hasher.py         Per-chunk and whole-file SHA-256 utilities
├── transport/
│   ├── interfaces.py     Connection / Transport Protocol classes (PEP 544)
│   └── fake.py           In-memory FakeTransport with fault injection
├── discovery/
│   ├── interfaces.py     DiscoveryService Protocol class
│   └── fake.py           In-memory FakeDiscovery for testing
├── storage/
│   ├── interfaces.py     FileStore Protocol class
│   ├── file_store.py     ConflictRenamer — rename-on-conflict for duplicate files
│   └── file_io.py        File stat, SHA-256, MIME guess, disk write bridge
└── logging_/
    └── setup.py          Structured logging with session_id correlation

desktop/
├── main.py               Entry point → delegates to CLI
├── cli.py                Argparse CLI: send/receive subcommands
└── adapters/
    ├── tcp_transport.py   TLS-wrapped TCP transport (real sockets)
    └── udp_discovery.py   UDP multicast discovery (real sockets)
```

## Layering

The engine follows a strict dependency order:

```
types.py (no internal deps)
  ↓
protocol/codec.py, wire.py (depends on: types)
  ↓
protocol/handshake.py, metadata.py, chunk_msg.py, ack_msg.py,
  transfer_start.py, complete.py, cancel.py (depends on: codec, wire, types)
  ↓
transfer/session.py, chunker.py, ack.py, window.py, state_machine.py
  (depends on: types, protocol)
  ↓
transfer/progress.py, result.py, retransmit.py, resume.py, builder.py
  (depends on: types, transfer)
  ↓
storage/file_io.py, file_store.py (depends on: types, storage/interfaces)
  ↓
integrity/hasher.py (no internal deps)
  ↓
transfer/sender.py, receiver.py (depends on: all of the above)
  ↓
desktop/cli.py (depends on: transfer/sender, transfer/receiver, adapters)
```

No circular dependencies exist. All interface modules use PEP 544
structural `Protocol` classes — concrete implementations do not need
to explicitly inherit from them.

## Orchestrator Design

The v1 orchestrator introduces `TransferSender` and `TransferReceiver`,
which are the top-level async entry points for file transfers.

### TransferSender (`engine/transfer/sender.py`)

Full async flow:
1. Read files into memory (v1 simplification)
2. HELLO handshake
3. Send METADATA, wait for ACCEPT/REJECT
4. Send TRANSFER_START
5. Per-file chunk loop with sliding window + concurrent ACK receiver
6. Send TRANSFER_COMPLETE

Design note: each file gets its own `SendWindow` because chunk sequence
numbers restart at 0 per file (per protocol §3.6).

### TransferReceiver (`engine/transfer/receiver.py`)

Full async flow:
1. HELLO handshake
2. Receive METADATA, accept or reject
3. Wait for TRANSFER_START
4. Chunk loop: receive, verify hash, write to ChunkAssembler, send ACK
5. On TRANSFER_COMPLETE: write files to disk via ConflictRenamer

### TransferResult (`engine/transfer/result.py`)

Frozen dataclass returned by both sender and receiver. Three factory
methods: `success()`, `failure()`, `cancelled()`. Derived properties:
`ok`, `total_bytes`, `throughput_mbps`.

### ProgressTracker (`engine/transfer/progress.py`)

Tracks bytes transferred and emits `ProgressSnapshot` callbacks at a
configurable interval (default 0.5s). Snapshots include fraction,
throughput (Mbps and MB/s), ETA, and a renderable text progress bar.

## Key Design Decisions

### Transport over TCP (ADR-001)
We chose TCP + TLS 1.3 over UDP for the transfer channel because:
- File transfers need reliable, ordered delivery
- TLS provides transport security with minimal overhead
- UDP would require us to build our own reliability layer

Discovery still uses UDP broadcast/multicast for zero-config peer finding.

### Length-Prefixed Framing
Every message uses a 5-byte header: `[4-byte payload length][1-byte msg type]`.
This makes parsing deterministic and stream-friendly — read 5 bytes,
learn the payload length, read exactly that many more bytes.

### Seek-Based Reassembly
`ChunkAssembler` pre-allocates a buffer and writes each chunk at its
computed byte offset (`seq * chunk_size`). This allows out-of-order
chunk arrival without sorting or buffering.

### Per-Chunk SHA-256
Every chunk carries its own SHA-256 hash so the receiver can verify
each piece independently. The whole-file SHA-256 is checked at the end
before `TRANSFER_COMPLETE` is sent.

### Per-File Sliding Window
The sender uses a per-file `SendWindow` (default capacity 32) to limit
in-flight chunks. Two concurrent asyncio tasks run per file: one sends
chunks through the window, the other reads ACKs and frees slots. This
avoids sequence number collisions across files.

### Fake Implementations for Testing
`FakeTransport` and `FakeDiscovery` allow full protocol testing without
any network I/O. `FaultConfig` supports configurable drop/delay/duplicate
rates with deterministic seeding for reproducible tests.

## CLI Usage

```bash
# Send files
python -m desktop.main send file1.txt file2.bin --host 192.168.1.5

# Receive files (auto-accept for scripted use)
python -m desktop.main receive --auto-accept --save-dir ~/received
```

## Test Coverage

All tests live under `tests/engine/` and `tests/desktop/`. The test
suite runs via `pytest` and uses FakeTransport for deterministic testing.

As of v1 orchestrator phase: **480+ tests, all passing.**
