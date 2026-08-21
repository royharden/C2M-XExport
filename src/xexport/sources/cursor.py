"""Parse Cursor Agent chat transcripts.

Store layout (observed 2026-08 — format is internal to the app and may drift;
unknown content types degrade to RAW blocks, never crash):

    ~/.cursor/projects/<encoded-cwd>/agent-transcripts/<uuid>/<uuid>.jsonl
    ~/.cursor/projects/<encoded-cwd>/agent-transcripts/<uuid>/subagents/*.jsonl

The active conversation id is exposed to agent shells as ``CURSOR_CONVERSATION_ID``.
User turns are commonly wrapped as ``<user_query>...</user_query>`` (sometimes with
a preceding ``<timestamp>...</timestamp>``).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from ..model import (
    ASSISTANT_TEXT, RAW, THINKING, TOOL_CALL, TOOL_RESULT, USER_TEXT,
    Block, Message, Session,
)
from .claude import SessionInfo, _pretty_json

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_USER_QUERY_RE = re.compile(
    r"<user_query>\s*(.*?)\s*</user_query>", re.DOTALL | re.IGNORECASE,
)
_TIMESTAMP_RE = re.compile(
    r"<timestamp>\s*.*?\s*</timestamp>\s*", re.DOTALL | re.IGNORECASE,
)


def cursor_home() -> Path:
    override = os.environ.get("XEXPORT_CURSOR_HOME")
    return Path(override) if override else Path.home() / ".cursor"


def projects_dir() -> Path:
    return cursor_home() / "projects"


def encode_project_dir(cwd: str | Path) -> str:
    """Cursor encodes the cwd as a single slug.

    ``C:\\Users\\Roy Harden\\OneDrive\\PJ-OD\\skills``
    → ``c-Users-Roy-Harden-OneDrive-PJ-OD-skills``
    """
    s = str(cwd).replace(":", "").replace("\\", "-").replace("/", "-")
    s = s.replace(" ", "-")
    while "--" in s:
        s = s.replace("--", "-")
    if s and s[0].isalpha():
        s = s[0].lower() + s[1:]
    return s


def _tool_detail(name: str, tool_input) -> str:
    if not isinstance(tool_input, dict):
        return ""
    return str(
        tool_input.get("description")
        or tool_input.get("path")
        or tool_input.get("file_path")
        or tool_input.get("command")
        or tool_input.get("pattern")
        or tool_input.get("url")
        or ""
    )[:120]


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                else:
                    parts.append(_pretty_json(item))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return _pretty_json(content)


def clean_user_text(text: str) -> str:
    """Strip Cursor harness wrappers; keep the human prompt."""
    if not text:
        return ""
    cleaned = _TIMESTAMP_RE.sub("", text).strip()
    match = _USER_QUERY_RE.search(cleaned)
    if match:
        return match.group(1).strip()
    return cleaned


def _title_from_prompt(text: str) -> str:
    """Short title from the first user prompt (word-boundary, no dangling '(')."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    first = re.sub(r"\s+", " ", first).strip()
    if not first:
        return ""
    # Prefer ~8 words for Cursor citation-style titles.
    words = first.split(" ")
    if len(words) > 8:
        first = " ".join(words[:8])
    if len(first) > 60:
        cut = first[:60]
        if " " in cut:
            cut = cut.rsplit(" ", 1)[0]
        first = cut
    return first.rstrip(" .,:;([{")


def parse_file(path: Path) -> Session:
    session = Session(
        source="cursor",
        session_id=path.stem,
        path=path,
        app="Cursor",
    )
    first_user_text = ""

    # Infer cwd from the projects/<encoded>/agent-transcripts/... layout when possible.
    try:
        # .../projects/<encoded>/agent-transcripts/<id>/<id>.jsonl
        encoded = path.parent.parent.parent.name
        if encoded and encoded != "projects":
            session.cwd = encoded
    except IndexError:
        pass

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                session.messages.append(Message(
                    role="assistant",
                    blocks=[Block(kind=RAW, text=line[:2000], name="unparseable line")],
                ))
                continue
            if not isinstance(obj, dict):
                continue

            role = obj.get("role")
            msg = obj.get("message") or {}
            content = msg.get("content")
            ts = obj.get("timestamp", "") or msg.get("timestamp", "")

            if role == "assistant":
                if not session.model and msg.get("model"):
                    session.model = msg["model"]
                blocks: list[Block] = []
                items = content if isinstance(content, list) else (
                    [{"type": "text", "text": content}] if isinstance(content, str) else []
                )
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    itype = item.get("type")
                    if itype == "text":
                        text = item.get("text") or ""
                        if text.strip():
                            blocks.append(Block(kind=ASSISTANT_TEXT, text=text))
                    elif itype == "thinking":
                        thinking = item.get("thinking") or item.get("text") or ""
                        if thinking.strip():
                            blocks.append(Block(kind=THINKING, text=thinking))
                    elif itype == "tool_use":
                        name = item.get("name", "tool")
                        tool_input = item.get("input")
                        blocks.append(Block(
                            kind=TOOL_CALL, name=name,
                            detail=_tool_detail(name, tool_input),
                            args=_pretty_json(tool_input),
                        ))
                    elif itype == "tool_result":
                        blocks.append(Block(
                            kind=TOOL_RESULT,
                            output=_tool_result_text(item.get("content")),
                            is_error=bool(item.get("is_error")),
                        ))
                    else:
                        blocks.append(Block(
                            kind=RAW, text=_pretty_json(item), name=str(itype),
                        ))
                if blocks:
                    # Split tool_result-only chunks as role "tool" for IR consistency.
                    tool_only = all(b.kind == TOOL_RESULT for b in blocks)
                    session.messages.append(Message(
                        role="tool" if tool_only else "assistant",
                        blocks=blocks,
                        timestamp=ts,
                    ))
                continue

            if role == "user":
                user_blocks: list[Block] = []
                tool_blocks: list[Block] = []
                if isinstance(content, str):
                    cleaned = clean_user_text(content)
                    if cleaned:
                        user_blocks.append(Block(kind=USER_TEXT, text=cleaned))
                elif isinstance(content, list):
                    for item in content:
                        if not isinstance(item, dict):
                            continue
                        itype = item.get("type")
                        if itype == "text":
                            cleaned = clean_user_text(item.get("text") or "")
                            if cleaned:
                                user_blocks.append(Block(kind=USER_TEXT, text=cleaned))
                        elif itype == "tool_result":
                            tool_blocks.append(Block(
                                kind=TOOL_RESULT,
                                output=_tool_result_text(item.get("content")),
                                is_error=bool(item.get("is_error")),
                            ))
                        else:
                            tool_blocks.append(Block(
                                kind=RAW, text=_pretty_json(item), name=str(itype),
                            ))
                if tool_blocks:
                    session.messages.append(
                        Message(role="tool", blocks=tool_blocks, timestamp=ts))
                if user_blocks:
                    if not first_user_text:
                        first_user_text = user_blocks[0].text
                    if not session.started and ts:
                        session.started = ts
                    session.messages.append(
                        Message(role="user", blocks=user_blocks, timestamp=ts))
                continue

            # Unknown top-level shape
            session.messages.append(Message(
                role="assistant",
                blocks=[Block(kind=RAW, text=_pretty_json(obj),
                              name=str(role or "unknown"))],
                timestamp=ts,
            ))

    session.title = _title_from_prompt(first_user_text) or session.session_id
    return session


def _quick_title(path: Path) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"role"' not in line or '"user"' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("role") != "user":
                    continue
                content = (obj.get("message") or {}).get("content")
                text = ""
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            text = item.get("text") or ""
                            if text.strip():
                                break
                cleaned = clean_user_text(text)
                if cleaned:
                    return _title_from_prompt(cleaned)
    except OSError:
        pass
    return path.stem


def _iter_transcript_files() -> list[Path]:
    root = projects_dir()
    if not root.is_dir():
        return []
    files: list[Path] = []
    for p in root.glob("*/agent-transcripts/*/*.jsonl"):
        # Skip nested subagent transcripts
        if "subagents" in p.parts:
            continue
        if _UUID_RE.match(p.stem) and p.parent.name.lower() == p.stem.lower():
            files.append(p)
    return files


def list_sessions(limit: int = 15) -> list[SessionInfo]:
    files = _iter_transcript_files()
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    infos: list[SessionInfo] = []
    for p in files[:limit]:
        infos.append(SessionInfo(
            source="cursor", session_id=p.stem, path=p,
            title=_quick_title(p), mtime=p.stat().st_mtime,
        ))
    return infos


def find_session(session_id: str) -> Path | None:
    root = projects_dir()
    if not root.is_dir():
        return None
    sid = session_id.strip()
    exact = list(root.glob(f"*/agent-transcripts/{sid}/{sid}.jsonl"))
    if exact:
        return exact[0]
    # Partial id match (first unique hit by mtime)
    matches = [
        p for p in _iter_transcript_files()
        if sid.lower() in p.stem.lower()
    ]
    if not matches:
        return None
    matches.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return matches[0]
