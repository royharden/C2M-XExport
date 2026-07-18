"""Detect the session the user is currently inside, per source."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .sources import claude, codex


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
    """Newest rollout whose session_meta.cwd matches; else newest overall.

    Returns (path, cwd_matched).
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
            return p, True
    return files[0], False
