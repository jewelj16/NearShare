"""File I/O utilities for the NearShare engine.

Bridges real filesystem operations with the engine's domain types.
No third-party dependencies — uses stdlib only (pathlib, hashlib,
mimetypes).
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
import mmap
from pathlib import Path

from engine.storage.file_store import ConflictRenamer
from engine.types import DEFAULT_CHUNK_SIZE, FileMetadata


# ── reading ───────────────────────────────────────────────────────────────────

def file_metadata_from_path(
    path: Path,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> FileMetadata:
    """Build a FileMetadata from a real file on disk.

    Computes the SHA-256 of the full file, guesses the MIME type from
    the extension, and auto-computes chunk_count from size.

    Args:
        path:       Absolute or relative path to the file.
        chunk_size: Chunk size to use for this transfer (default 64 KB).

    Returns:
        A populated FileMetadata.

    Raises:
        FileNotFoundError: If the path does not exist.
        IsADirectoryError: If the path is a directory.
        OSError: For other I/O errors.
    """
    path = path.resolve()
    if not path.exists():
        raise FileNotFoundError(f"No such file: {path}")
    if path.is_dir():
        raise IsADirectoryError(f"Path is a directory: {path}")

    size = path.stat().st_size
    sha256 = _compute_sha256(path)
    mime_type, _ = mimetypes.guess_type(path.name)

    return FileMetadata(
        name=path.name,
        size=size,
        mime_type=mime_type or None,
        sha256=sha256,
        chunk_size=chunk_size,
    )


class MemoryMappedFile:
    """Context manager for safely mapping a file into memory via mmap.
    
    Provides zero-copy access for reading, and efficient seek-based writing
    for large files without loading everything into RAM.
    """
    def __init__(self, path: Path, write: bool = False, size: int = 0) -> None:
        self.path = path.resolve()
        self.write = write
        self.size = size
        self._f = None
        self._mmap = None

    def __enter__(self):
        if self.write:
            if not self.path.parent.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True)
            if not self.path.exists() or self.path.stat().st_size != self.size:
                with self.path.open("wb") as f:
                    f.truncate(self.size)
            self._f = self.path.open("r+b")
            if self.size == 0:
                self._mmap = bytearray()
            else:
                self._mmap = mmap.mmap(self._f.fileno(), self.size, access=mmap.ACCESS_WRITE)
        else:
            if not self.path.exists():
                raise FileNotFoundError(f"No such file: {self.path}")
            if self.path.stat().st_size == 0:
                self._mmap = b""
            else:
                self._f = self.path.open("rb")
                self._mmap = mmap.mmap(self._f.fileno(), 0, access=mmap.ACCESS_READ)
        return self._mmap

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if isinstance(self._mmap, mmap.mmap):
            self._mmap.close()
        if self._f:
            self._f.close()



# ── writing ───────────────────────────────────────────────────────────────────

def write_file(
    directory: Path,
    metadata: FileMetadata,
    data: bytes,
) -> Path:
    """Write *data* to *directory* under the filename in *metadata*.

    Uses ConflictRenamer to avoid overwriting existing files.

    Args:
        directory: Target directory (created if it doesn't exist).
        metadata:  FileMetadata whose `name` field is used as filename.
        data:      Raw bytes to write.

    Returns:
        The final Path the file was written to (may have a numeric suffix
        if the name conflicted).

    Raises:
        OSError: If the directory cannot be created or the file written.
    """
    directory.mkdir(parents=True, exist_ok=True)
    dest = ConflictRenamer.resolve(directory, metadata.name)
    dest.write_bytes(data)
    return dest


# ── internal ──────────────────────────────────────────────────────────────────

def _compute_sha256(path: Path) -> str:
    """Compute the hex-encoded SHA-256 of a file, streaming in 1 MB blocks.

    Streaming avoids holding the entire file in memory for large files.
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1_048_576), b""):
            h.update(block)
    return h.hexdigest()
