"""Size-aware HTML pagination, full.html, the page map and the layout upgrade.

The contract under test: a page-NNN.html is one whole read for an agent's file-read
tool, whatever the conversation holds, and nothing is ever cut to make that true.
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest
from click.testing import CliRunner

from xexport import cli, cursors
from xexport.cli import main
from xexport.model import (
    ASSISTANT_TEXT, RAW, THINKING, TOOL_CALL, TOOL_RESULT, USER_TEXT,
    Block, Message, Session,
)
from xexport.render import html as html_mod
from xexport.render.html import (
    _span, estimate_tokens, layout_key, page_limits, page_name, render_html,
    token_weight,
)

from conftest import CLAUDE_SESSION_ID

HUGE = str(10**9)
REPO = Path(__file__).resolve().parent.parent


# ------------------------------------------------------------------ local helpers
def _limits(monkeypatch, *, tokens=HUGE, lines=HUGE, nbytes=HUGE) -> None:
    monkeypatch.setenv("XEXPORT_PAGE_MAX_TOKENS", str(tokens))
    monkeypatch.setenv("XEXPORT_PAGE_MAX_LINES", str(lines))
    monkeypatch.setenv("XEXPORT_PAGE_MAX_BYTES", str(nbytes))


def _user(text: str) -> Message:
    return Message(role="user", blocks=[Block(kind=USER_TEXT, text=text)],
                   timestamp="2026-07-17T10:00:00.000Z")


def _assistant(text: str) -> Message:
    return Message(role="assistant", blocks=[Block(kind=ASSISTANT_TEXT, text=text)])


def _tool(output: str) -> Message:
    return Message(role="tool", blocks=[Block(kind=TOOL_RESULT, output=output)])


def _session(prompts: int, *, reply: str = "word " * 120,
             replies: int = 1) -> Session:
    s = Session(source="claude", session_id="x", app="Claude Code", title="Paging")
    for i in range(prompts):
        s.messages.append(_user(f"prompt number {i}"))
        for j in range(replies):
            s.messages.append(_assistant(f"reply {i}.{j} {reply}"))
    return s


def _pages(out: Path) -> list[Path]:
    return sorted(out.glob("page-*.html"), key=lambda p: int(p.stem.split("-")[1]))


def _read(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def _is_oversize(text: str) -> bool:
    return 'class="page-note oversize"' in text


def _message_ids(text: str) -> list[int]:
    return [int(n) for n in re.findall(r'<div class="message [^"]*" id="msg-(\d+)"',
                                       text)]


def _assert_within(out: Path) -> None:
    """Every page that is not flagged oversize is inside all three limits."""
    limits = page_limits()
    for page in _pages(out):
        text = _read(page)
        if _is_oversize(text):
            continue
        assert len(text.encode("utf-8")) <= limits.bytes, page.name
        assert text.endswith("</html>\n"), page.name
        assert text.count("\n") <= limits.lines, page.name
        assert estimate_tokens(text) <= limits.tokens, page.name


# --------------------------------------------------------------------- triggers
class TestEachTriggerAlone:
    """Each limit closes a page by itself, with the other two out of reach."""

    def test_no_limit_in_reach_means_one_page(self, tmp_path, monkeypatch):
        _limits(monkeypatch)
        render_html(_session(30), tmp_path)
        assert [p.name for p in _pages(tmp_path)] == ["page-001.html"]

    def test_tokens(self, tmp_path, monkeypatch):
        _limits(monkeypatch, tokens=3000)
        render_html(_session(30), tmp_path)
        assert len(_pages(tmp_path)) > 3
        _assert_within(tmp_path)

    def test_lines(self, tmp_path, monkeypatch):
        _limits(monkeypatch, lines=60)
        render_html(_session(30), tmp_path)
        assert len(_pages(tmp_path)) > 3
        _assert_within(tmp_path)

    def test_bytes(self, tmp_path, monkeypatch):
        _limits(monkeypatch, nbytes=6000)
        render_html(_session(30), tmp_path)
        assert len(_pages(tmp_path)) > 3
        _assert_within(tmp_path)

    def test_no_page_is_over_at_any_limit_in_a_sweep(self, tmp_path, monkeypatch):
        """what_bug_this_catches: accounting that is right at one limit and a few
        bytes out at another -- the last page's disabled "Next" is longer than
        the link the shell was measured with."""
        s = _session(6)
        for step, limit in enumerate(range(4200, 6200, 37)):
            _limits(monkeypatch, nbytes=limit)
            out = tmp_path / str(step)
            render_html(s, out)
            _assert_within(out)

    def test_a_multi_line_title_cannot_push_a_page_past_the_line_limit(
            self, tmp_path, monkeypatch):
        """what_bug_this_catches: a title with line breaks in it once went onto
        every page as it was, adding lines the page budget never counted. Only
        a line limit shows it; the token and byte limits have slack to spare.
        Continuation pages are in the sweep because they carry one line more
        than the first page of a group."""
        s = _session(1, replies=150, reply="w")
        s.title = "a\nb\n" * 40
        for limit in range(52, 64):
            _limits(monkeypatch, lines=limit)
            out = tmp_path / str(limit)
            render_html(s, out)
            assert len(_pages(out)) > 3
            assert any("Continues prompt" in _read(p) for p in _pages(out))
            _assert_within(out)

    def test_default_budget_sits_under_the_reader_caps(self):
        """The defaults are the claim the whole layout makes; pin them."""
        assert html_mod.PAGE_MAX_TOKENS <= 17_000 < 25_000
        assert html_mod.PAGE_MAX_LINES <= 1_500 < 2_000
        assert html_mod.PAGE_MAX_BYTES <= 200_000 < 256_000

    def test_a_bad_override_falls_back_instead_of_raising(self, monkeypatch):
        """what_bug_this_catches: this runs inside a Stop hook, where an exception
        blocks the turn. A mistyped override must cost nothing but the override."""
        for bad in ("", "abc", "0", "-5", "1.5"):
            monkeypatch.setenv("XEXPORT_PAGE_MAX_TOKENS", bad)
            assert page_limits().tokens == html_mod.PAGE_MAX_TOKENS


class TestCutRules:
    def test_pages_break_between_prompts_when_they_can(self, tmp_path, monkeypatch):
        _limits(monkeypatch, tokens=3000)
        render_html(_session(30), tmp_path)
        for page in _pages(tmp_path):
            text = _read(page)
            assert "Continues prompt" not in text
            # A whole prompt group: the first message drawn is a user prompt.
            assert re.search(r'<div class="message (\w+)', text).group(1) == "user"

    def test_a_group_larger_than_a_page_is_split_between_messages(
            self, tmp_path, monkeypatch):
        _limits(monkeypatch, tokens=3000)
        s = _session(1, replies=40)
        s.messages.append(_user("the second prompt"))
        s.messages.append(_assistant("short"))
        render_html(s, tmp_path)
        pages = _pages(tmp_path)
        assert len(pages) > 2
        _assert_within(tmp_path)
        assert "Continues prompt" not in _read(pages[0])
        for page in pages[1:-1]:
            assert "Continues prompt #1 from" in _read(page)
        index = _read(tmp_path / "index.html")
        assert f"pages 1–{len(pages) - 1}" in index or f"pages 1–{len(pages)}" in index

    def test_one_oversize_message_gets_its_own_page_and_is_not_cut(
            self, tmp_path, monkeypatch):
        """Receipts are full fidelity: an oversize message is flagged, never
        truncated, and never shares its page."""
        _limits(monkeypatch, tokens=3000)
        s = _session(2)
        blob = "\n".join(f"line {i} of the big tool output" for i in range(2000))
        s.messages.insert(2, _tool(blob))
        s.messages.append(_user("after the blob"))
        render_html(s, tmp_path)

        oversize = [p for p in _pages(tmp_path) if _is_oversize(_read(p))]
        assert len(oversize) == 1
        text = _read(oversize[0])
        assert _message_ids(text) == [2]
        assert "line 0 of the big tool output" in text
        assert "line 1999 of the big tool output" in text
        index = _read(tmp_path / "index.html")
        row = re.search(rf'<tr data-page="{int(oversize[0].stem.split("-")[1])}">.*?</tr>',
                        index).group(0)
        assert "OVERSIZE" in row
        # Everything else still fits.
        _assert_within(tmp_path)

    def test_anchors_are_global_and_in_order(self, tmp_path, monkeypatch):
        _limits(monkeypatch, tokens=3000)
        s = _session(12, replies=3)
        render_html(s, tmp_path)
        ids = [i for page in _pages(tmp_path) for i in _message_ids(_read(page))]
        assert ids == list(range(len(s.messages)))


class TestPrefixStability:
    def test_appending_turns_never_changes_a_closed_page(self, tmp_path, monkeypatch):
        """what_bug_this_catches: a page that lists the page total, the export time
        or every other page changes whenever the session grows, so every link into
        it and every sync of it is disturbed by an unrelated later turn."""
        _limits(monkeypatch, tokens=3000)
        before, after = tmp_path / "before", tmp_path / "after"
        s = _session(20, replies=2)
        render_html(s, before)
        closed = _pages(before)[:-1]
        assert len(closed) >= 3

        grown = _session(20, replies=2)
        grown.messages.append(_assistant("one more message in the last group"))
        for i in range(15):
            grown.messages.append(_user(f"later prompt {i}"))
            grown.messages.append(_assistant("later reply " + "word " * 300))
        render_html(grown, after)
        assert len(_pages(after)) > len(closed) + 1
        for page in closed:
            assert _read(page) == _read(after / page.name), page.name

    def test_a_new_title_never_moves_a_page_cut(self, tmp_path, monkeypatch):
        """what_bug_this_catches: the title is on every page, so its length was
        part of every page's budget. Chats are retitled while they run, and a
        longer title moved the cuts: nine pages in ten then held different
        messages, and every link into them pointed at the wrong page."""
        _limits(monkeypatch, tokens=3000)
        # One prompt with many tiny replies packs every page to within a few
        # tokens of the limit, so any title cost that leaks into the budget
        # shows up as a moved cut.
        for number, title in enumerate([
                "<" * 200,                                   # every character an entity
                "日本語のタイトル & <more> " * 9,
                "a\nb\n" * 40,                               # line breaks in a title
                "😀" * 200]):
            before = tmp_path / f"before{number}"
            after = tmp_path / f"after{number}"
            s = _session(1, replies=120, reply="w")
            s.title = "A"
            render_html(s, before)
            s.title = title
            s.messages.append(_assistant("one more message"))
            render_html(s, after)
            closed = _pages(before)[:-1]
            assert len(closed) >= 3
            for page in closed:
                assert (_message_ids(_read(page))
                        == _message_ids(_read(after / page.name))), (title, page.name)
            _assert_within(after)

    def test_pages_do_not_carry_the_xexport_version(self, tmp_path):
        """A version on every page would rewrite every page on every upgrade."""
        from xexport import __version__
        render_html(_session(3), tmp_path)
        assert f"v{__version__}" not in _read(tmp_path / "page-001.html")
        assert f"v{__version__}" in _read(tmp_path / "index.html")

    def test_a_refresh_writes_what_changed_and_nothing_else(self, tmp_path,
                                                            monkeypatch):
        """Both directions: a page with a successor is not written again, and the
        page that did change is. Skipping too much leaves stale pages on disk."""
        _limits(monkeypatch, tokens=3000)
        s = _session(20)
        render_html(s, tmp_path)
        pages = [p.name for p in _pages(tmp_path)]
        closed, last = pages[:-1], pages[-1]

        written = []
        real = html_mod.atomic_write_text

        def recording(path, value):
            written.append(Path(path).name)
            real(path, value)

        monkeypatch.setattr(html_mod, "atomic_write_text", recording)
        s.messages.append(_assistant("GROWN-REPLY in the last group"))
        render_html(s, tmp_path)
        assert not set(written) & set(closed)
        assert {"full.html", "index.html"} <= set(written)
        assert "xexport.css" not in written and "xexport.js" not in written
        new_pages = [n for n in written if n.startswith("page-")]
        assert new_pages and all(n == last or n not in pages for n in new_pages)
        holders = [p.name for p in _pages(tmp_path) if "GROWN-REPLY" in _read(p)]
        assert len(holders) == 1 and holders[0] in new_pages
        assert "GROWN-REPLY" in _read(tmp_path / "full.html")

    def test_no_page_carries_the_export_time(self, tmp_path, monkeypatch):
        """what_bug_this_catches: an export time on a page rewrites every page on
        every refresh. Comparing two renders cannot see it unless the clock
        happens to tick between them, so the clock is pinned instead."""
        class Clock:
            @staticmethod
            def now():
                return Clock()

            def astimezone(self):
                return self

            def strftime(self, _format):
                return "CLOCK-SENTINEL"

        monkeypatch.setattr(html_mod, "datetime", Clock)
        _limits(monkeypatch, tokens=3000)
        render_html(_session(12), tmp_path)
        assert "CLOCK-SENTINEL" in _read(tmp_path / "index.html")
        assert "CLOCK-SENTINEL" in _read(tmp_path / "full.html")
        assert len(_pages(tmp_path)) > 1
        for page in _pages(tmp_path):
            assert "CLOCK-SENTINEL" not in _read(page), page.name


class TestManyPages:
    @staticmethod
    def _one_prompt_per_page(monkeypatch, tmp_path, prompts: int) -> Session:
        # Fast plain writes: a thousand fsyncs is a slow way to test naming.
        def plain(path, value):
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(value)
        monkeypatch.setattr(html_mod, "atomic_write_text", plain)
        s = _session(prompts, reply="word " * 250)
        _limits(monkeypatch, nbytes=5000)
        render_html(s, tmp_path)
        return s

    def test_navigation_does_not_grow_with_the_page_count(self, tmp_path, monkeypatch):
        """what_bug_this_catches: navigation that lists every page is itself an
        over-cap file on a session with hundreds of pages."""
        self._one_prompt_per_page(monkeypatch, tmp_path, 300)
        pages = _pages(tmp_path)
        assert len(pages) == 300
        early, late = _read(pages[1]), _read(pages[250])
        assert abs(len(early) - len(late)) < 40
        for text in (early, late):
            assert len(re.findall(r'href="page-\d+\.html"', text)) <= 4

    def test_more_than_999_pages(self, tmp_path, monkeypatch):
        s = self._one_prompt_per_page(monkeypatch, tmp_path, 1003)
        names = [p.name for p in _pages(tmp_path)]
        assert len(names) == 1003
        assert names[998:1001] == ["page-999.html", "page-1000.html", "page-1001.html"]
        assert 'href="page-1000.html"' in _read(tmp_path / "page-999.html")
        assert 'href="page-999.html"' in _read(tmp_path / "page-1000.html")
        index = _read(tmp_path / "index.html")
        assert 'href="page-1003.html#msg-2004"' in index
        assert '<tr data-page="1003">' in index

        # The stale-page sweep still understands four-digit names.
        s.messages = s.messages[:10]
        render_html(s, tmp_path)
        assert len(_pages(tmp_path)) == 5

    def test_path_budget_reserves_room_for_long_page_names(self, tmp_path):
        """_path_budget assumed page-001.html. What it reserves for must be at
        least as long as the deepest path an export really writes, which is the
        temporary file a late page is published through."""
        budget = cli._path_budget(tmp_path, 10_000)
        temporary = "\\." + page_name(9_999_999) + ".XXXXXXXX.tmp"
        deepest = len(str(tmp_path.resolve())) + 1 + budget + len(temporary)
        assert deepest < 260
        marker = "\\." + cursors.HTML_CURSOR_NAME + ".XXXXXXXX.tmp"
        assert len(str(tmp_path.resolve())) + 1 + budget + len(marker) < 260


class TestFullHtml:
    def test_full_holds_exactly_the_messages_of_the_pages(self, tmp_path, monkeypatch):
        _limits(monkeypatch, tokens=3000)
        render_html(_session(20, replies=2), tmp_path)
        paged = [i for page in _pages(tmp_path) for i in _message_ids(_read(page))]
        full = _read(tmp_path / "full.html")
        assert _message_ids(full) == paged
        assert len(paged) == 60

    def test_full_marks_where_each_page_starts(self, tmp_path, monkeypatch):
        _limits(monkeypatch, tokens=3000)
        render_html(_session(20), tmp_path)
        full = _read(tmp_path / "full.html")
        for page in _pages(tmp_path):
            number = int(page.stem.split("-")[1])
            marker = f'<div class="page-break" id="page-{number}" data-page="{number}">'
            assert full.count(marker) == 1
            first = _message_ids(_read(page))[0]
            assert full.index(marker) < full.index(f'id="msg-{first}"')
            if first:
                assert full.index(f'id="msg-{first - 1}"') < full.index(marker)

    def test_the_warning_is_at_the_top_of_the_file(self, tmp_path):
        """A truncated read shows the top of a file, so that is where the note
        saying 'do not read this whole' has to be."""
        render_html(_session(3), tmp_path)
        full = _read(tmp_path / "full.html")
        banner = full.index('class="full-banner"')
        assert banner < full.index("<h1>") < full.index('class="message ')
        assert "too large to read whole" in full[banner:banner + 900]
        # And once more as the second line of the file, ahead of the inline
        # stylesheet, which is where a `head` or a cut-short read looks.
        second = full.split("\n")[1]
        assert second.startswith("<!-- full.html:") and second.endswith("-->")
        assert "too large to read whole" in second

    def test_full_stands_alone_and_pages_share_assets(self, tmp_path):
        render_html(_session(3), tmp_path)
        full = _read(tmp_path / "full.html")
        assert "<style>" in full and 'href="xexport.css"' not in full
        for name in ("index.html", "page-001.html"):
            text = _read(tmp_path / name)
            assert "<style>" not in text and 'href="xexport.css"' in text
        assert (tmp_path / "xexport.css").is_file()
        assert (tmp_path / "xexport.js").is_file()

    def test_filters_reach_full_and_pages(self, tmp_path):
        """The filters are privacy controls: a new output file is a new place for
        excluded content to leak, so it is checked by name."""
        s = Session(source="claude", session_id="x", app="Claude Code", title="t")
        s.messages = [
            _user("deploy it"),
            Message(role="assistant", blocks=[
                Block(kind=THINKING, text="SECRET-THINKING"),
                Block(kind=ASSISTANT_TEXT, text="done"),
                Block(kind=TOOL_CALL, name="Bash", args='{"c": "SECRET-ARG"}')]),
            Message(role="tool", blocks=[
                Block(kind=TOOL_RESULT, output="SECRET-OUTPUT"),
                Block(kind=RAW, name="entry", text='{"x": "SECRET-RAW"}')]),
        ]
        render_html(s, tmp_path, brief=True)
        for name in ("full.html", "page-001.html", "index.html"):
            text = _read(tmp_path / name)
            assert "SECRET" not in text, name
        assert "deploy it" in _read(tmp_path / "full.html")
        assert "done" in _read(tmp_path / "full.html")


class TestIndex:
    def test_the_page_map_comes_before_the_cards(self, tmp_path, monkeypatch):
        _limits(monkeypatch, tokens=3000)
        render_html(_session(20), tmp_path)
        index = _read(tmp_path / "index.html")
        assert index.index('class="page-map"') < index.index('class="index-item"')
        assert index.count("<tr data-page=") == len(_pages(tmp_path))
        assert 'href="full.html"' in index

    def test_the_map_reports_the_files_as_written(self, tmp_path, monkeypatch):
        _limits(monkeypatch, tokens=3000)
        render_html(_session(20), tmp_path)
        index = _read(tmp_path / "index.html")
        for page in _pages(tmp_path):
            number = int(page.stem.split("-")[1])
            row = re.search(rf'<tr data-page="{number}">(.*?)</tr>', index).group(1)
            cells = re.findall(r"<td>(.*?)</td>", row)
            text = _read(page)
            assert cells[4] == f"{text.count(chr(10)):,}"
            assert cells[6] == f"{estimate_tokens(text):,}"
            ids = _message_ids(text)
            assert cells[2] == (f"{ids[0]}–{ids[-1]}" if len(ids) > 1 else str(ids[0]))

    def test_every_link_and_anchor_resolves(self, tmp_path, monkeypatch):
        """Every href points at a file that exists and, where it names a fragment,
        at an id inside that file. An index card must land on the page that
        actually holds its message."""
        _limits(monkeypatch, tokens=3000)
        s = _session(8, replies=2)
        s.messages[1:1] = [_assistant("filler " * 900) for _ in range(12)]
        render_html(s, tmp_path)
        assert len(_pages(tmp_path)) > 3

        texts = {p.name: _read(p) for p in tmp_path.glob("*.html")}
        ids = {name: set(re.findall(r'\bid="([^"]+)"', text))
               for name, text in texts.items()}
        checked = 0
        for name, text in texts.items():
            for href in re.findall(r'href="([^"]+)"', text):
                if href.startswith(("http://", "https://")):
                    continue
                target, _, fragment = href.partition("#")
                target = target or name
                if target.endswith((".css", ".json")):
                    assert (tmp_path / target).is_file()
                    continue
                assert target in texts, f"{name} links to missing {target}"
                if fragment:
                    assert fragment in ids[target], f"{name} -> {href}"
                checked += 1
        assert checked > 50
        for src in re.findall(r'<script src="([^"]+)"', "".join(texts.values())):
            assert (tmp_path / src).is_file()

        cards = re.findall(r'<div class="index-item"><a href="(page-\d+\.html)#msg-(\d+)"',
                           texts["index.html"])
        assert len(cards) == 8
        for page, anchor in cards:
            assert int(anchor) in _message_ids(texts[page])

    def test_the_map_flags_a_page_with_a_very_long_line(self, tmp_path):
        """A long line is not wrapped (it is inside a <pre>, where a newline would
        change the content), so the map is where a reader learns it is there."""
        s = _session(2)
        s.messages.insert(1, _tool("x" * 5000))
        render_html(s, tmp_path)
        row = re.search(r'<tr data-page="1">.*?</tr>',
                        _read(tmp_path / "index.html")).group(0)
        assert re.search(r"longest line 5,\d{3} chars", row)
        assert "x" * 5000 in _read(tmp_path / "page-001.html")

    def test_dividers_mark_only_pages_that_open_with_a_prompt(
            self, tmp_path, monkeypatch):
        _limits(monkeypatch, tokens=3000)
        s = _session(2, reply="short")          # two prompts that share page 1
        s.messages.extend(_session(1, replies=40).messages)
        s.messages.append(_user("second"))
        s.messages.append(_assistant("word " * 1200))
        s.messages.append(_user("third"))
        s.messages.append(_assistant("short"))
        render_html(s, tmp_path)
        assert len(_message_ids(_read(tmp_path / "page-001.html"))) == 4
        index = _read(tmp_path / "index.html")
        dividers = [int(n) for n in
                    re.findall(r'class="page-divider" id="page-(\d+)"', index)]
        opens_with_a_prompt = [
            int(p.stem.split("-")[1]) for p in _pages(tmp_path)
            if "Continues prompt" not in _read(p)]
        assert dividers == opens_with_a_prompt
        assert len(dividers) >= 2 and len(dividers) < len(_pages(tmp_path))
        ids = re.findall(r'\bid="([^"]+)"', index)
        assert len(ids) == len(set(ids))

    def test_a_card_links_to_the_first_message_that_was_drawn(self, tmp_path):
        """what_bug_this_catches: under --no-tools the first message of a group
        can be emptied and never drawn. A card anchored on it links to an id
        that is on no page."""
        s = Session(source="claude", session_id="x", app="Claude Code", title="t")
        s.messages = [_tool("metadata before the first prompt"),
                      _user("the prompt"), _assistant("the answer")]
        render_html(s, tmp_path, include_tools=False)
        card = re.search(r'<div class="index-item"><a href="([^"#]+)#msg-(\d+)"',
                         _read(tmp_path / "index.html"))
        assert card.group(2) == "1"
        assert 'id="msg-1"' in _read(tmp_path / card.group(1))
        assert 'id="msg-0"' not in _read(tmp_path / card.group(1))

    def test_files_are_published_pages_then_full_then_map_then_index(
            self, tmp_path, monkeypatch):
        """The index is last so it never points at a file that is not there yet."""
        order = []
        real = html_mod.atomic_write_text

        def recording(path, value):
            order.append(Path(path).name)
            real(path, value)

        monkeypatch.setattr(html_mod, "atomic_write_text", recording)
        _limits(monkeypatch, tokens=3000)
        render_html(_session(12), tmp_path)
        assert order[-1] == "index.html"
        assert order[-2] == "pages.json"
        assert order[-3] == "full.html"
        assert all(name.startswith(("page-", "xexport.")) for name in order[:-3])


class TestPagesJson:
    def test_the_map_as_data_matches_the_files_and_the_table(self, tmp_path,
                                                             monkeypatch):
        """pages.json is the page map an agent can always read whole. Every
        number in it is measured on the file it describes."""
        _limits(monkeypatch, tokens=3000)
        s = _session(20, replies=2)
        s.messages.insert(3, _tool("=" * 2500))     # one long line to flag
        render_html(s, tmp_path)
        data = json.loads(_read(tmp_path / "pages.json"))
        pages = _pages(tmp_path)
        assert data["layout"] == html_mod.HTML_LAYOUT
        assert data["budget"] == {"tokens": 3000, "lines": 10**9, "bytes": 10**9}
        assert data["pages"] == len(pages) == len(data["page_map"])
        assert data["prompts"] == 20 and data["messages"] == 61
        assert data["files"] == {"index": "index.html", "full": "full.html"}
        index = _read(tmp_path / "index.html")
        anchors_seen = []
        for row, page in zip(data["page_map"], pages):
            text = _read(page)
            assert row["file"] == page.name
            assert row["lines"] == text.count("\n")
            assert row["bytes"] == len(text.encode("utf-8"))
            assert row["tokens"] == estimate_tokens(text)
            longest = max(len(line) for line in text.split("\n"))
            assert row.get("longest") == (longest if longest > 2000 else None)
            ids = _message_ids(text)
            assert row["messages"] == [ids[0], ids[-1]]
            anchors_seen.extend(ids)
            assert "oversize" not in row
            assert ("continues" in row) == ("Continues prompt" in text)
            # The same row, as the index table shows it to a person.
            cells = re.findall(r"<td>(.*?)</td>", re.search(
                rf'<tr data-page="{row["page"]}">(.*?)</tr>', index).group(1))
            assert cells[1] == _span(*row["prompts"], prefix="#")
            assert cells[2] == _span(*row["messages"])
            assert cells[3] == (row["first"] or "")[:19]
            assert cells[4] == f"{row['lines']:,}" and cells[6] == f"{row['tokens']:,}"
        assert anchors_seen == list(range(61))
        assert any(row.get("longest", 0) > 2400 for row in data["page_map"])
        # One page per line, so a Grep for a page number finds its row.
        lines = _read(tmp_path / "pages.json").split("\n")
        rows = [line for line in lines if line.startswith('  {"page":')]
        assert len(rows) == len(pages)

    def test_oversize_pages_are_listed(self, tmp_path, monkeypatch):
        _limits(monkeypatch, tokens=3000)
        s = _session(2)
        s.messages.insert(2, _tool("\n".join(f"line {i}" for i in range(2000))))
        render_html(s, tmp_path)
        data = json.loads(_read(tmp_path / "pages.json"))
        flagged = [row["page"] for row in data["page_map"] if row.get("oversize")]
        assert data["oversize_pages"] == flagged and len(flagged) == 1
        assert _is_oversize(_read(tmp_path / page_name(flagged[0])))

    def test_the_header_counts_what_was_drawn(self, tmp_path):
        """A privacy filter must not be visible as a count of what it removed."""
        s = Session(source="claude", session_id="x", app="Claude Code", title="t")
        s.messages = [_tool("before"), _user("one"), _assistant("a"),
                      _tool("mid"), _user("two"), _assistant("b")]
        render_html(s, tmp_path, include_tools=False)
        data = json.loads(_read(tmp_path / "pages.json"))
        assert data["messages"] == 4 and data["prompts"] == 2
        assert data["page_map"][0]["messages"] == [1, 5]
        assert "before" not in _read(tmp_path / "pages.json")

    def test_the_map_is_not_rewritten_when_nothing_changed(self, tmp_path, monkeypatch):
        render_html(_session(3), tmp_path)
        written = []
        monkeypatch.setattr(html_mod, "atomic_write_text",
                            lambda path, value: written.append(Path(path).name))
        render_html(_session(3), tmp_path)
        assert "pages.json" not in written and "page-001.html" not in written
        assert "index.html" in written

    def test_the_map_is_small_however_long_the_session(self, tmp_path, monkeypatch):
        def plain(path, value):
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(value)
        monkeypatch.setattr(html_mod, "atomic_write_text", plain)
        _limits(monkeypatch, nbytes=5000)
        render_html(_session(200, reply="word " * 250), tmp_path)
        text = _read(tmp_path / "pages.json")
        assert json.loads(text)["pages"] == 200
        # The promise the docs make: one read for a session of up to about 200
        # pages, by the same estimate the pages are budgeted with.
        assert estimate_tokens(text) < 25_000


class TestFirstExportCrash:
    def test_a_crash_during_a_first_export_leaves_no_folder(self, claude_store,
                                                            tmp_path, monkeypatch):
        """what_bug_this_catches: a folder written without its marker can never be
        verified, so every later run fails closed and writes a companion beside
        it. On the first export there is nothing to protect, so the half-written
        folder is removed instead."""
        real = cli.render_html

        def exploding(session, folder, **kwargs):
            real(session, folder, **kwargs)
            raise OSError("disk full")

        monkeypatch.setattr(cli, "render_html", exploding)
        out = tmp_path / "exports"
        bystander = out / "html" / "someone else -- their chat -- claude-other"
        bystander.mkdir(parents=True)
        (bystander / "index.html").write_text("theirs", encoding="utf-8")
        r = _export(out)
        assert r.exit_code != 0
        assert [p.name for p in (out / "html").iterdir()] == [bystander.name]
        assert (bystander / "index.html").read_text(encoding="utf-8") == "theirs"

        monkeypatch.setattr(cli, "render_html", real)
        assert _export(out).exit_code == 0
        assert len(list((out / "html").iterdir())) == 2

    def test_a_ctrl_c_during_a_first_export_cleans_up_too(self, claude_store,
                                                          tmp_path, monkeypatch):
        def interrupted(session, folder, **kwargs):
            (folder / "page-001.html").write_text("half", encoding="utf-8")
            raise KeyboardInterrupt

        monkeypatch.setattr(cli, "render_html", interrupted)
        out = tmp_path / "exports"
        r = _export(out)
        assert r.exit_code != 0
        assert not list((out / "html").iterdir())

    def test_a_crash_during_a_refresh_keeps_the_export(self, claude_store, tmp_path,
                                                       monkeypatch):
        out = tmp_path / "exports"
        assert _export(out).exit_code == 0
        folder = next((out / "html").iterdir())
        before = sorted(p.name for p in folder.iterdir())

        def exploding(session, folder, **kwargs):
            raise OSError("disk full")

        monkeypatch.setattr(cli, "render_html", exploding)
        from conftest import _jsonl, claude_entries
        _jsonl(claude_store / "projects" / "C--proj" / f"{CLAUDE_SESSION_ID}.jsonl",
               claude_entries() + claude_entries()[2:4])
        r = _export(out, "--mode", "append")
        assert r.exit_code != 0
        assert folder.is_dir() and sorted(p.name for p in folder.iterdir()) == before


class TestEstimator:
    def test_weights_add_up(self):
        a, b = "<div>héllo wörld 😀</div>\n".encode("utf-8"), b"<pre>12345</pre>\n"
        assert token_weight(a + b) == pytest.approx(token_weight(a) + token_weight(b))

    def test_the_estimate_is_not_under_the_read_tools_own_counts(self):
        """what_bug_this_catches: a flat bytes-per-token ratio. Dividing bytes by 3
        looked safe against tiktoken on real exports and under-counted the Read
        tool by 27% to 65% on JSON, hex, base64 and emoji -- the content tool-heavy
        pages are made of. The recorded counts live with the dev script."""
        spec = importlib.util.spec_from_file_location(
            "validate_token_estimate", REPO / "scripts" / "validate_token_estimate.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        checked = 0
        for name, text in module.synthetic().items():
            if name in module.KNOWN_UNDERCOUNT:
                continue
            real = module.READ_TOOL_TOKENS[name]
            estimate = estimate_tokens(text)
            assert estimate >= module.floor_for(name) * real, name
            # And not wildly over: an estimate several times too high would pass
            # the floor while cutting pages into slivers.
            assert estimate <= 1.8 * real, name
            checked += 1
        assert checked >= 27
        assert all(floor >= 0.84 for floor in module.RELAXED_FLOOR.values())


# ------------------------------------------------------- upgrade through the CLI
def _run(*args):
    return CliRunner().invoke(main, list(args))


def _export(out: Path, *extra):
    return _run("current", "--session-id", CLAUDE_SESSION_ID, "--format", "html",
                "--out", str(out), *extra)


OLD_PAGE = "OLD-LAYOUT-PAGE-SENTINEL"


def _as_written_by_0_2_3(folder: Path) -> None:
    """Strip an export back to what 0.2.3 left on disk: no layout in the record,
    no full.html, no shared assets."""
    marker = folder / cursors.HTML_CURSOR_NAME
    data = json.loads(marker.read_text(encoding="utf-8"))
    data.pop("layout")
    data["tool"] = "0.2.3"
    marker.write_text(json.dumps(data), encoding="utf-8")
    for name in ("full.html", "xexport.css", "xexport.js"):
        (folder / name).unlink()
    # 0.2.3 cut pages five prompts at a time, so its folder holds pages the new
    # layout will not: content that must be replaced, and pages that must go.
    for name in ("page-001.html", "page-002.html", "page-003.html"):
        (folder / name).write_text(f"<html>{OLD_PAGE} {name}</html>", encoding="utf-8")


class TestLayoutUpgrade:
    def test_an_old_layout_export_refreshes_into_the_new_one(self, claude_store,
                                                              tmp_path):
        """what_bug_this_catches: 'nothing changed, so nothing to do' was decided
        on the transcript alone, so an export written before full.html existed
        would never gain one unless the conversation happened to continue."""
        out = tmp_path / "exports"
        assert _export(out).exit_code == 0
        folder = next((out / "html").iterdir())
        _as_written_by_0_2_3(folder)

        r = _export(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert "Updated" in r.output and "Warning" not in r.output
        assert (folder / "full.html").is_file()
        assert cursors.read_html_marker(folder)["layout"] == layout_key()
        assert len(list((out / "html").iterdir())) == 1      # in place, no companion
        # The old pages were re-rendered, and the ones the new layout has no use
        # for are gone, rather than left beside the new ones.
        assert [p.name for p in _pages(folder)] == ["page-001.html"]
        for path in folder.glob("*.html"):
            assert OLD_PAGE not in _read(path), path.name
        assert "Second prompt" in _read(folder / "page-001.html")

    def test_up_to_date_is_still_a_no_op_afterwards(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        assert _export(out).exit_code == 0
        folder = next((out / "html").iterdir())
        _as_written_by_0_2_3(folder)
        assert _export(out, "--mode", "append").exit_code == 0

        stamps = {p.name: p.stat().st_mtime_ns for p in folder.iterdir()}
        r = _export(out, "--mode", "append")
        assert r.exit_code == 0 and "Up to date" in r.output
        assert {p.name: p.stat().st_mtime_ns for p in folder.iterdir()} == stamps

    def test_a_changed_budget_re_lays_out_an_unchanged_export(self, claude_store,
                                                              tmp_path, monkeypatch):
        out = tmp_path / "exports"
        assert _export(out).exit_code == 0
        folder = next((out / "html").iterdir())
        assert len(_pages(folder)) == 1
        monkeypatch.setenv("XEXPORT_PAGE_MAX_LINES", "30")
        r = _export(out, "--mode", "append")
        assert "Updated" in r.output
        assert len(_pages(folder)) > 1

    def test_the_upgrade_does_not_carry_an_export_past_the_shrink_guard(
            self, claude_store, tmp_path):
        """A layout change is a reason to re-render, never a reason to overwrite a
        longer export with a shorter transcript."""
        from conftest import _jsonl, claude_entries
        out = tmp_path / "exports"
        assert _export(out).exit_code == 0
        folder = next((out / "html").iterdir())
        _as_written_by_0_2_3(folder)
        before = _read(folder / "index.html")
        _jsonl(claude_store / "projects" / "C--proj" / f"{CLAUDE_SESSION_ID}.jsonl",
               claude_entries()[:3])

        r = _export(out, "--mode", "append")
        assert r.exit_code == 0 and "Warning" in r.output
        assert _read(folder / "index.html") == before
        assert not (folder / "full.html").exists()
        assert len(list((out / "html").iterdir())) == 2

    @pytest.mark.parametrize("case", ["shrink", "filters", "no-marker",
                                      "corrupt-marker", "newer-marker"])
    def test_no_guard_is_bypassed_by_the_upgrade(self, claude_store, tmp_path, case):
        """what_bug_this_catches: the layout check in cursors.validate answers "ok,
        re-render". Placed above any refusal, it would carry an old-layout export
        straight past that refusal -- rewriting a full export as --brief, or
        re-rendering over an export whose marker cannot be verified. Each guard is
        exercised here on an export with no layout recorded, and run three times
        so that a refusal which forks once per run is caught too."""
        from conftest import _jsonl, claude_entries
        out = tmp_path / "exports"
        assert _export(out).exit_code == 0
        folder = next((out / "html").iterdir())
        _as_written_by_0_2_3(folder)
        marker = folder / cursors.HTML_CURSOR_NAME
        extra = []
        if case == "shrink":
            _jsonl(claude_store / "projects" / "C--proj" / f"{CLAUDE_SESSION_ID}.jsonl",
                   claude_entries()[:3])
        elif case == "filters":
            extra = ["--brief"]
        elif case == "no-marker":
            marker.unlink()
        elif case == "corrupt-marker":
            marker.write_text('{"v": 1, "session_id": "', encoding="utf-8")
        elif case == "newer-marker":
            data = json.loads(marker.read_text(encoding="utf-8"))
            data["v"] = cursors.CURSOR_V + 1
            marker.write_text(json.dumps(data), encoding="utf-8")
        before = {p.name: _read(p) for p in folder.glob("*.html")}

        for _ in range(3):
            r = _export(out, "--mode", "append", *extra)
            assert r.exit_code == 0, r.output

        assert {p.name: _read(p) for p in folder.glob("*.html")} == before
        assert not (folder / "full.html").exists()
        assert len(list((out / "html").iterdir())) == 2      # one companion, once

    def test_a_layout_2_export_gains_pages_json_on_refresh(self, claude_store, tmp_path):
        """0.3.0 wrote layout 2: no pages.json, a 20,000-token budget."""
        out = tmp_path / "exports"
        assert _export(out).exit_code == 0
        folder = next((out / "html").iterdir())
        marker = folder / cursors.HTML_CURSOR_NAME
        data = json.loads(marker.read_text(encoding="utf-8"))
        data["layout"] = "2:20000/1500/200000"
        data["tool"] = "0.3.0"
        marker.write_text(json.dumps(data), encoding="utf-8")
        (folder / "pages.json").unlink()

        r = _export(out, "--mode", "append")
        assert r.exit_code == 0 and "Updated" in r.output
        assert (folder / "pages.json").is_file()
        assert cursors.read_html_marker(folder)["layout"] == layout_key()
        assert layout_key().startswith("3:")
        r = _export(out, "--mode", "append")
        assert "Up to date" in r.output

    def test_markdown_records_carry_no_layout(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        r = _run("current", "--session-id", CLAUDE_SESSION_ID, "--format", "md",
                 "--out", str(out))
        assert r.exit_code == 0
        assert "layout" not in cursors.read_md_marker(next(out.glob("*.md")))
