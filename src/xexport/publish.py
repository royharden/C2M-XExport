"""Crash-resistant publication helpers for canonical export refreshes."""

from __future__ import annotations

import glob as _glob
import hashlib
import os
import shutil
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import click
from filelock import FileLock, Timeout

# os.replace onto a path OneDrive or an antivirus scanner is holding open fails with
# PermissionError, and this tool's whole reason to exist lives in a OneDrive folder.
# The hold is momentary, so a short backoff turns a hard failure into a pause.
_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF = 0.15
_STALE_TEMP_SECONDS = 3600


@contextmanager
def export_lock(export_root: Path, identity: str) -> Iterator[None]:
    """Serialize refreshes of one session without cluttering the export root."""
    digest = hashlib.sha256(
        f"{export_root.resolve()}\0{identity}".encode("utf-8")
    ).hexdigest()
    lock_root = Path(tempfile.gettempdir()) / "xexport-locks"
    try:
        lock_root.mkdir(parents=True, exist_ok=True)
        lock = FileLock(lock_root / f"{digest}.lock", timeout=30)
        lock.acquire()
    except Timeout:
        # Proceed rather than fail. The lock stops two overlapping refreshes from
        # interleaving, but a refresh now writes a complete document and swaps it
        # in atomically, so the worst case without it is that one whole valid
        # export replaces another whole valid export. Losing the receipt entirely
        # would be worse -- and this runs inside a Stop hook, where an exception
        # is a blocked turn.
        click.echo("Warning: another xexport is still writing this export; "
                   "continuing without the lock.", err=True)
        yield
        return
    except OSError:
        yield                       # no usable temp dir: proceed unlocked
        return
    try:
        yield
    finally:
        lock.release()


def _sweep_stale_temps(directory: Path, name: str) -> None:
    """Drop temp files a killed process left behind, so they cannot accumulate."""
    try:
        # sanitize_title permits [ and ], which glob would read as a character
        # class and quietly match nothing.
        for leftover in directory.glob(f".{_glob.escape(name)}.*.tmp"):
            try:
                if time.time() - leftover.stat().st_mtime > _STALE_TEMP_SECONDS:
                    leftover.unlink()
            except OSError:
                continue
    except OSError:
        return


def _replace_with_retry(source: Path, destination: Path) -> None:
    for attempt in range(_REPLACE_ATTEMPTS):
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if attempt == _REPLACE_ATTEMPTS - 1:
                raise
            time.sleep(_REPLACE_BACKOFF * (attempt + 1))


def atomic_write_text(path: Path, value: str) -> None:
    """Publish UTF-8 text with a same-directory atomic replacement."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _sweep_stale_temps(path.parent, path.name)
    handle, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temp_path = Path(temporary)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        _replace_with_retry(temp_path, path)
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
        _replace_with_retry(temp_path, destination)
    finally:
        temp_path.unlink(missing_ok=True)
