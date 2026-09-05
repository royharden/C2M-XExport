"""Parse Claude Code session transcripts.

Store layout (observed 2026-07, Claude Code 2.1.x — format is internal to the app
and may drift; unknown entries degrade to RAW blocks, never crash):

    ~/.claude/projects/<encoded-cwd>/<session-id>.jsonl   transcript
    ~/.claude/projects/<encoded-cwd>/<session-id>/subagents/*.jsonl
    ~/.claude/projects/<encoded-cwd>/<session-id>/subagents/agent-<hex>.meta.json
    ~/.claude/sessions/<pid>.json                          live-session registry

Every line of a subagent transcript carries "isSidechain": true, so the sidechain
filter that keeps a parent transcript clean has to be lifted when the file being
parsed *is* the subagent's own transcript.

The chat title shown in the apps is written into the transcript as
{"type": "ai-title", "aiTitle": "..."} lines — the last one wins.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from ..model import (
    ASSISTANT_TEXT, RAW, THINKING, TOOL_CALL, TOOL_RESULT, USER_TEXT,
    Block, Message, Session,
)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)

# Metadata line types that carry no conversation content.
_SKIP_TYPES = {
    "ai-title", "summary",            # handled separately (titles)
    "queue-operation", "mode", "last-prompt", "progress",
    "file-history-snapshot", "attachment", "system", "diagnostics", "todo",
}


def claude_home() -> Path:
    override = os.environ.get("XEXPORT_CLAUDE_HOME")
    return Path(override) if override else Path.home() / ".claude"


def projects_dir() -> Path:
    return claude_home() / "projects"


def encode_project_dir(cwd: str | Path) -> str:
    """Claude encodes the cwd by replacing every non-alphanumeric char with '-'.

    C:\\Users\\Roy Harden\\X -> C--Users-Roy-Harden-X
    """
    return re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def is_subagent_path(path: Path) -> bool:
    return "subagents" in {p.lower() for p in path.parts}


def subagent_meta(path: Path) -> dict:
    """`agent-<id>.meta.json` sits beside the transcript.

    Observed shape: {"agentType": "general-purpose",
                     "description": "Summarize existing canonical skills",
                     "toolUseId": "toolu_..."}
    `description` is the one-line task the parent wrote - a far better title than
    anything derivable from the transcript body, whose first user message is the
    parent's full instructions.
    """
    meta_path = path.parent / f"{path.stem}.meta.json"
    try:
        obj = json.loads(meta_path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _subagent_title(path: Path) -> str:
    meta = subagent_meta(path)
    return (str(meta.get("description") or "").strip()
            or str(meta.get("agentType") or "").strip()
            or path.stem)


@dataclass
class SessionInfo:
    source: str
    session_id: str
    path: Path
    title: str
    mtime: float


def _pretty_json(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(value)


def _tool_result_text(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    parts.append(item.get("text", ""))
                elif item.get("type") == "image":
                    parts.append("[image]")
                else:
                    parts.append(_pretty_json(item))
            else:
                parts.append(str(item))
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return _pretty_json(content)


def _tool_detail(name: str, tool_input) -> str:
    if not isinstance(tool_input, dict):
        return ""
    return str(
        tool_input.get("description")
        or tool_input.get("file_path")
        or tool_input.get("pattern")
        or tool_input.get("url")
        or ""
    )


def parse_file(path: Path, *, include_sidechain: bool | None = None) -> Session:
    """Parse one transcript.

    `include_sidechain` defaults to "yes if this file IS a subagent transcript".
    A parent transcript still drops sidechain lines (they duplicate the subagent's
    own file); a subagent transcript keeps them, because that is all it contains.
    """
    if include_sidechain is None:
        include_sidechain = is_subagent_path(path)
    session = Session(source="claude", session_id=path.stem, path=path,
                      app="Claude Code")
    if include_sidechain:
        session.is_subagent = True
        session.app = "Claude Code subagent"
        #  .../<encoded-cwd>/<parent-session-id>/subagents/<file>.jsonl
        session.parent_session_id = path.parent.parent.name
    title = ""
    custom_title = ""
    summary_fallback = ""
    first_user_text = ""

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

            ltype = obj.get("type")
            if ltype == "ai-title":
                title = obj.get("aiTitle") or title
                continue
            if ltype == "custom-title":
                # A name the user set explicitly (via /rename or the app UI):
                # takes precedence over the generated ai-title.
                custom_title = obj.get("customTitle") or custom_title
                continue
            if ltype == "summary":
                summary_fallback = obj.get("summary") or summary_fallback
                continue
            if ltype in _SKIP_TYPES:
                continue

            if ltype not in ("user", "assistant"):
                # Unknown metadata line: keep, collapsed, so drift is visible
                session.messages.append(Message(
                    role="assistant",
                    blocks=[Block(kind=RAW, text=_pretty_json(obj),
                                  name=str(ltype))],
                    timestamp=obj.get("timestamp", ""),
                ))
                continue

            if (obj.get("isSidechain") and not include_sidechain) or obj.get("isMeta"):
                continue

            if not session.cwd and obj.get("cwd"):
                session.cwd = obj["cwd"]
            if not session.started and obj.get("timestamp"):
                session.started = obj["timestamp"]

            msg = obj.get("message") or {}
            content = msg.get("content")
            ts = obj.get("timestamp", "")

            if ltype == "assistant":
                if not session.model and msg.get("model"):
                    session.model = msg["model"]
                blocks: list[Block] = []
                for item in content if isinstance(content, list) else []:
                    if not isinstance(item, dict):
                        continue
                    itype = item.get("type")
                    if itype == "text":
                        if item.get("text"):
                            blocks.append(Block(kind=ASSISTANT_TEXT,
                                                text=item["text"]))
                    elif itype == "thinking":
                        if item.get("thinking"):
                            blocks.append(Block(kind=THINKING,
                                                text=item["thinking"]))
                    elif itype == "tool_use":
                        name = item.get("name", "tool")
                        tool_input = item.get("input")
                        blocks.append(Block(
                            kind=TOOL_CALL, name=name,
                            detail=_tool_detail(name, tool_input),
                            args=_pretty_json(tool_input),
                        ))
                    else:
                        blocks.append(Block(kind=RAW, text=_pretty_json(item),
                                            name=str(itype)))
                if blocks:
                    session.messages.append(
                        Message(role="assistant", blocks=blocks, timestamp=ts))
                continue

            # ltype == "user": real prompts and tool results both arrive here
            user_blocks: list[Block] = []
            tool_blocks: list[Block] = []
            if isinstance(content, str):
                if content.strip():
                    user_blocks.append(Block(kind=USER_TEXT, text=content))
            elif isinstance(content, list):
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    itype = item.get("type")
                    if itype == "text":
                        if item.get("text", "").strip():
                            user_blocks.append(Block(kind=USER_TEXT,
                                                     text=item["text"]))
                    elif itype == "tool_result":
                        tool_blocks.append(Block(
                            kind=TOOL_RESULT,
                            output=_tool_result_text(item.get("content")),
                            is_error=bool(item.get("is_error")),
                        ))
                    elif itype == "image":
                        user_blocks.append(Block(kind=USER_TEXT, text="[image]"))
                    else:
                        tool_blocks.append(Block(kind=RAW,
                                                 text=_pretty_json(item),
                                                 name=str(itype)))
            if tool_blocks:
                session.messages.append(
                    Message(role="tool", blocks=tool_blocks, timestamp=ts))
            if user_blocks:
                if not first_user_text:
                    first_user_text = user_blocks[0].text
                session.messages.append(
                    Message(role="user", blocks=user_blocks, timestamp=ts))

    user_fallback = ""
    if first_user_text.strip():
        user_fallback = first_user_text.strip().splitlines()[0][:80]
    meta_title = ""
    if include_sidechain:
        meta = subagent_meta(path)
        meta_title = (str(meta.get("description") or "").strip()
                      or str(meta.get("agentType") or "").strip())
    session.title = (custom_title or title or meta_title or summary_fallback
                     or user_fallback or session.session_id)
    return session


def _quick_title(path: Path, max_bytes: int = 4_000_000) -> str:
    """Title for listings: last ai-title line, else first user text."""
    title = ""
    custom = ""
    first_user = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if '"ai-title"' in line or '"custom-title"' in line:
                    try:
                        obj = json.loads(line)
                        if obj.get("type") == "ai-title" and obj.get("aiTitle"):
                            title = obj["aiTitle"]
                        elif (obj.get("type") == "custom-title"
                                and obj.get("customTitle")):
                            custom = obj["customTitle"]
                    except json.JSONDecodeError:
                        pass
                elif not first_user and '"type":"user"' in line.replace(" ", ""):
                    try:
                        obj = json.loads(line)
                        if (obj.get("type") == "user"
                                and not obj.get("isSidechain")
                                and not obj.get("isMeta")):
                            content = (obj.get("message") or {}).get("content")
                            if isinstance(content, str):
                                first_user = content
                            elif isinstance(content, list):
                                for item in content:
                                    if (isinstance(item, dict)
                                            and item.get("type") == "text"
                                            and item.get("text", "").strip()):
                                        first_user = item["text"]
                                        break
                    except json.JSONDecodeError:
                        pass
    except OSError:
        return path.stem
    result = custom or title or first_user.strip().replace("\n", " ")[:80]
    return result or path.stem


def list_sessions(limit: int = 15, *, subagents: bool = False) -> list[SessionInfo]:
    root = projects_dir()
    if not root.is_dir():
        return []
    files = [
        p for p in root.glob("*/*.jsonl")
        if _UUID_RE.match(p.stem)
    ]
    if subagents:
        files.extend(root.glob("*/*/subagents/agent-*.jsonl"))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    infos = []
    for p in files[:limit]:
        infos.append(SessionInfo(
            source="claude", session_id=p.stem, path=p,
            title=(_subagent_title(p) if is_subagent_path(p) else _quick_title(p)),
            mtime=p.stat().st_mtime,
        ))
    return infos


def list_subagents(parent_session_id: str) -> list[SessionInfo]:
    """Every subagent transcript belonging to one parent session, oldest first."""
    root = projects_dir()
    if not root.is_dir() or not parent_session_id:
        return []
    files = sorted(root.glob(f"*/{parent_session_id}/subagents/agent-*.jsonl"))
    if not files:
        # Tolerate a partial parent id, the way find_session does.
        files = sorted(
            p for p in root.glob("*/*/subagents/agent-*.jsonl")
            if parent_session_id.lower() in p.parent.parent.name.lower()
        )
    return [
        SessionInfo(source="claude", session_id=p.stem, path=p,
                    title=_subagent_title(p), mtime=p.stat().st_mtime)
        for p in files
    ]


def find_session(session_id: str) -> Path | None:
    root = projects_dir()
    if not root.is_dir():
        return None
    matches = list(root.glob(f"*/{session_id}.jsonl"))
    if not matches:
        matches = list(root.glob(f"*/*/subagents/{session_id}.jsonl"))
    if not matches:
        matches = [p for p in root.glob("*/*.jsonl") if session_id in p.stem]
    if not matches:
        matches = [p for p in root.glob("*/*/subagents/agent-*.jsonl")
                   if session_id in p.stem]
    return matches[0] if matches else None
