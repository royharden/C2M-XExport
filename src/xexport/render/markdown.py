"""Render a Session to a single Markdown transcript.

Shape follows the codex-export style Roy liked (## 👤 / 🤖 headings), with
collapsible <details> for thinking and tool output so long sessions stay
skimmable on GitHub/Obsidian.

0.2.0: the body can start part-way through a session (`start_index`) and the file
header can be omitted, so a later run appends only its new turns to an export that
already exists.
"""

from __future__ import annotations

import re
from datetime import datetime

from .. import __version__
from ..model import (
    ASSISTANT_TEXT, RAW, THINKING, TOOL_CALL, TOOL_RESULT, USER_TEXT,
    Session,
)
from . import filtered

_TICKS = re.compile(r"`+")


def _fence(text: str, lang: str = "") -> str:
    """Fence `text`, using more backticks than any run inside it."""
    longest = max((len(m.group()) for m in _TICKS.finditer(text)), default=0)
    fence = "`" * max(3, longest + 1)
    return f"{fence}{lang}\n{text}\n{fence}"


def _truncate(text: str, limit: int) -> str:
    if limit <= 0 or len(text) <= limit:
        return text
    return f"{text[:limit]}\n… (+{len(text) - limit:,} chars truncated — use --full for everything)"


def _header_lines(session: Session) -> list[str]:
    lines = [f"# {session.title}", ""]
    meta = [f"- **Source:** {session.app or session.source}"]
    if session.model:
        meta[0] += f" ({session.model})"
    meta.append(f"- **Session:** `{session.session_id}`")
    if session.is_subagent and session.parent_session_id:
        meta.append(f"- **Subagent of:** `{session.parent_session_id}`")
    if session.cwd:
        meta.append(f"- **Workspace:** `{session.cwd}`")
    if session.started:
        meta.append(f"- **Started:** {session.started}")
    meta.append(
        f"- **Exported:** {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M %Z')}"
        f" by xexport v{__version__}"
    )
    lines.extend(meta)
    lines.extend(["", "---", ""])
    return lines


def describe_delta(session: Session, start_index: int, end_index: int) -> str:
    """Human summary of an appended range.

    A delta often contains no new *prompt* at all — a long turn of tool work appends
    plenty of content without the user having said anything. "0 new prompts" is both
    wrong-sounding and uninformative, so the unit switches to messages in that case.
    """
    prompts = sum(
        1 for m in session.messages[start_index:end_index] if session.is_prompt(m)
    )
    span = f"messages {start_index + 1}–{end_index}"
    if prompts:
        return f"{prompts} new prompt{'' if prompts == 1 else 's'} ({span})"
    count = end_index - start_index
    return f"{count} new message{'' if count == 1 else 's'} ({start_index + 1}–{end_index})"


def addendum_header(
    session: Session,
    *,
    run: int,
    start_index: int,
    end_index: int,
    previous_title: str = "",
) -> str:
    """The seam between an existing export and the turns appended after it.

    An h2 so it lands in a table of contents alongside the ## 👤 User headings. The
    title sentence appears only when the chat was actually renamed since the last run,
    which is how a retitle gets recorded without renaming the file (and without
    rewriting the header block at the top).
    """
    when = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    note = f"*{describe_delta(session, start_index, end_index)}.*"
    if previous_title and previous_title != session.title:
        note = note[:-1] + f" Chat title is now \"{session.title}\".*"
    return "\n".join([
        "", "---", "",
        f"## ➕ Addendum {run} · {when}",
        "", note, "",
        "---", "",
    ])


def render_markdown(
    session: Session,
    *,
    brief: bool = False,
    include_tools: bool = True,
    include_thinking: bool = True,
    truncate: int = 2000,
    start_index: int = 0,
    header: bool = True,
) -> str:
    # One predicate for both renderers - see render/__init__.filtered.
    session = filtered(session, brief=brief, include_tools=include_tools,
                       include_thinking=include_thinking)
    include_tools = include_tools and not brief
    include_thinking = include_thinking and not brief

    label = session.assistant_label
    lines: list[str] = _header_lines(session) if header else []

    for message in session.messages[start_index:]:
        for block in message.blocks:
            if block.kind == USER_TEXT:
                lines.extend([f"## 👤 User", "", block.text, ""])
            elif block.kind == ASSISTANT_TEXT:
                lines.extend([f"## 🤖 {label}", "", block.text, ""])
            elif block.kind == THINKING and include_thinking:
                lines.extend([
                    "<details><summary>💭 Thinking</summary>", "",
                    _truncate(block.text, truncate), "",
                    "</details>", "",
                ])
            elif block.kind == TOOL_CALL and include_tools:
                heading = f"### 🔧 {block.name}"
                if block.detail:
                    heading += f" — {block.detail}"
                lines.extend([heading, ""])
                if block.args.strip():
                    lines.extend([_fence(_truncate(block.args, truncate), "json"), ""])
            elif block.kind == TOOL_RESULT and include_tools:
                flag = "❌ Output (error)" if block.is_error else "📤 Output"
                body = block.output.strip() or "(empty)"
                lines.extend([
                    f"<details><summary>{flag} ({len(block.output):,} chars)</summary>", "",
                    _fence(_truncate(body, truncate)), "",
                    "</details>", "",
                ])
            elif block.kind == RAW and include_tools:
                lines.extend([
                    f"<details><summary>⚙️ {block.name or 'entry'} (raw)</summary>", "",
                    _fence(_truncate(block.text, truncate), "json"), "",
                    "</details>", "",
                ])
    return "\n".join(lines).rstrip() + "\n"
