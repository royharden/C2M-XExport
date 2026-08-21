"""Detect the session the user is currently inside, per source."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .sources import claude, codex, cursor


def _norm(p: str | Path) -> str:
    return os.path.normcase(os.path.normpath(str(p)))


def detect_claude(cwd: str | Path) -> tuple[Path | None, bool]:
    """Newest transcript for this cwd's encoded project dir.

    The live-session registry (~/.claude/sessions/<pid>.json) disambiguates when
    several sessions share the project dir. Returns (path, registry_confirmed).
    """
    proj = claude.projects_dir() / claude.encode_project_dir(cwd)
    if not proj.is_dir():
        return None, False
    candidates = sorted(
        (p for p in proj.glob("*.jsonl")),
        key=lambda p: p.stat().st_mtime, reverse=True,
    )
    if not candidates:
        return None, False

    registry_ids: set[str] = set()
    reg_dir = claude.claude_home() / "sessions"
    if reg_dir.is_dir():
        for f in reg_dir.glob("*.json"):
            try:
                obj = json.loads(f.read_text(encoding="utf-8", errors="replace"))
            except (json.JSONDecodeError, OSError):
                continue
            if isinstance(obj, dict) and _norm(obj.get("cwd", "")) == _norm(cwd):
                sid = obj.get("sessionId")
                if sid:
                    registry_ids.add(sid)

    for p in candidates:
        if p.stem in registry_ids:
            return p, True
    return candidates[0], False


def detect_codex(cwd: str | Path, scan_limit: int = 30) -> tuple[Path | None, bool]:
    """Fallback to the newest rollout whose session_meta.cwd matches.

    A working directory is shared by concurrent Codex tasks, so even a cwd match
    is only a heuristic, not confirmation of the active task. ``current`` uses
    ``CODEX_THREAD_ID`` when Codex Desktop exposes it; this helper remains for
    manual environments that do not have that variable.

    Returns (path, active_session_confirmed), where the second value is always
    False for this fallback.
    """
    files: list[Path] = []
    sessions_root = codex.codex_home() / "sessions"
    if sessions_root.is_dir():
        files = sorted(sessions_root.rglob("rollout-*.jsonl"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return None, False

    for p in files[:scan_limit]:
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                first = f.readline()
            obj = json.loads(first)
            meta_cwd = (obj.get("payload") or {}).get("cwd", "")
        except (json.JSONDecodeError, OSError, ValueError):
            continue
        if meta_cwd and _norm(meta_cwd) == _norm(cwd):
            return p, False
    return files[0], False


def detect_cursor(cwd: str | Path) -> tuple[Path | None, bool]:
    """Newest agent transcript for this cwd's encoded Cursor project folder.

    ``CURSOR_CONVERSATION_ID`` is preferred by ``current`` when present; this
    helper is the cwd heuristic fallback. Returns (path, confirmed) where
    confirmed is always False for this heuristic.
    """
    proj = cursor.projects_dir() / cursor.encode_project_dir(cwd)
    transcripts = proj / "agent-transcripts"
    if not transcripts.is_dir():
        return None, False
    candidates: list[Path] = []
    for folder in transcripts.iterdir():
        if not folder.is_dir():
            continue
        path = folder / f"{folder.name}.jsonl"
        if path.is_file():
            candidates.append(path)
    if not candidates:
        return None, False
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0], False
