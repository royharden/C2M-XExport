"""Parse Codex (Codex CLI / ChatGPT-Codex desktop) rollout transcripts.

Store layout (observed 2026-07, codex-cli 0.129+ — format is internal to the app
and may drift; unknown entries degrade to RAW blocks, never crash):

    ~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<uuid>.jsonl   live sessions
    ~/.codex/archived_sessions/**                             archived sessions
    ~/.codex/session_index.jsonl    {"id", "thread_name", "updated_at"} per session
    ~/.codex/state_5.sqlite         threads table (id, title, source, created_at)

Only ``response_item`` entries carry conversation content; ``event_msg`` entries
duplicate them as a UI event stream and are skipped.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from pathlib import Path

from ..model import (
    ASSISTANT_TEXT, RAW, THINKING, TOOL_CALL, TOOL_RESULT, USER_TEXT,
    Block, Message, Session,
)
from .claude import SessionInfo, _pretty_json  # shared listing record + helper

_UUID_RE = re.compile(
    r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\.jsonl$"
)

# System-injected user blocks that are noise in a transcript.
_SKIP_USER_PREFIXES = (
    "<permissions instructions>",
    "# AGENTS.md instructions",
    "<environment_context>",
    "<recommended_plugins>",
    "<user_instructions>",
    "<turn_context",
    "<ide_context",
    "<app_context",
    "<system",
    "<collaboration",
    "<current_datetime",
)

# Top-level entry types that never carry transcript content.
# world_state entries are environment/AGENTS.md snapshots — large and noisy.
_SKIP_ENTRY_TYPES = {"event_msg", "turn_context", "compacted", "world_state"}


def codex_home() -> Path:
    override = os.environ.get("XEXPORT_CODEX_HOME")
    return Path(override) if override else Path.home() / ".codex"


def _session_dirs() -> list[Path]:
    home = codex_home()
    return [home / "sessions", home / "archived_sessions"]


def _text_from_content(content) -> str:
    if not isinstance(content, list):
        return ""
    parts = []
    for c in content:
        if not isinstance(c, dict):
            continue
        if c.get("type") in ("input_text", "output_text", "text"):
            text = c.get("text", "").strip()
            if text:
                parts.append(text)
    return "\n\n".join(parts)


def _is_system_block(text: str) -> bool:
    return text.startswith(_SKIP_USER_PREFIXES)


def _format_output(output) -> str:
    """Tool outputs are often JSON strings, sometimes {"output": "..."} wrappers."""
    if not isinstance(output, str):
        return _pretty_json(output)
    try:
        parsed = json.loads(output)
    except (json.JSONDecodeError, ValueError):
        return output
    if isinstance(parsed, dict) and isinstance(parsed.get("output"), str):
        return parsed["output"]
    return _pretty_json(parsed)


def parse_file(path: Path) -> Session:
    session = Session(source="codex", session_id="", path=path, app="Codex")
    first_user_meta = ""

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

            etype = obj.get("type")
            ts = obj.get("timestamp", "")
            payload = obj.get("payload") or {}

            if etype == "session_meta":
                session.session_id = payload.get("session_id") or payload.get("id", "")
                session.cwd = payload.get("cwd", "")
                session.started = payload.get("timestamp", "")
                session.app = payload.get("originator") or "Codex"
                first_user_meta = payload.get("first_user_message", "") or ""
                continue

            if etype == "turn_context":
                if not session.model and payload.get("model"):
                    session.model = str(payload["model"])
                continue

            if etype in _SKIP_ENTRY_TYPES:
                continue

            if etype != "response_item":
                session.messages.append(Message(
                    role="assistant",
                    blocks=[Block(kind=RAW, text=_pretty_json(obj), name=str(etype))],
                    timestamp=ts,
                ))
                continue

            ptype = payload.get("type")
            role = payload.get("role")

            if ptype == "message":
                text = _text_from_content(payload.get("content"))
                if not text:
                    continue
                if role == "user":
                    if _is_system_block(text):
                        continue
                    session.messages.append(Message(
                        role="user",
                        blocks=[Block(kind=USER_TEXT, text=text)],
                        timestamp=ts,
                    ))
                elif role == "assistant":
                    session.messages.append(Message(
                        role="assistant",
                        blocks=[Block(kind=ASSISTANT_TEXT, text=text)],
                        timestamp=ts,
                    ))
                continue

            if ptype == "reasoning":
                parts = []
                for item in payload.get("summary") or []:
                    if isinstance(item, dict) and item.get("text"):
                        parts.append(item["text"])
                for item in payload.get("content") or []:
                    if isinstance(item, dict) and item.get("text"):
                        parts.append(item["text"])
                text = "\n\n".join(parts).strip()
                if text:
                    session.messages.append(Message(
                        role="assistant",
                        blocks=[Block(kind=THINKING, text=text)],
                        timestamp=ts,
                    ))
                continue

            if ptype in ("function_call", "custom_tool_call"):
                name = payload.get("name", "tool")
                raw_args = payload.get("arguments", payload.get("input", ""))
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except (json.JSONDecodeError, ValueError):
                        pass
                session.messages.append(Message(
                    role="assistant",
                    blocks=[Block(kind=TOOL_CALL, name=name,
                                  args=_pretty_json(raw_args)
                                  if not isinstance(raw_args, str) else raw_args)],
                    timestamp=ts,
                ))
                continue

            if ptype == "local_shell_call":
                action = payload.get("action") or {}
                command = action.get("command")
                args = " ".join(command) if isinstance(command, list) else _pretty_json(action)
                session.messages.append(Message(
                    role="assistant",
                    blocks=[Block(kind=TOOL_CALL, name="shell", args=args)],
                    timestamp=ts,
                ))
                continue

            if ptype in ("function_call_output", "custom_tool_call_output"):
                session.messages.append(Message(
                    role="tool",
                    blocks=[Block(kind=TOOL_RESULT,
                                  output=_format_output(payload.get("output", "")))],
                    timestamp=ts,
                ))
                continue

            if ptype == "web_search_call":
                action = payload.get("action") or {}
                session.messages.append(Message(
                    role="assistant",
                    blocks=[Block(kind=TOOL_CALL, name="web_search",
                                  args=_pretty_json(action))],
                    timestamp=ts,
                ))
                continue

            # Unknown response_item payload: keep, collapsed
            session.messages.append(Message(
                role="assistant",
                blocks=[Block(kind=RAW, text=_pretty_json(payload), name=str(ptype))],
                timestamp=ts,
            ))

    if not session.session_id:
        m = _UUID_RE.search(path.name)
        session.session_id = m.group(1) if m else path.stem

    meta_fallback = ""
    if first_user_meta.strip():
        meta_fallback = first_user_meta.strip().splitlines()[0][:80]
    session.title = (lookup_title(session.session_id) or meta_fallback
                     or _first_user_text(session) or session.session_id)
    return session


def _first_user_text(session: Session) -> str:
    for m in session.messages:
        if m.role == "user":
            for b in m.blocks:
                if b.kind == USER_TEXT and b.text.strip():
                    return b.text.strip().splitlines()[0][:80]
    return ""


def _index_titles() -> dict[str, str]:
    titles: dict[str, str] = {}
    idx = codex_home() / "session_index.jsonl"
    if not idx.is_file():
        return titles
    try:
        with open(idx, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("id") and obj.get("thread_name"):
                    titles[obj["id"]] = obj["thread_name"]
    except OSError:
        pass
    return titles


def lookup_title(session_id: str) -> str:
    if not session_id:
        return ""
    title = _index_titles().get(session_id, "")
    if title:
        return title
    db = codex_home() / "state_5.sqlite"
    if db.is_file():
        try:
            con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
            try:
                row = con.execute(
                    "SELECT title FROM threads WHERE id = ?", (session_id,)
                ).fetchone()
            finally:
                con.close()
            if row and row[0]:
                return str(row[0])
        except sqlite3.Error:
            pass
    return ""


def list_sessions(limit: int = 15) -> list[SessionInfo]:
    files: list[Path] = []
    for root in _session_dirs():
        if root.is_dir():
            files.extend(root.rglob("rollout-*.jsonl"))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    titles = _index_titles()
    infos = []
    for p in files[:limit]:
        m = _UUID_RE.search(p.name)
        sid = m.group(1) if m else p.stem
        infos.append(SessionInfo(
            source="codex", session_id=sid, path=p,
            title=titles.get(sid, "") or sid, mtime=p.stat().st_mtime,
        ))
    return infos


def find_session(session_id: str) -> Path | None:
    for root in _session_dirs():
        if root.is_dir():
            matches = list(root.rglob(f"*{session_id}*.jsonl"))
            if matches:
                return matches[0]
    return None
