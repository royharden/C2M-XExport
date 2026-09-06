"""Crash-resistant publication helpers for canonical export refreshes."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from filelock import FileLock


@contextmanager
def export_lock(export_root: Path, identity: str) -> Iterator[None]:
    """Serialize refreshes of one session without cluttering the export root."""
    digest = hashlib.sha256(
        f"{export_root.resolve()}\0{identity}".encode("utf-8")
    ).hexdigest()
    lock_root = Path(tempfile.gettempdir()) / "xexport-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    with FileLock(lock_root / f"{digest}.lock", timeout=30):
        yield


def atomic_write_text(path: Path, value: str) -> None:
    """Publish UTF-8 text with a same-directory atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def atomic_copy(source: Path, destination: Path) -> None:
    """Copy a file's content and metadata, then atomically publish it."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent
    )
    os.close(handle)
    temp_path = Path(temporary)
    try:
        shutil.copy2(source, temp_path)
        os.replace(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
