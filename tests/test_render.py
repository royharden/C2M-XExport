"""Renderer tests: IR → Markdown and paginated HTML."""

from __future__ import annotations

from xexport.model import (
    ASSISTANT_TEXT, RAW, THINKING, TOOL_CALL, TOOL_RESULT, USER_TEXT,
    Block, Message, Session,
)
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


class TestFilterParity:
    """The content filters are privacy controls, so they must reach EVERY format."""

    @staticmethod
    def _loaded() -> Session:
        s = Session(source="claude", session_id="x", app="Claude Code",
                    title="Filter parity")
        s.messages = [
            Message(role="user", blocks=[
                Block(kind=USER_TEXT, text="deploy it")]),
            Message(role="assistant", blocks=[
                Block(kind=THINKING, text="the password is SECRET-THINKING"),
                Block(kind=ASSISTANT_TEXT, text="done"),
                Block(kind=TOOL_CALL, name="Bash",
                      args='{"command": "psql://admin:SECRET-ARG@db"}'),
            ]),
            Message(role="tool", blocks=[
                Block(kind=TOOL_RESULT, output="AWS_SECRET_ACCESS_KEY=SECRET-OUTPUT"),
                Block(kind=RAW, name="entry", text='{"x": "SECRET-RAW"}'),
            ]),
        ]
        return s

    SECRETS = ("SECRET-THINKING", "SECRET-ARG", "SECRET-OUTPUT", "SECRET-RAW")

    def test_html_honours_brief(self, tmp_path):
        """what_bug_this_catches: --brief filtered Markdown only, so `--format both
        --brief` wrote a redacted .md beside an HTML export that still carried every
        thinking block, tool argument, tool result and raw entry -- and, because HTML
        does no truncation, carried them in full. A privacy flag that silently applies
        to one of two requested formats is worse than no flag."""
        out = tmp_path / "html"
        render_html(self._loaded(), out, brief=True)
        blob = "".join(p.read_text(encoding="utf-8") for p in out.glob("*.html"))
        for secret in self.SECRETS:
            assert secret not in blob, f"{secret} leaked into HTML under --brief"
        assert "deploy it" in blob and "done" in blob

    def test_html_honours_granular_filters(self, tmp_path):
        out = tmp_path / "html"
        render_html(self._loaded(), out, include_tools=False)
        blob = "".join(p.read_text(encoding="utf-8") for p in out.glob("*.html"))
        assert "SECRET-ARG" not in blob and "SECRET-OUTPUT" not in blob
        assert "SECRET-RAW" not in blob
        assert "SECRET-THINKING" in blob        # --no-tools keeps thinking

        out2 = tmp_path / "html2"
        render_html(self._loaded(), out2, include_thinking=False)
        blob2 = "".join(p.read_text(encoding="utf-8") for p in out2.glob("*.html"))
        assert "SECRET-THINKING" not in blob2
        assert "SECRET-OUTPUT" in blob2         # --no-thinking keeps tools

    def test_both_formats_exclude_the_same_content(self, tmp_path):
        """Parity, not merely 'HTML filters something'."""
        out = tmp_path / "html"
        render_html(self._loaded(), out, brief=True)
        html = "".join(p.read_text(encoding="utf-8") for p in out.glob("*.html"))
        md = render_markdown(self._loaded(), brief=True)
        for secret in self.SECRETS:
            assert (secret in md) == (secret in html) is False
