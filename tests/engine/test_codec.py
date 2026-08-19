"""Tests for engine.protocol.codec — round-trip encode/decode,
multiple message shapes, and malformed-frame rejection."""

import struct

import pytest

from engine.protocol.codec import (
    MAX_PAYLOAD_SIZE,
    CodecError,
    Frame,
    MessageType,
    _HEADER_FMT,
    _HEADER_SIZE,
    decode_frame,
    decode_frame_header,
    encode_frame,
)


# Round-trip encode/decode

class TestRoundTrip:
    """Encode a frame and decode it back — the result should match."""

    def test_hello_empty_payload(self) -> None:
        """HELLO with no payload — simplest possible frame."""
        raw = encode_frame(MessageType.HELLO)
        frame = decode_frame(raw)
        assert frame.msg_type is MessageType.HELLO
        assert frame.payload == b""

    def test_chunk_with_binary_payload(self) -> None:
        """CHUNK with a realistic binary payload."""
        payload = bytes(range(256)) * 4  # 1 KB of non-trivial bytes
        raw = encode_frame(MessageType.CHUNK, payload)
        frame = decode_frame(raw)
        assert frame.msg_type is MessageType.CHUNK
        assert frame.payload == payload

    def test_metadata_with_text_payload(self) -> None:
        """METADATA with a UTF-8 encoded string payload."""
        payload = "photo.jpg".encode("utf-8")
        raw = encode_frame(MessageType.METADATA, payload)
        frame = decode_frame(raw)
        assert frame.msg_type is MessageType.METADATA
        assert frame.payload == payload

    def test_error_with_short_payload(self) -> None:
        """ERROR with a small error code payload."""
        payload = struct.pack("!H", 0x0001)  # 2-byte error code
        raw = encode_frame(MessageType.ERROR, payload)
        frame = decode_frame(raw)
        assert frame.msg_type is MessageType.ERROR
        assert frame.payload == payload

    def test_accept_round_trip(self) -> None:
        """ACCEPT with a transfer_id string payload."""
        payload = b"550e8400-e29b-41d4-a716-446655440000"
        raw = encode_frame(MessageType.ACCEPT, payload)
        frame = decode_frame(raw)
        assert frame.msg_type is MessageType.ACCEPT
        assert frame.payload == payload

    def test_cancel_round_trip(self) -> None:
        """CANCEL with a reason message."""
        payload = "User cancelled".encode("utf-8")
        raw = encode_frame(MessageType.CANCEL, payload)
        frame = decode_frame(raw)
        assert frame.msg_type is MessageType.CANCEL
        assert frame.payload == payload

    def test_all_message_types_round_trip(self) -> None:
        """Every MessageType should survive a round-trip."""
        for mt in MessageType:
            payload = f"test-{mt.name}".encode("utf-8")
            raw = encode_frame(mt, payload)
            frame = decode_frame(raw)
            assert frame.msg_type is mt
            assert frame.payload == payload


# Frame structure checks

class TestFrameStructure:
    """Verify the wire format matches the protocol spec."""

    def test_header_size_is_5_bytes(self) -> None:
        assert _HEADER_SIZE == 5

    def test_empty_payload_frame_is_5_bytes(self) -> None:
        raw = encode_frame(MessageType.HELLO)
        assert len(raw) == 5

    def test_payload_length_in_header(self) -> None:
        """The first 4 bytes should be the big-endian payload length."""
        payload = b"abcdef"
        raw = encode_frame(MessageType.METADATA, payload)
        length_field = struct.unpack("!I", raw[:4])[0]
        assert length_field == len(payload)

    def test_message_type_byte(self) -> None:
        """The 5th byte should be the message type code."""
        raw = encode_frame(MessageType.CHUNK, b"data")
        assert raw[4] == MessageType.CHUNK.value

    def test_payload_follows_header(self) -> None:
        payload = b"hello world"
        raw = encode_frame(MessageType.TRANSFER_START, payload)
        assert raw[5:] == payload


# Malformed frame rejection

class TestMalformedFrames:
    """The decoder must reject bad input with CodecError."""

    def test_empty_data(self) -> None:
        with pytest.raises(CodecError, match="too short"):
            decode_frame(b"")

    def test_truncated_header(self) -> None:
        with pytest.raises(CodecError, match="too short"):
            decode_frame(b"\x00\x00")

    def test_unknown_message_type(self) -> None:
        # Valid length (0), but message type 0xFF is not defined
        bad = struct.pack("!IB", 0, 0xFF)
        with pytest.raises(CodecError, match="Unknown message type"):
            decode_frame(bad)

    def test_incomplete_payload(self) -> None:
        # Header says 100 bytes of payload, but we only give 5
        header = struct.pack(_HEADER_FMT, 100, MessageType.HELLO)
        data = header + b"short"
        with pytest.raises(CodecError, match="Incomplete frame"):
            decode_frame(data)

    def test_payload_exceeds_max(self) -> None:
        """Encoding a payload larger than 64 MiB should fail."""
        # We won't actually allocate 64 MiB — just check the guard
        huge = b"\x00" * (MAX_PAYLOAD_SIZE + 1)
        with pytest.raises(CodecError, match="too large"):
            encode_frame(MessageType.CHUNK, huge)

    def test_header_claims_oversized_payload(self) -> None:
        """A header claiming payload > MAX_PAYLOAD_SIZE should be rejected."""
        bad_header = struct.pack(_HEADER_FMT, MAX_PAYLOAD_SIZE + 1, MessageType.HELLO)
        with pytest.raises(CodecError, match="exceeds max"):
            decode_frame(bad_header)


# decode_frame_header

class TestDecodeFrameHeader:
    """Test the streaming-friendly header parser."""

    def test_valid_header(self) -> None:
        header = struct.pack(_HEADER_FMT, 42, MessageType.ACK)
        length, msg_type = decode_frame_header(header)
        assert length == 42
        assert msg_type is MessageType.ACK

    def test_header_too_short(self) -> None:
        with pytest.raises(CodecError, match="too short"):
            decode_frame_header(b"\x00\x00")

    def test_unknown_type_in_header(self) -> None:
        bad_header = struct.pack(_HEADER_FMT, 0, 0xFE)
        with pytest.raises(CodecError, match="Unknown message type"):
            decode_frame_header(bad_header)

    def test_oversized_length_in_header(self) -> None:
        bad_header = struct.pack(_HEADER_FMT, MAX_PAYLOAD_SIZE + 1, MessageType.HELLO)
        with pytest.raises(CodecError, match="exceeds max"):
            decode_frame_header(bad_header)


# MessageType enum coverage

class TestMessageType:
    """Make sure all 10 protocol message types are defined."""

    EXPECTED = {
        "HELLO", "METADATA", "ACCEPT", "REJECT", "TRANSFER_START",
        "CHUNK", "ACK", "CANCEL", "TRANSFER_COMPLETE", "ERROR",
        "JOIN_SESSION",
    }

    def test_all_members_present(self) -> None:
        actual = {m.name for m in MessageType}
        assert actual == self.EXPECTED

    def test_member_count(self) -> None:
        assert len(MessageType) == 11

    def test_codes_are_sequential(self) -> None:
        codes = [m.value for m in MessageType]
        assert codes == list(range(0x01, 0x0C))


# Frame dataclass

class TestFrame:
    """Basic checks on the Frame frozen dataclass."""

    def test_frozen(self) -> None:
        f = Frame(msg_type=MessageType.HELLO, payload=b"")
        with pytest.raises(AttributeError):
            f.payload = b"nope"  # type: ignore[misc]

    def test_equality(self) -> None:
        a = Frame(msg_type=MessageType.HELLO, payload=b"x")
        b = Frame(msg_type=MessageType.HELLO, payload=b"x")
        assert a == b

    def test_inequality(self) -> None:
        a = Frame(msg_type=MessageType.HELLO, payload=b"x")
        b = Frame(msg_type=MessageType.CHUNK, payload=b"x")
        assert a != b
