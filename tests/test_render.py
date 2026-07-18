"""Renderer tests: IR → Markdown and paginated HTML."""

from __future__ import annotations

from xexport.model import Block, Message, Session, TOOL_CALL, TOOL_RESULT, USER_TEXT
from xexport.render.html import render_html
from xexport.render.markdown import render_markdown
from xexport.sources import claude

from conftest import CLAUDE_SESSION_ID


def _session(claude_store) -> Session:
    return claude.parse_file(claude.find_session(CLAUDE_SESSION_ID))


class TestMarkdown:
    def test_full_render(self, claude_store):
        md = render_markdown(_session(claude_store))
        assert md.startswith("# My Renamed Chat: v2?")
        assert "## 👤 User" in md
        assert "## 🤖 Claude" in md
        assert "💭 Thinking" in md
        assert "🔧 Bash" in md
        assert "file_a.py" in md  # tool output present

    def test_brief_strips_tools_and_thinking(self, claude_store):
        md = render_markdown(_session(claude_store), brief=True)
        # file_b.py only exists in tool output; file_a.py also appears in prose
        assert "🔧" not in md and "💭" not in md and "file_b.py" not in md
        assert "## 👤 User" in md and "## 🤖 Claude" in md

    def test_fence_escalates_past_backticks(self):
        s = Session(source="codex", session_id="x", app="Codex")
        s.messages = [Message(role="tool", blocks=[
            Block(kind=TOOL_RESULT, output="```python\nprint('hi')\n```")])]
        md = render_markdown(s)
        # the fence around the output must be longer than the inner ```
        assert "````" in md

    def test_truncation_and_full(self):
        s = Session(source="claude", session_id="x", app="Claude Code")
        s.messages = [Message(role="tool", blocks=[
            Block(kind=TOOL_RESULT, output="y" * 5000)])]
        assert "truncated" in render_markdown(s)
        assert "truncated" not in render_markdown(s, truncate=0)


class TestHtml:
    def test_paginated_output(self, claude_store, tmp_path):
        out = tmp_path / "html"
        index = render_html(_session(claude_store), out)
        assert index.is_file()
        assert (out / "page-001.html").is_file()
        html = index.read_text(encoding="utf-8")
        assert "My Renamed Chat: v2?" in html
        assert "index-item" in html

    def test_script_in_user_text_is_escaped(self, claude_store, tmp_path):
        # what_bug_this_catches: transcript content must never become live
        # markup in the export (XSS via chat content)
        out = tmp_path / "html"
        render_html(_session(claude_store), out)
        page = (out / "page-001.html").read_text(encoding="utf-8")
        assert "<script>alert(1)</script>" not in page
        assert "alert(1)" in page  # still visible as text

    def test_five_prompts_per_page(self, tmp_path):
        s = Session(source="claude", session_id="x", app="Claude Code",
                    title="Paging")
        for i in range(12):
            s.messages.append(Message(role="user", blocks=[
                Block(kind=USER_TEXT, text=f"prompt {i}")]))
        out = tmp_path / "html"
        render_html(s, out)
        pages = sorted(p.name for p in out.glob("page-*.html"))
        assert pages == ["page-001.html", "page-002.html", "page-003.html"]

    def test_tool_icons_dont_crash_unknown_names(self, tmp_path):
        s = Session(source="codex", session_id="x", app="Codex", title="t")
        s.messages = [Message(role="assistant", blocks=[
            Block(kind=TOOL_CALL, name="totally_novel_tool", args="{}")])]
        index = render_html(s, tmp_path / "h")
        assert index.is_file()
