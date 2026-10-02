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
    estimate_tokens, layout_key, page_limits, page_name, render_html, token_weight,
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
        assert text.count("\n") + 1 <= limits.lines, page.name
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

    def test_default_budget_sits_under_the_reader_caps(self):
        """The defaults are the claim the whole layout makes; pin them."""
        assert html_mod.PAGE_MAX_TOKENS <= 20_000 < 25_000
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

    def test_a_refresh_does_not_rewrite_an_unchanged_page(self, tmp_path, monkeypatch):
        _limits(monkeypatch, tokens=3000)
        s = _session(20)
        render_html(s, tmp_path)
        closed = _pages(tmp_path)[:-1]
        stamps = {p.name: p.stat().st_mtime_ns for p in closed}
        s.messages.append(_user("a new prompt"))
        render_html(s, tmp_path)
        assert {p.name: p.stat().st_mtime_ns for p in closed} == stamps


class TestManyPages:
    @staticmethod
    def _one_prompt_per_page(monkeypatch, tmp_path, prompts: int) -> Session:
        # Fast plain writes: a thousand fsyncs is a slow way to test naming.
        def plain(path, value):
            with open(path, "w", encoding="utf-8", newline="") as f:
                f.write(value)
        monkeypatch.setattr(html_mod, "atomic_write_text", plain)
        s = _session(prompts, reply="word " * 250)
        _limits(monkeypatch, tokens=1500)
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
        """_path_budget assumed page-001.html. The name it reserves for must be at
        least as long as any page name an export can produce."""
        budget = cli._path_budget(tmp_path, 10_000)
        deepest = len(str(tmp_path.resolve())) + 1 + budget + len("\\" + page_name(9_999_999))
        assert deepest <= 260


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
            assert cells[4] == f"{text.count(chr(10)) + 1:,}"
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
                if target.endswith(".css"):
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
        s = _session(1, replies=40)
        s.messages.append(_user("second"))
        s.messages.append(_assistant("word " * 1200))
        render_html(s, tmp_path)
        index = _read(tmp_path / "index.html")
        for number in re.findall(r'class="page-divider" id="page-(\d+)"', index):
            text = _read(tmp_path / page_name(int(number)))
            assert "Continues prompt" not in text

    def test_files_are_published_pages_then_full_then_index(
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
        assert order[-2] == "full.html"
        assert all(name.startswith(("page-", "xexport.")) for name in order[:-2])


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
        for name, text in module.synthetic().items():
            real = module.READ_TOOL_TOKENS[name]
            assert estimate_tokens(text) >= 0.9 * real, name


# ------------------------------------------------------- upgrade through the CLI
def _run(*args):
    return CliRunner().invoke(main, list(args))


def _export(out: Path, *extra):
    return _run("current", "--session-id", CLAUDE_SESSION_ID, "--format", "html",
                "--out", str(out), *extra)


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

    def test_markdown_records_carry_no_layout(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        r = _run("current", "--session-id", CLAUDE_SESSION_ID, "--format", "md",
                 "--out", str(out))
        assert r.exit_code == 0
        assert "layout" not in cursors.read_md_marker(next(out.glob("*.md")))
