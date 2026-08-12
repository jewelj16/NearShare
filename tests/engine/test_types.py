"""Unit tests for engine.types — DeviceId, PeerInfo, FileMetadata, Chunk, enums."""

import math

from engine.types import (
    DEFAULT_CHUNK_SIZE,
    DEFAULT_PORT,
    DEFAULT_SEND_WINDOW,
    PROTOCOL_VERSION,
    Chunk,
    DeviceId,
    FileMetadata,
    PeerInfo,
    TransferDirection,
    TransferState,
    new_device_id,
)


# DeviceId

class TestDeviceId:
    """DeviceId is a NewType over str — lightweight but type-distinct."""

    def test_construction(self) -> None:
        did = DeviceId("abc-123")
        assert did == "abc-123"

    def test_new_device_id_is_unique(self) -> None:
        a = new_device_id()
        b = new_device_id()
        assert a != b

    def test_new_device_id_is_uuid_format(self) -> None:
        did = new_device_id()
        parts = did.split("-")
        assert len(parts) == 5, "Expected UUID-4 format (5 dash-separated groups)"


# PeerInfo

class TestPeerInfo:
    """PeerInfo is a mutable dataclass (last_seen updates over time)."""

    def _make_peer(self, **overrides: object) -> PeerInfo:
        defaults = dict(
            device_id=DeviceId("dev-1"),
            display_name="Alice's Laptop",
            ip="192.168.1.42",
            port=DEFAULT_PORT,
        )
        defaults.update(overrides)
        return PeerInfo(**defaults)  # type: ignore[arg-type]

    def test_construction_with_defaults(self) -> None:
        p = self._make_peer()
        assert p.device_id == DeviceId("dev-1")
        assert p.display_name == "Alice's Laptop"
        assert p.ip == "192.168.1.42"
        assert p.port == DEFAULT_PORT
        assert p.proto_version == PROTOCOL_VERSION
        assert p.last_seen == 0.0

    def test_construction_with_overrides(self) -> None:
        p = self._make_peer(proto_version=2, last_seen=1000.0)
        assert p.proto_version == 2
        assert p.last_seen == 1000.0

    def test_equality(self) -> None:
        a = self._make_peer()
        b = self._make_peer()
        assert a == b

    def test_inequality_different_device_id(self) -> None:
        a = self._make_peer(device_id=DeviceId("dev-1"))
        b = self._make_peer(device_id=DeviceId("dev-2"))
        assert a != b

    def test_last_seen_mutable(self) -> None:
        p = self._make_peer()
        p.last_seen = 999.0
        assert p.last_seen == 999.0


# FileMetadata

class TestFileMetadata:
    """FileMetadata is a frozen dataclass with auto-computed chunk_count."""

    def test_construction_basic(self) -> None:
        fm = FileMetadata(name="photo.jpg", size=1_000_000)
        assert fm.name == "photo.jpg"
        assert fm.size == 1_000_000
        assert fm.mime_type is None
        assert fm.sha256 is None
        assert fm.chunk_size == DEFAULT_CHUNK_SIZE

    def test_auto_chunk_count(self) -> None:
        fm = FileMetadata(name="file.bin", size=1_000_000)
        expected = math.ceil(1_000_000 / DEFAULT_CHUNK_SIZE)
        assert fm.chunk_count == expected

    def test_auto_chunk_count_exact_multiple(self) -> None:
        size = DEFAULT_CHUNK_SIZE * 4
        fm = FileMetadata(name="file.bin", size=size)
        assert fm.chunk_count == 4

    def test_auto_chunk_count_one_byte_over(self) -> None:
        size = DEFAULT_CHUNK_SIZE * 4 + 1
        fm = FileMetadata(name="file.bin", size=size)
        assert fm.chunk_count == 5

    def test_explicit_chunk_count_honoured(self) -> None:
        fm = FileMetadata(name="file.bin", size=1_000_000, chunk_count=42)
        assert fm.chunk_count == 42

    def test_empty_file(self) -> None:
        fm = FileMetadata(name="empty.txt", size=0)
        assert fm.chunk_count == 0

    def test_custom_chunk_size(self) -> None:
        fm = FileMetadata(name="file.bin", size=1024, chunk_size=512)
        assert fm.chunk_count == 2

    def test_frozen(self) -> None:
        fm = FileMetadata(name="file.bin", size=100)
        try:
            fm.name = "other.bin"  # type: ignore[misc]
            assert False, "Should have raised FrozenInstanceError"
        except AttributeError:
            pass  # expected

    def test_equality(self) -> None:
        a = FileMetadata(name="f.txt", size=500)
        b = FileMetadata(name="f.txt", size=500)
        assert a == b

    def test_inequality(self) -> None:
        a = FileMetadata(name="f.txt", size=500)
        b = FileMetadata(name="g.txt", size=500)
        assert a != b

    def test_with_all_fields(self) -> None:
        fm = FileMetadata(
            name="report.pdf",
            size=2_000_000,
            mime_type="application/pdf",
            sha256="abcdef1234567890" * 4,
            chunk_size=128_000,
            chunk_count=16,
        )
        assert fm.mime_type == "application/pdf"
        assert fm.sha256 == "abcdef1234567890" * 4
        assert fm.chunk_size == 128_000
        assert fm.chunk_count == 16


# Chunk

class TestChunk:
    """Chunk is a frozen dataclass representing a single piece of file data."""

    def test_construction(self) -> None:
        c = Chunk(
            transfer_id="sess-1",
            file_index=0,
            seq=3,
            data=b"hello",
            sha256="abc123",
        )
        assert c.transfer_id == "sess-1"
        assert c.file_index == 0
        assert c.seq == 3
        assert c.data == b"hello"
        assert c.sha256 == "abc123"

    def test_frozen(self) -> None:
        c = Chunk(
            transfer_id="sess-1",
            file_index=0,
            seq=0,
            data=b"x",
            sha256="a",
        )
        try:
            c.seq = 99  # type: ignore[misc]
            assert False, "Should have raised FrozenInstanceError"
        except AttributeError:
            pass

    def test_equality(self) -> None:
        kwargs = dict(
            transfer_id="s1", file_index=0, seq=0, data=b"x", sha256="a"
        )
        assert Chunk(**kwargs) == Chunk(**kwargs)

    def test_inequality(self) -> None:
        base = dict(
            transfer_id="s1", file_index=0, seq=0, data=b"x", sha256="a"
        )
        assert Chunk(**base) != Chunk(**{**base, "seq": 1})


# Enum coverage

class TestTransferState:
    """Verify all expected TransferState members exist."""

    EXPECTED_MEMBERS = {
        "IDLE",
        "CONNECTING",
        "HANDSHAKING",
        "METADATA_SENT",
        "AWAITING_ACCEPT",
        "TRANSFERRING",
        "INTERRUPTED",
        "COMPLETE",
        "FAILED",
    }

    def test_all_members_present(self) -> None:
        actual = {m.name for m in TransferState}
        assert actual == self.EXPECTED_MEMBERS

    def test_member_count(self) -> None:
        assert len(TransferState) == 9

    def test_values_are_lowercase_names(self) -> None:
        for member in TransferState:
            assert member.value == member.name.lower()


class TestTransferDirection:
    """Verify TransferDirection members."""

    def test_send_and_receive(self) -> None:
        assert TransferDirection.SEND.value == "send"
        assert TransferDirection.RECEIVE.value == "receive"

    def test_member_count(self) -> None:
        assert len(TransferDirection) == 2
