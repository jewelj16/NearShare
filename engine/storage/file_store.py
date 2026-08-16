"""Concrete FileStore with rename-on-conflict logic.

Implements the FileStore interface for local filesystem storage.
When a file already exists at the target path, it is renamed with a
numeric suffix (e.g. ``photo.jpg`` → ``photo (1).jpg``).
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


class ConflictRenamer:
    """Generates conflict-free filenames by appending a numeric suffix.

    Given a base directory and a filename, returns a path that does not
    collide with any existing file in that directory.

    Examples:
        ``photo.jpg`` → ``photo.jpg`` (if no conflict)
        ``photo.jpg`` → ``photo (1).jpg`` (if ``photo.jpg`` exists)
        ``photo.jpg`` → ``photo (2).jpg`` (if both exist)
        ``archive.tar.gz`` → ``archive (1).tar.gz``
    """

    @staticmethod
    def resolve(directory: Path, filename: str) -> Path:
        """Return a conflict-free path for *filename* in *directory*.

        Args:
            directory: The target directory.
            filename:  The desired filename (basename only).

        Returns:
            An absolute Path that does not collide with existing files.
        """
        target = directory / filename
        if not target.exists():
            return target

        stem, ext = ConflictRenamer._split_name(filename)
        counter = 1
        while True:
            candidate = directory / f"{stem} ({counter}){ext}"
            if not candidate.exists():
                return candidate
            counter += 1

    @staticmethod
    def _split_name(filename: str) -> tuple[str, str]:
        """Split a filename into stem and extension, handling multi-part extensions.

        ``archive.tar.gz`` → (``archive``, ``.tar.gz``)
        ``photo.jpg``      → (``photo``, ``.jpg``)
        ``README``         → (``README``, ````)
        """
        # Handle double extensions like .tar.gz, .tar.bz2
        known_double = {".tar.gz", ".tar.bz2", ".tar.xz"}
        for ext in known_double:
            if filename.endswith(ext):
                stem = filename[: -len(ext)]
                return stem, ext

        p = Path(filename)
        return p.stem, p.suffix
