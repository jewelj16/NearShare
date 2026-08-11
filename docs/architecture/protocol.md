# NearShare Protocol (v1)

NearShare uses a custom length-prefixed binary framing protocol over TLS-wrapped TCP. 
Each frame has the format: `[4-byte big-endian length][1-byte message type][payload]`.

## Message Types (v1)

1. **HELLO**: Sent immediately after connection to establish protocol version and basic peer info.
2. **METADATA**: Sent by the sender to describe the file(s) being transferred (name, size, SHA-256, negotiated chunk size).
3. **ACCEPT/REJECT**: Sent by the receiver in response to METADATA to accept or decline the transfer.
4. **TRANSFER_START**: Sent by the sender to indicate chunks will begin transmitting.
5. **CHUNK**: Contains a portion of the file data (transfer ID, file index, sequence number, bytes, SHA-256).
6. **ACK**: Sent by the receiver to acknowledge receipt of chunks (typically a cumulative bitmap).
7. **CANCEL**: Sent by either party to explicitly abort the transfer.
8. **TRANSFER_COMPLETE**: Sent by the sender when all chunks are transmitted and acknowledged.
9. **ERROR**: Sent by either party if a fatal protocol or IO error occurs.

## Connection Flow
`DISCOVERY → CONNECT → HELLO → METADATA → ACCEPT/REJECT → TRANSFER_START → CHUNK↔ACK (windowed) → retransmit missing → whole-file hash check → TRANSFER_COMPLETE`
