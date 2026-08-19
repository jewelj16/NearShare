"""File I/O utilities for the NearShare engine.

Bridges real filesystem operations with the engine's domain types.
No third-party dependencies — uses stdlib only (pathlib, hashlib,
mimetypes).
"""

from __future__ import annotations

import hashlib
import mimetypes
import os
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
        chunk_size: Chunk size to use for this transfer (default 256 KB).

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


import mmap
from contextlib import contextmanager
from collections.abc import Generator

@contextmanager
def mmap_file(path: Path) -> Generator[mmap.mmap | bytes, None, None]:
    """Map an entire file into memory using mmap.

    For empty files, yields an empty bytes object since mmap cannot map 0 bytes.

    Args:
        path: Path to the file.

    Yields:
        An mmap object (or empty bytes) that supports slicing like bytes.

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
    if size == 0:
        yield b""
        return
        
    with path.open("rb") as f:
        with mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as mm:
            yield mm

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
