"""Render a Session to paginated HTML (index.html + page-NNN.html).

Layout and styling adapted from claude-code-transcripts by Simon Willison
(Apache-2.0) — see NOTICE. Index page: one card per user prompt with tool-use
stats and the closing long answer; detail pages: 5 prompts each, full content
with client-side expand/collapse.
"""

from __future__ import annotations

import copy
from collections import Counter
from datetime import datetime
from pathlib import Path

import mistune
from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup

from .. import __version__
from ..publish import atomic_write_text
from ..model import ASSISTANT_TEXT, TOOL_CALL, USER_TEXT, Message, Session
from . import filtered

PROMPTS_PER_PAGE = 5
LONG_TEXT_MIN_CHARS = 400  # closing answers longer than this get an index preview

_markdown = mistune.create_markdown(
    escape=True, plugins=["table", "strikethrough", "url"]
)


def _md(text: str) -> Markup:
    return Markup(_markdown(text or ""))


def _tool_icon(name: str) -> str:
    n = (name or "").lower()
    if "bash" in n or "shell" in n or "powershell" in n:
        return "$"
    if "read" in n:
        return "📖"
    if "write" in n or "edit" in n:
        return "✏️"
    if "grep" in n or "glob" in n or "search" in n or "find" in n:
        return "🔍"
    if "web" in n or "fetch" in n or "browser" in n or "navigate" in n:
        return "🌐"
    if "task" in n or "agent" in n:
        return "🤖"
    if "todo" in n:
        return "☑️"
    return "🔧"


def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("xexport.render", "templates"),
        autoescape=select_autoescape(("html",)),
    )
    env.filters["md"] = _md
    env.globals["tool_icon"] = _tool_icon
    return env


def _role_view(m: Message, session: Session, anchor: int) -> dict:
    css = {"user": "user", "assistant": "assistant", "tool": "tool-reply"}[m.role]
    label = {"user": "User", "assistant": session.assistant_label,
             "tool": "Tool reply"}[m.role]
    return {"css": css, "label": label, "anchor": anchor,
            "ts": m.timestamp, "blocks": m.blocks}


def _group_stats_line(group: list[Message]) -> str:
    counts = Counter(
        (b.name or "tool").lower()
        for m in group for b in m.blocks if b.kind == TOOL_CALL
    )
    return " · ".join(f"{n} {name}" for name, n in counts.most_common())


def _group_prompt(group: list[Message]) -> str:
    for m in group:
        if m.role == "user":
            for b in m.blocks:
                if b.kind == USER_TEXT and b.text.strip():
                    return b.text
    return "(no prompt)"


def _group_long_text(group: list[Message]) -> str:
    for m in reversed(group):
        if m.role == "assistant":
            for b in reversed(m.blocks):
                if b.kind == ASSISTANT_TEXT and len(b.text) >= LONG_TEXT_MIN_CHARS:
                    return b.text
    return ""


def render_html(session: Session, out_dir: Path, *, brief: bool = False,
                include_tools: bool = True, include_thinking: bool = True) -> Path:
    """Write index.html + page-NNN.html into out_dir; return the index path.

    The content filters are applied here rather than by the caller so that
    prompt grouping, the index counters and `session.stats()` all describe the
    document that was actually written.
    """
    session = filtered(session, brief=brief, include_tools=include_tools,
                       include_thinking=include_thinking)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = _env()
    exported = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    common = {"session": session, "version": __version__, "exported": exported}

    groups = session.prompt_groups()
    pages = [groups[i:i + PROMPTS_PER_PAGE]
             for i in range(0, len(groups), PROMPTS_PER_PAGE)] or [[]]
    total_pages = len(pages)

    # Assign global anchors and build per-page message views + index items
    anchor = 0
    index_items = []
    page_views: list[list[dict]] = []
    prompt_number = 0
    for page_no, page_groups in enumerate(pages, start=1):
        views: list[dict] = []
        for group in page_groups:
            first_anchor = None
            for m in group:
                # A message stripped bare by a filter would render as an empty
                # card; skip drawing it, but still spend its anchor so numbering
                # matches the unfiltered transcript.
                if m.blocks:
                    if first_anchor is None:
                        # The index must link to a message that was actually
                        # drawn. Anchoring on the group's first message linked to
                        # nothing whenever a filter emptied it -- reachable for
                        # group 1, which absorbs any metadata before the first
                        # prompt, and --no-tools empties exactly that.
                        first_anchor = anchor
                    views.append(_role_view(m, session, anchor))
                anchor += 1
            if first_anchor is None:
                first_anchor = anchor - len(group)
            prompt_number += 1
            ts = next((m.timestamp for m in group if m.timestamp), "")
            index_items.append({
                "number": prompt_number,
                "href": f"page-{page_no:03d}.html#msg-{first_anchor}",
                "ts": ts,
                "prompt_html": _md(_group_prompt(group)),
                "stats_line": _group_stats_line(group),
                "long_html": _md(_group_long_text(group))
                if _group_long_text(group) else "",
            })
        page_views.append(views)

    page_tpl = env.get_template("page.html")
    for page_no, views in enumerate(page_views, start=1):
        html = page_tpl.render(number=page_no, total_pages=total_pages,
                               messages=views, **common)
        atomic_write_text(out_dir / f"page-{page_no:03d}.html", html)

    # Re-rendering into an existing folder: drop pages left over from a previous,
    # longer render so nothing stale stays linkable. Done BEFORE the index is
    # written, so the index is the last file published and never points at a page
    # that is about to be removed.
    for stale in out_dir.glob("page-*.html"):
        try:
            if int(stale.stem.split("-")[1]) > total_pages:
                stale.unlink()
        except (ValueError, IndexError, OSError):
            continue

    index_tpl = env.get_template("index.html")
    # Count what was rendered, not what was parsed: a filter can empty a message,
    # and an index reading "4 messages" above two cards is simply wrong.
    visible = copy.copy(session)
    visible.messages = [m for m in session.messages if m.blocks]
    html = index_tpl.render(total_pages=total_pages, index_items=index_items,
                            stats=visible.stats(), **common)
    index_path = out_dir / "index.html"
    atomic_write_text(index_path, html)
    return index_path
