"""Tests for engine.storage.file_store — filename conflict renaming."""

import os
from pathlib import Path

import pytest

from engine.storage.file_store import ConflictRenamer


class TestConflictRenamer:
    """Rename-on-conflict logic for duplicate filenames."""

    def test_no_conflict(self, tmp_path: Path) -> None:
        result = ConflictRenamer.resolve(tmp_path, "photo.jpg")
        assert result == tmp_path / "photo.jpg"

    def test_single_conflict(self, tmp_path: Path) -> None:
        (tmp_path / "photo.jpg").write_text("existing")
        result = ConflictRenamer.resolve(tmp_path, "photo.jpg")
        assert result == tmp_path / "photo (1).jpg"

    def test_double_conflict(self, tmp_path: Path) -> None:
        (tmp_path / "photo.jpg").write_text("existing")
        (tmp_path / "photo (1).jpg").write_text("existing")
        result = ConflictRenamer.resolve(tmp_path, "photo.jpg")
        assert result == tmp_path / "photo (2).jpg"

    def test_triple_conflict(self, tmp_path: Path) -> None:
        for i in range(3):
            suffix = f" ({i})" if i > 0 else ""
            (tmp_path / f"doc{suffix}.pdf").write_text("x")
        result = ConflictRenamer.resolve(tmp_path, "doc.pdf")
        assert result == tmp_path / "doc (3).pdf"

    def test_no_extension(self, tmp_path: Path) -> None:
        (tmp_path / "README").write_text("existing")
        result = ConflictRenamer.resolve(tmp_path, "README")
        assert result == tmp_path / "README (1)"

    def test_double_extension(self, tmp_path: Path) -> None:
        (tmp_path / "archive.tar.gz").write_text("existing")
        result = ConflictRenamer.resolve(tmp_path, "archive.tar.gz")
        assert result == tmp_path / "archive (1).tar.gz"

    def test_tar_bz2(self, tmp_path: Path) -> None:
        (tmp_path / "backup.tar.bz2").write_text("existing")
        result = ConflictRenamer.resolve(tmp_path, "backup.tar.bz2")
        assert result == tmp_path / "backup (1).tar.bz2"

    def test_different_files_no_conflict(self, tmp_path: Path) -> None:
        (tmp_path / "photo.jpg").write_text("existing")
        result = ConflictRenamer.resolve(tmp_path, "photo.png")
        assert result == tmp_path / "photo.png"

    def test_returns_absolute_path(self, tmp_path: Path) -> None:
        result = ConflictRenamer.resolve(tmp_path, "file.txt")
        assert result.is_absolute()


class TestSplitName:
    """Internal name splitting logic."""

    def test_simple_extension(self) -> None:
        stem, ext = ConflictRenamer._split_name("photo.jpg")
        assert stem == "photo"
        assert ext == ".jpg"

    def test_no_extension(self) -> None:
        stem, ext = ConflictRenamer._split_name("README")
        assert stem == "README"
        assert ext == ""

    def test_tar_gz(self) -> None:
        stem, ext = ConflictRenamer._split_name("archive.tar.gz")
        assert stem == "archive"
        assert ext == ".tar.gz"

    def test_dotfile(self) -> None:
        stem, ext = ConflictRenamer._split_name(".gitignore")
        assert stem == ".gitignore"
        assert ext == ""
