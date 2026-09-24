"""Parse Grok CLI / Grok Build session transcripts.

Store layout (observed 2026-09-17, Grok CLI 1.0.34 — internal; unknown
entries degrade to RAW blocks):

    ~/.grok/sessions/<url-encoded-cwd>/<session-id>/chat_history.jsonl
    ~/.grok/sessions/<url-encoded-cwd>/<session-id>/summary.json

Grok children have their own GROK_SESSION_ID and sit as sibling session
folders, not under a subagents/ directory. Parent-driven child export uses
the AgentNamer registry (parent_id -> child session_id) when present.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote, unquote

from .. import agentnamer
from ..model import (
    ASSISTANT_TEXT,
    RAW,
    THINKING,
    TOOL_CALL,
    TOOL_RESULT,
    USER_TEXT,
    Block,
    Message,
    Session,
)


def grok_home() -> Path:
    override = os.environ.get("XEXPORT_GROK_HOME")
    return Path(override) if override else Path.home() / ".grok"


def sessions_dir() -> Path:
    return grok_home() / "sessions"


def encode_project_dir(cwd: str | Path) -> str:
    return quote(str(cwd), safe="")


def decode_project_dir(encoded: str) -> str:
    return unquote(encoded)


def is_subagent_path(path: Path) -> bool:
    return False


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
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, dict) and item.get("type") == "image":
                parts.append("[image]")
            else:
                parts.append(_pretty_json(item) if isinstance(item, dict) else str(item))
        return "\n".join(p for p in parts if p)
    if content is None:
        return ""
    return _pretty_json(content)


def _tool_detail(name: str, tool_input) -> str:
    if isinstance(tool_input, str):
        try:
            tool_input = json.loads(tool_input)
        except (json.JSONDecodeError, TypeError, ValueError):
            return tool_input[:120]
    if not isinstance(tool_input, dict):
        return ""
    return str(
        tool_input.get("description")
        or tool_input.get("file_path")
        or tool_input.get("target_file")
        or tool_input.get("pattern")
        or tool_input.get("command")
        or tool_input.get("url")
        or ""
    )


def _summary_title(folder: Path) -> str:
    path = folder / "summary.json"
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, ValueError):
        return ""
    if not isinstance(obj, dict):
        return ""
    return str(
        obj.get("generated_title")
        or obj.get("session_summary")
        or ""
    ).strip()


def _reasoning_text(obj: dict) -> str:
    summary = obj.get("summary")
    if isinstance(summary, list):
        parts = []
        for item in summary:
            if isinstance(item, dict) and item.get("text"):
                parts.append(str(item["text"]))
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts).strip()
    if isinstance(summary, str):
        return summary.strip()
    return ""


def parse_file(path: Path, *, include_sidechain: bool | None = None) -> Session:
    del include_sidechain
    folder = path.parent if path.name == "chat_history.jsonl" else path.parent
    session_id = folder.name
    session = Session(
        source="grok",
        session_id=session_id,
        path=path,
        app="Grok CLI",
        cwd=decode_project_dir(folder.parent.name) if folder.parent.name else "",
        title=_summary_title(folder),
    )
    first_user_text = ""

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                session.messages.append(
                    Message(
                        role="assistant",
                        blocks=[Block(kind=RAW, text=line[:2000], name="unparseable line")],
                    )
                )
                continue
            if not isinstance(obj, dict):
                continue
            ltype = obj.get("type")
            if ltype == "system":
                continue
            if ltype == "reasoning":
                text = _reasoning_text(obj)
                if text:
                    session.messages.append(
                        Message(
                            role="assistant",
                            blocks=[Block(kind=THINKING, text=text)],
                        )
                    )
                continue
            if ltype == "assistant":
                if not session.model:
                    session.model = str(obj.get("model_id") or obj.get("model") or "")
                blocks: list[Block] = []
                content = obj.get("content")
                if isinstance(content, str) and content.strip():
                    blocks.append(Block(kind=ASSISTANT_TEXT, text=content))
                elif isinstance(content, list):
                    for item in content:
                        if not isinstance(item, dict):
                            continue
                        itype = item.get("type")
                        if itype == "text" and item.get("text"):
                            blocks.append(Block(kind=ASSISTANT_TEXT, text=item["text"]))
                        elif itype == "thinking" and item.get("thinking"):
                            blocks.append(Block(kind=THINKING, text=item["thinking"]))
                        else:
                            blocks.append(Block(kind=RAW, text=_pretty_json(item), name=str(itype)))
                for call in obj.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    name = call.get("name", "tool")
                    args = call.get("arguments")
                    parsed = args
                    if isinstance(args, str):
                        try:
                            parsed = json.loads(args)
                        except (json.JSONDecodeError, ValueError):
                            parsed = args
                    blocks.append(
                        Block(
                            kind=TOOL_CALL,
                            name=name,
                            detail=_tool_detail(name, parsed),
                            args=_pretty_json(parsed) if not isinstance(parsed, str) else parsed,
                        )
                    )
                if blocks:
                    session.messages.append(Message(role="assistant", blocks=blocks))
                continue
            if ltype == "tool_result":
                session.messages.append(
                    Message(
                        role="tool",
                        blocks=[
                            Block(
                                kind=TOOL_RESULT,
                                output=_tool_result_text(obj.get("content")),
                                is_error=bool(obj.get("is_error")),
                            )
                        ],
                    )
                )
                continue
            if ltype == "user":
                user_blocks: list[Block] = []
                content = obj.get("content")
                if isinstance(content, str) and content.strip():
                    user_blocks.append(Block(kind=USER_TEXT, text=content))
                elif isinstance(content, list):
                    for item in content:
                        if (isinstance(item, dict) and item.get("type") == "text"
                                and isinstance(item.get("text"), str) and item["text"].strip()):
                            user_blocks.append(Block(kind=USER_TEXT, text=item["text"]))
                        elif isinstance(item, dict) and item.get("type") == "image":
                            user_blocks.append(Block(kind=USER_TEXT, text="[image]"))
                if user_blocks:
                    if not first_user_text:
                        first_user_text = user_blocks[0].text
                    session.messages.append(Message(role="user", blocks=user_blocks))
                continue
            session.messages.append(
                Message(
                    role="assistant",
                    blocks=[Block(kind=RAW, text=_pretty_json(obj), name=str(ltype))],
                )
            )

    if not session.title:
        text = agentnamer.strip_assignment_preamble(first_user_text) if first_user_text else ""
        session.title = (text.strip().splitlines()[0][:80] if text.strip() else session.session_id)
    return session


def find_session(session_id: str) -> Path | None:
    root = sessions_dir()
    if not root.is_dir() or not session_id:
        return None
    matches = list(root.glob(f"*/{session_id}/chat_history.jsonl"))
    if not matches:
        matches = [
            p for p in root.glob("*/*/chat_history.jsonl")
            if session_id.lower() in p.parent.name.lower()
        ]
    return matches[0] if matches else None


def list_sessions(limit: int = 15, *, subagents: bool = False) -> list:
    from .claude import SessionInfo

    del subagents
    root = sessions_dir()
    if not root.is_dir():
        return []
    files = sorted(
        root.glob("*/*/chat_history.jsonl"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    infos = []
    for p in files[:limit]:
        infos.append(
            SessionInfo(
                source="grok",
                session_id=p.parent.name,
                path=p,
                title=_summary_title(p.parent) or p.parent.name,
                mtime=p.stat().st_mtime,
            )
        )
    return infos


def list_subagents(parent_session_id: str) -> list:
    """Children of a Grok session, via AgentNamer parent_id when a registry exists."""
    from .claude import SessionInfo

    if not parent_session_id:
        return []
    parent_rec = None
    children = []
    registry = agentnamer.find_registry(Path.cwd())
    ids_dir = (registry / "ids") if registry else None
    if ids_dir and ids_dir.is_dir():
        for path in ids_dir.glob("*.json"):
            try:
                rec = json.loads(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("session_id") == parent_session_id and not rec.get("sub"):
                parent_rec = rec
            if rec.get("sub") and rec.get("session_id"):
                children.append(rec)
    if not parent_rec:
        return []
    parent_id = parent_rec.get("id")
    infos = []
    for rec in children:
        if rec.get("parent_id") != parent_id:
            continue
        path = find_session(str(rec.get("session_id")))
        if path is None:
            continue
        infos.append(
            SessionInfo(
                source="grok",
                session_id=path.parent.name,
                path=path,
                title=_summary_title(path.parent) or path.parent.name,
                mtime=path.stat().st_mtime,
            )
        )
    infos.sort(key=lambda i: i.mtime)
    return infos
