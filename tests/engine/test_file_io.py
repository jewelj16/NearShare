"""Tests for engine.storage.file_io — file stat, SHA-256, MIME, write."""

import hashlib
from pathlib import Path

import pytest

from engine.storage.file_io import (
    _compute_sha256,
    file_metadata_from_path,
    read_file_bytes,
    write_file,
)
from engine.types import DEFAULT_CHUNK_SIZE, FileMetadata


# ── file_metadata_from_path ───────────────────────────────────────────────────

class TestFileMetadataFromPath:
    """Stat + SHA-256 + MIME from a real file."""

    def test_basic_metadata(self, tmp_path: Path) -> None:
        f = tmp_path / "hello.txt"
        f.write_bytes(b"hello world")
        meta = file_metadata_from_path(f)
        assert meta.name == "hello.txt"
        assert meta.size == 11
        assert meta.sha256 == hashlib.sha256(b"hello world").hexdigest()

    def test_mime_type_txt(self, tmp_path: Path) -> None:
        f = tmp_path / "readme.txt"
        f.write_bytes(b"text")
        meta = file_metadata_from_path(f)
        assert meta.mime_type is not None
        assert "text" in meta.mime_type

    def test_mime_type_png(self, tmp_path: Path) -> None:
        f = tmp_path / "image.png"
        f.write_bytes(b"\x89PNG")
        meta = file_metadata_from_path(f)
        assert meta.mime_type == "image/png"

    def test_no_extension_mime_is_none(self, tmp_path: Path) -> None:
        f = tmp_path / "README"
        f.write_bytes(b"readme content")
        meta = file_metadata_from_path(f)
        assert meta.mime_type is None

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        meta = file_metadata_from_path(f)
        assert meta.size == 0
        assert meta.chunk_count == 0

    def test_chunk_count_auto_computed(self, tmp_path: Path) -> None:
        data = b"x" * (DEFAULT_CHUNK_SIZE * 3)
        f = tmp_path / "big.bin"
        f.write_bytes(data)
        meta = file_metadata_from_path(f)
        assert meta.chunk_count == 3

    def test_custom_chunk_size(self, tmp_path: Path) -> None:
        data = b"x" * 1000
        f = tmp_path / "small.bin"
        f.write_bytes(data)
        meta = file_metadata_from_path(f, chunk_size=256)
        assert meta.chunk_size == 256
        assert meta.chunk_count == 4  # ceil(1000 / 256)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            file_metadata_from_path(tmp_path / "nonexistent.txt")

    def test_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IsADirectoryError):
            file_metadata_from_path(tmp_path)


# ── read_file_bytes ───────────────────────────────────────────────────────────

class TestReadFileBytes:
    """Read file content into memory."""

    def test_reads_content(self, tmp_path: Path) -> None:
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00\x01\x02\x03")
        assert read_file_bytes(f) == b"\x00\x01\x02\x03"

    def test_reads_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert read_file_bytes(f) == b""

    def test_missing_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            read_file_bytes(tmp_path / "nope.bin")

    def test_directory_raises(self, tmp_path: Path) -> None:
        with pytest.raises(IsADirectoryError):
            read_file_bytes(tmp_path)


# ── write_file ────────────────────────────────────────────────────────────────

class TestWriteFile:
    """Write bytes to disk with conflict rename."""

    def test_writes_data(self, tmp_path: Path) -> None:
        meta = FileMetadata(name="out.txt", size=5)
        dest = write_file(tmp_path, meta, b"hello")
        assert dest.read_bytes() == b"hello"
        assert dest.name == "out.txt"

    def test_creates_directory(self, tmp_path: Path) -> None:
        subdir = tmp_path / "new" / "subdir"
        meta = FileMetadata(name="f.bin", size=3)
        dest = write_file(subdir, meta, b"abc")
        assert dest.exists()

    def test_conflict_rename(self, tmp_path: Path) -> None:
        meta = FileMetadata(name="photo.jpg", size=4)
        # write first copy
        write_file(tmp_path, meta, b"img1")
        # second write should rename
        dest2 = write_file(tmp_path, meta, b"img2")
        assert dest2.name == "photo (1).jpg"
        assert dest2.read_bytes() == b"img2"

    def test_returns_final_path(self, tmp_path: Path) -> None:
        meta = FileMetadata(name="doc.pdf", size=3)
        dest = write_file(tmp_path, meta, b"pdf")
        assert dest.is_absolute()
        assert dest.parent == tmp_path


# ── _compute_sha256 ───────────────────────────────────────────────────────────

class TestComputeSha256:
    """Streaming SHA-256 matches hashlib reference."""

    def test_matches_hashlib(self, tmp_path: Path) -> None:
        data = b"x" * 4_000_000  # 4 MB
        f = tmp_path / "big.bin"
        f.write_bytes(data)
        expected = hashlib.sha256(data).hexdigest()
        assert _compute_sha256(f) == expected

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / "empty.bin"
        f.write_bytes(b"")
        assert _compute_sha256(f) == hashlib.sha256(b"").hexdigest()
