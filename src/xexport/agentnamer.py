"""Optional AgentNamer integration.

xexport never initializes or mutates a callsign registry. It only reads an
existing registry, or recognizes the callsign declaration in a subagent's own
transcript, so projects that do not use AgentNamer retain the normal name.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

_ASSIGNMENT_RE = re.compile(
    r"(?im)^\s*(?:[-*]\s*)?(?:your\s+callsign\s+is|callsign:)\s+"
    r"([0-9A-Z]{2}[0-9]{2}_(?:Claude|Codex|Cursor|Grok)_"
    r"[A-Za-z0-9-]+(?:_[A-Za-z0-9-]+)*)\b"
)
_INJECTED_PREFIXES = (
    "<recommended_plugins>", "# AGENTS.md instructions", "<environment_context>",
    "<permissions instructions>", "<user_instructions>", "<turn_context",
    "<ide_context", "<app_context", "<system", "<collaboration",
)
_LIVE_STATUSES = {"active", "reserved", "done"}


def _first_user_record_text(record: object) -> str:
    """Return text from the first user-shaped record across supported stores."""
    if not isinstance(record, dict):
        return ""
    candidate = record.get("payload")
    if not isinstance(candidate, dict) or candidate.get("role") != "user":
        candidate = record
    message = candidate.get("message")
    if isinstance(message, dict):
        role = message.get("role") or candidate.get("role")
        content = message.get("content")
    else:
        role = candidate.get("role")
        content = candidate.get("content")
    if role != "user" and candidate.get("type") != "user":
        return ""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    texts = []
    for item in content:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            texts.append(item["text"])
    return "\n".join(texts)


def callsign_from_transcript(path: Path | None) -> str:
    """Read only a subagent's opening task record for its callsign."""
    if path is None or not path.is_file():
        return ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line_number, line in enumerate(f):
                if line_number >= 64:
                    break
                try:
                    text = _first_user_record_text(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    continue
                if not text:
                    continue
                if text.lstrip().startswith(_INJECTED_PREFIXES):
                    continue
                match = _ASSIGNMENT_RE.search(text)
                return match.group(1) if match else ""
    except OSError:
        return ""
    return ""


def strip_assignment_preamble(text: str) -> str:
    """Remove AgentNamer's complete first-line child assignment boilerplate."""
    lines = text.splitlines()
    if not lines:
        return text
    match = _ASSIGNMENT_RE.search(lines[0])
    if not match:
        return text
    remainder = lines[0][match.end():].strip(" .:-")
    # The canonical line contains parent/check-in instructions. None of that is
    # a useful chat title; the delegated task begins on the following line.
    if "run:" in remainder.lower() or "do not claim" in remainder.lower():
        remainder = ""
    return "\n".join(([remainder] if remainder else []) + lines[1:]).strip()


def _git_main_root(cwd: Path) -> Path | None:
    """Resolve the main checkout root shared by linked worktrees."""
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), "rev-parse", "--git-common-dir"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    common = Path(result.stdout.strip())
    if not common.is_absolute():
        common = (cwd / common).resolve()
    return common.parent if common.name == ".git" else None


def _registry_from_root(root: Path) -> Path | None:
    pointer = root / ".agent-registry-path"
    if pointer.is_file():
        try:
            value = pointer.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            value = ""
        if value:
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = root / candidate
            if (candidate / "config.json").is_file():
                return candidate.resolve()
    candidate = root / ".agent-registry"
    return candidate if (candidate / "config.json").is_file() else None


def find_registry(cwd: Path) -> Path | None:
    """Find the AgentNamer registry for cwd without changing it."""
    cwd = cwd.resolve()
    if main_root := _git_main_root(cwd):
        # A nested repository must never inherit an unrelated wrapper's
        # registry. AgentNamer keeps a linked-worktree registry at main_root.
        return _registry_from_root(main_root)
    for root in (cwd, *cwd.parents):
        if registry := _registry_from_root(root):
            return registry
    return None


def callsign_from_registry(session_id: str, cwd: Path) -> str:
    """Return the top-level callsign registered to ``session_id``."""
    if not session_id or not (registry := find_registry(cwd)):
        return ""
    matches: list[tuple[str, str]] = []
    for path in (registry / "ids").glob("*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if (
            isinstance(record, dict)
            and not record.get("sub")
            and record.get("session_id") == session_id
            and record.get("status") in _LIVE_STATUSES
            and record.get("name")
        ):
            matches.append((path.stem, str(record["name"])))
    return sorted(matches)[-1][1] if matches else ""


def detect_callsign(
    session_id: str,
    cwd: Path,
    transcript_path: Path | None = None,
    *,
    subagent: bool = False,
) -> str:
    """Resolve an optional callsign without making AgentNamer a dependency."""
    # A child's parent-assigned name is more precise than a process-level env
    # value that may describe the parent agent.
    if subagent and (value := callsign_from_transcript(transcript_path)):
        return value
    if value := os.environ.get("XEXPORT_CALLSIGN", "").strip():
        return value
    if value := callsign_from_registry(session_id, cwd):
        return value
    return ""
