"""Render a Session to a single Markdown transcript.

Shape follows the codex-export style Roy liked (## 👤 / 🤖 headings), with
collapsible <details> for thinking and tool output so long sessions stay
skimmable on GitHub/Obsidian.
"""

from __future__ import annotations

import re
from datetime import datetime

from .. import __version__
from ..model import (
    ASSISTANT_TEXT, RAW, THINKING, TOOL_CALL, TOOL_RESULT, USER_TEXT,
    Session,
)

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


def render_markdown(
    session: Session,
    *,
    brief: bool = False,
    include_tools: bool = True,
    include_thinking: bool = True,
    truncate: int = 2000,
) -> str:
    if brief:
        include_tools = False
        include_thinking = False

    label = session.assistant_label
    lines: list[str] = [f"# {session.title}", ""]
    meta = [f"- **Source:** {session.app or session.source}"]
    if session.model:
        meta[0] += f" ({session.model})"
    meta.append(f"- **Session:** `{session.session_id}`")
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

    for message in session.messages:
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
