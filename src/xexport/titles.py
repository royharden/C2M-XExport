"""Chat-title → Windows/OneDrive-safe file and folder names."""

from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path

_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_title(title: str, max_len: int = 80) -> str:
    """Turn a chat title into a safe file/folder name (spaces preserved)."""
    name = unicodedata.normalize("NFC", title or "")
    name = _INVALID.sub("", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Windows: names may not end with a dot or space
    name = name.rstrip(". ")
    if len(name) > max_len:
        name = name[:max_len].rstrip(". ")
    if not name:
        name = "session"
    if name.split(".")[0].upper() in _RESERVED:
        name = f"_{name}"
    return name


def unique_path(directory: Path, name: str, suffix: str = "") -> Path:
    """Return directory/name+suffix, appending " (2)", " (3)"… on collision."""
    candidate = directory / f"{name}{suffix}"
    n = 2
    # lexists, not exists: a dangling junction or symlink is a name that is
    # taken even though nothing is behind it, and a caller that then tries to
    # create it would be told so forever.
    while os.path.lexists(candidate):
        candidate = directory / f"{name} ({n}){suffix}"
        n += 1
    return candidate
