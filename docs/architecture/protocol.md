# NearShare Protocol (v1)

> **Author**: Kazuha (Day 1 — protocol design)
> **Status**: Draft — all decisions captured in ADR-001 (transport choice).

NearShare uses a custom **length-prefixed binary framing** protocol over
**TLS-wrapped TCP** (see ADR-001 for the rationale).  Peer discovery uses
**UDP multicast/broadcast** on a fixed port and is handled separately by the
discovery sub-system; this document covers the transfer protocol only.

---

## 1. Frame Format

Every protocol message is wrapped in the same frame:

```
 0               1               2               3
 0 1 2 3 4 5 6 7 0 1 2 3 4 5 6 7 0 1 2 3 4 5 6 7 0 1 2 3 4 5 6 7
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|                      Payload Length (4)                       |  big-endian uint32
+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+-+
|  Msg Type (1) |                  Payload (N bytes)            |
+-+-+-+-+-+-+-+-+-               …                -+-+-+-+-+-+-+
```

| Field          | Size    | Type              | Notes                              |
|----------------|---------|-------------------|------------------------------------|
| Payload Length | 4 bytes | big-endian uint32 | Length of `Payload` only, not of the type byte. |
| Message Type   | 1 byte  | uint8             | One of the type codes in §2.       |
| Payload        | N bytes | bytes             | Message-specific (see §3).         |

The maximum frame size is **64 MiB** (`67_108_864` bytes).  Any frame larger
than this MUST be rejected with an ERROR message and the connection closed.

---

## 2. Message Type Codes

| Code | Name              | Sender    | Description                                         |
|------|-------------------|-----------|-----------------------------------------------------|
| 0x01 | HELLO             | Both      | Handshake — protocol version + peer identity.       |
| 0x02 | METADATA          | Sender    | Describe the file(s) to be transferred.             |
| 0x03 | ACCEPT            | Receiver  | Agree to receive the described transfer.            |
| 0x04 | REJECT            | Receiver  | Decline the transfer; gives a reason code.          |
| 0x05 | TRANSFER_START    | Sender    | Signal that chunk transmission is about to begin.   |
| 0x06 | CHUNK             | Sender    | A single chunk of file data.                        |
| 0x07 | ACK               | Receiver  | Cumulative bitmap of acknowledged chunks.           |
| 0x08 | CANCEL            | Both      | Abort the transfer immediately.                     |
| 0x09 | TRANSFER_COMPLETE | Sender    | All chunks sent and verified; transfer done.        |
| 0x0A | ERROR             | Both      | Fatal protocol or I/O error; connection closes.     |

---

## 3. Per-Message Payload Specification

All multi-byte integers are **big-endian**.  Variable-length strings are
encoded as `[uint16 length][UTF-8 bytes]`.

### 3.1 HELLO (0x01)

Sent immediately after the TLS handshake, before any file transfer.

| Field          | Size     | Type   | Notes                          |
|----------------|----------|--------|--------------------------------|
| proto_version  | 2 bytes  | uint16 | Must be `0x0001` for v1.       |
| device_id      | variable | string | UUID-4 of the sending device.  |
| display_name   | variable | string | Human-readable name for the UI.|

Both parties send HELLO and wait for the peer's HELLO before continuing.
If `proto_version` values are incompatible the receiver MUST send ERROR and
close the connection.

### 3.2 METADATA (0x02)

Describes one or more files that will be transferred in this session.

| Field          | Size     | Type    | Notes                                       |
|----------------|----------|---------|---------------------------------------------|
| transfer_id    | variable | string  | UUID-4 for this session.                    |
| file_count     | 2 bytes  | uint16  | Number of `FileEntry` records that follow.  |
| FileEntry (×N) | variable | struct  | See below.                                  |

**FileEntry**:

| Field       | Size     | Type    | Notes                                         |
|-------------|----------|---------|-----------------------------------------------|
| name        | variable | string  | Basename only — no path separators allowed.   |
| size        | 8 bytes  | uint64  | Total file size in bytes.                     |
| chunk_size  | 4 bytes  | uint32  | Negotiated chunk size (default 262 144 B).    |
| chunk_count | 4 bytes  | uint32  | `ceil(size / chunk_size)`.                    |
| mime_type   | variable | string  | Best-guess MIME type, or empty string.        |
| sha256      | 64 bytes | ASCII   | Hex-encoded whole-file SHA-256, or 64 zeroes if not yet computed. |

### 3.3 ACCEPT (0x03)

| Field       | Size     | Type   | Notes                         |
|-------------|----------|--------|-------------------------------|
| transfer_id | variable | string | Echo of METADATA transfer_id. |

### 3.4 REJECT (0x04)

| Field       | Size     | Type   | Notes                                  |
|-------------|----------|--------|----------------------------------------|
| transfer_id | variable | string | Echo of METADATA transfer_id.          |
| reason_code | 1 byte   | uint8  | 0x01 = user declined, 0x02 = no space, 0x03 = other. |
| reason_msg  | variable | string | Human-readable explanation.            |

### 3.5 TRANSFER_START (0x05)

| Field       | Size     | Type   | Notes                                  |
|-------------|----------|--------|----------------------------------------|
| transfer_id | variable | string | Identifies the session.                |

### 3.6 CHUNK (0x06)

| Field       | Size     | Type   | Notes                                         |
|-------------|----------|--------|-----------------------------------------------|
| transfer_id | variable | string | Session this chunk belongs to.                |
| file_index  | 2 bytes  | uint16 | Zero-based index into the METADATA file list. |
| seq         | 4 bytes  | uint32 | Zero-based sequence number within that file.  |
| data_length | 4 bytes  | uint32 | Number of bytes in `data`.                    |
| data        | N bytes  | bytes  | Raw chunk bytes.                              |
| sha256      | 64 bytes | ASCII  | Hex-encoded SHA-256 of `data` only.           |

### 3.7 ACK (0x07)

The receiver sends ACK messages to confirm receipt.  NearShare uses a
**set-based bitmap** (rather than a simple cumulative counter) so that
out-of-order chunks and selective retransmission are supported natively.

| Field        | Size     | Type   | Notes                                         |
|--------------|----------|--------|-----------------------------------------------|
| transfer_id  | variable | string | Session identifier.                           |
| file_index   | 2 bytes  | uint16 | Which file's chunks are being acknowledged.   |
| acked_count  | 4 bytes  | uint32 | Number of `seq` values in the list below.     |
| acked_seqs   | 4×N bytes | uint32[] | Sorted list of acknowledged sequence numbers. |

### 3.8 CANCEL (0x08)

| Field       | Size     | Type   | Notes                                  |
|-------------|----------|--------|----------------------------------------|
| transfer_id | variable | string | Session to cancel.                     |
| reason_msg  | variable | string | Short human-readable explanation.      |

### 3.9 TRANSFER_COMPLETE (0x09)

Sent by the sender after it has received ACK for every chunk of every file
**and** verified the whole-file SHA-256 of each file on the receiver's side
(confirmed via the final cumulative ACK).

| Field       | Size     | Type   | Notes              |
|-------------|----------|--------|--------------------|
| transfer_id | variable | string | Session identifier.|

### 3.10 ERROR (0x0A)

| Field      | Size     | Type   | Notes                                          |
|------------|----------|--------|------------------------------------------------|
| error_code | 2 bytes  | uint16 | 0x0001 = protocol error, 0x0002 = I/O error, 0x0003 = hash mismatch, 0x0004 = version mismatch. |
| message    | variable | string | Human-readable description.                    |

The sender of ERROR MUST close the connection after sending this message.

---

## 4. Connection and Session Flow

```
Sender                            Receiver
  |  ---- TCP connect + TLS ----->  |
  |  <------- TLS handshake ------  |
  |                                 |
  |  --------- HELLO (0x01) ------> |   (both send simultaneously)
  |  <-------- HELLO (0x01) ------  |
  |                                 |
  |  ------- METADATA (0x02) -----> |
  |  <-- ACCEPT (0x03) or           |
  |     REJECT (0x04) ----------    |
  |                                 |
  |  ---- TRANSFER_START (0x05) --> |
  |                                 |
  |  -------- CHUNK (0x06) -------> |  (sliding window, multiple in-flight)
  |  <--------- ACK (0x07) -------  |  (cumulative bitmap per file)
  |      … (retransmit missing) …   |
  |                                 |
  |  -- TRANSFER_COMPLETE (0x09) -> |  (whole-file hash verified)
  |                                 |
  TCP connection closed
```

**Windowing**: The sender MAY have at most `DEFAULT_SEND_WINDOW` (32) chunks
in flight per file before it must wait for the receiver to advance its ACK
bitmap.

**Resumption**: If the connection drops, a new session may be opened.  The
receiver can report previously ACK'd chunks in the first ACK message after a
`TRANSFER_START`, allowing the sender to skip those chunks.  The
`RESUME_TIMEOUT_S` constant (30 s) controls how long a session ID is kept
alive by the receiver.

---

## 5. State Machine

The `TransferState` enum in `engine/types.py` captures every state:

```
IDLE
 └─(connect)─► CONNECTING
                └─(TLS ok + HELLO ok)─► HANDSHAKING
                                         └─(HELLO ok)─► METADATA_SENT  [sender]
                                                          └─(ACCEPT)─► AWAITING_ACCEPT ─► TRANSFERRING
                                                          └─(REJECT)─► FAILED
                TRANSFERRING ──(all chunks ACK'd + hash ok)──► COMPLETE
                TRANSFERRING ──(CANCEL / ERROR / timeout)────► INTERRUPTED
                INTERRUPTED  ──(resume)───────────────────────► TRANSFERRING
                INTERRUPTED  ──(timeout)──────────────────────► FAILED
```

---

## 6. Discovery Protocol (Summary)

Discovery is handled separately by `engine/discovery/`.  Peers broadcast a
compact UDP beacon on `DEFAULT_PORT` (47 321) every 5 seconds.  If a peer's
beacon is not seen for `DISCOVERY_TIMEOUT_S` (15 s), it is removed from the
peer list.  The beacon payload is a minimal HELLO-equivalent containing:
`proto_version`, `device_id`, `display_name`, `tcp_port`.

---

## 7. Security Notes

- Transport security: **TLS 1.3**, self-signed per-device certificate.
- Trust model: **TOFU** (Trust On First Use) — the device ID and certificate
  fingerprint are stored after the first successful connection.
- Each `CHUNK` payload is independently SHA-256'd; the whole file is also
  SHA-256'd and checked before `TRANSFER_COMPLETE` is sent.
- No authentication beyond TOFU is in scope for v1.

---

## 8. Versioning

The `proto_version` field in HELLO is `uint16`.  If a receiver does not
support the sender's `proto_version`, it MUST respond with ERROR (code
`0x0004`) and close the connection.  Minor extensions that are fully backward-
compatible (e.g. new optional fields appended to a payload) do not require a
version bump; a new major feature or breaking format change does.

Currently `PROTOCOL_VERSION = 1` as defined in `engine/types.py`.
