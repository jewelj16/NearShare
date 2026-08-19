"""Payload codec for JOIN_SESSION messages.

Sent by the sender on secondary TCP connections to associate them with an
existing TransferSession.
"""

from engine.protocol.codec import CodecError


def build_join_session_payload(session_id: str) -> bytes:
    """Encode a JOIN_SESSION payload (just the session ID as UTF-8)."""
    return session_id.encode("utf-8")


def parse_join_session_payload(payload: bytes) -> str:
    """Decode a JOIN_SESSION payload into the session ID."""
    if not payload:
        raise CodecError("Empty JOIN_SESSION payload")
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError:
        raise CodecError("JOIN_SESSION payload is not valid UTF-8")
