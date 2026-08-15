# NearShare Engine Architecture Overview

> **Status**: Living document — updated as the engine evolves.
> **Last updated**: Day 7 (Phase 1 review)

## Module Map

```
engine/
├── types.py              Core domain types (DeviceId, PeerInfo, FileMetadata, Chunk, enums)
├── protocol/
│   ├── codec.py          Length-prefixed frame encode/decode, MessageType enum
│   ├── wire.py           Shared wire helpers (variable-length string encode/decode)
│   ├── handshake.py      HELLO exchange and version negotiation
│   └── metadata.py       METADATA/ACCEPT/REJECT build/parse + async flow helpers
├── transfer/
│   ├── session.py        TransferSession — per-transfer state and ack tracking
│   └── chunker.py        split() generator + ChunkAssembler for reassembly
├── integrity/
│   └── hasher.py         Per-chunk and whole-file SHA-256 utilities
├── transport/
│   ├── interfaces.py     Connection / Transport Protocol classes (PEP 544)
│   └── fake.py           In-memory FakeTransport with fault injection
├── discovery/
│   ├── interfaces.py     DiscoveryService Protocol class
│   └── fake.py           In-memory FakeDiscovery for testing
└── storage/
    └── interfaces.py     FileStore Protocol class
```

## Layering

The engine follows a strict dependency order:

```
types.py (no internal deps)
  ↓
protocol/codec.py (depends on: types)
  ↓
protocol/wire.py (no internal deps)
  ↓
protocol/handshake.py, metadata.py (depends on: codec, wire, types)
  ↓
transfer/session.py, chunker.py (depends on: types)
  ↓
integrity/hasher.py (no internal deps)
  ↓
transport/interfaces.py (depends on: codec)
  ↓
discovery/interfaces.py, storage/interfaces.py (depends on: types)
```

No circular dependencies exist. All interface modules use PEP 544
structural `Protocol` classes — concrete implementations do not need
to explicitly inherit from them.

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

### Fake Implementations for Testing
`FakeTransport` and `FakeDiscovery` allow full protocol testing without
any network I/O. `FaultConfig` supports configurable drop/delay/duplicate
rates with deterministic seeding for reproducible tests.

## Test Coverage

All tests live under `tests/engine/` and run via `pytest`. The test
suite is designed to be fast (< 1s) with no network or filesystem I/O.

As of Day 7: **175+ tests, all passing.**
