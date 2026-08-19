# engine/protocol — NearShare v1 wire format serialization.
#
# Each sub-module handles one message type's build/parse:
#   codec.py          — frame encode/decode + MessageType enum
#   wire.py           — shared string encode/decode helpers
#   handshake.py      — HELLO (0x01)
#   metadata.py       — METADATA (0x02) / ACCEPT (0x03) / REJECT (0x04)
#   transfer_start.py — TRANSFER_START (0x05)
#   chunk_msg.py      — CHUNK (0x06)
#   ack_msg.py        — ACK (0x07)
#   cancel.py         — CANCEL (0x08)
#   complete.py       — TRANSFER_COMPLETE (0x09)
#   join_session.py   — JOIN_SESSION (0x0B)
