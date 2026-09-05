"""Append mode, export naming, output layout, and subagent transcripts.

House rule from AGENTS.md: a fixed bug gets a regression test that says which bug it
catches. The two marked REGRESSION tests below cover the two real defects found while
designing 0.2.0.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from xexport import cursors, naming
from xexport.cli import main
from xexport.model import Session
from xexport.sources import claude

from conftest import CLAUDE_SESSION_ID, _jsonl, claude_entries

SUBAGENT_ID = "agent-a109fa34123bba393"


# ------------------------------------------------------------------ local helpers
def _transcript(store: Path) -> Path:
    return store / "projects" / "C--proj" / f"{CLAUDE_SESSION_ID}.jsonl"


def _append_entries(store: Path, entries: list[dict]) -> None:
    """Grow the synthetic transcript the way a live session would."""
    with open(_transcript(store), "a", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")


def _turn(prompt: str, reply: str) -> list[dict]:
    sid = CLAUDE_SESSION_ID
    return [
        {"type": "user", "sessionId": sid, "timestamp": "2026-07-17T11:00:00.000Z",
         "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}},
        {"type": "assistant", "sessionId": sid, "timestamp": "2026-07-17T11:00:05.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}},
    ]


def _run(*args):
    return CliRunner().invoke(main, list(args))


def _export_md(out: Path, *extra):
    return _run("current", "--session-id", CLAUDE_SESSION_ID,
                "--format", "md", "--out", str(out), *extra)


@pytest.fixture
def subagent_store(claude_store):
    """A subagent transcript in the shape Claude Code writes on disk."""
    folder = claude_store / "projects" / "C--proj" / CLAUDE_SESSION_ID / "subagents"
    _jsonl(folder / f"{SUBAGENT_ID}.jsonl", [
        {"parentUuid": None, "isSidechain": True, "agentId": SUBAGENT_ID,
         "type": "user", "sessionId": CLAUDE_SESSION_ID, "cwd": "C:\\proj",
         "timestamp": "2026-07-17T10:30:00.000Z",
         "message": {"role": "user", "content":
                     "Your callsign is 0009_Claude_Sonnet5_Sub_Explorer (parent 0007). "
                     "Read these files and summarize them."}},
        {"isSidechain": True, "agentId": SUBAGENT_ID, "type": "assistant",
         "sessionId": CLAUDE_SESSION_ID, "timestamp": "2026-07-17T10:30:09.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "Here is the summary you asked for."}]}},
    ])
    (folder / f"{SUBAGENT_ID}.meta.json").write_text(
        json.dumps({"agentType": "general-purpose",
                    "description": "Summarize existing canonical skills",
                    "toolUseId": "toolu_01HdZ"}),
        encoding="utf-8",
    )
    return claude_store


# ============================================================================ naming
class TestNaming:
    def _session(self, sid=CLAUDE_SESSION_ID, title="My chat"):
        return Session(source="claude", session_id=sid, title=title, app="Claude Code")

    def test_default_shape_with_callsign(self):
        name = naming.build_name(self._session(), callsign="0007_Claude_Opus5")
        assert name == "0007_Claude_Opus5 -- My chat -- claude-11111111-2222-3333-4444-555555555555"

    def test_no_registry_falls_back_to_harness_and_model(self):
        """Roy 2026-09-04: every export says which agent produced it — a callsign when
        the project has an AgentNamer registry, harness+model when it does not. The two
        forms are deliberately compatible, so adopting AgentNamer later only adds the id
        rather than renaming everything."""
        s = self._session()
        s.model = "claude-fable-5-1"
        assert naming.build_name(s) == "Claude_Fable51 -- My chat -- claude-11111111-2222-3333-4444-555555555555"
        assert (naming.build_name(s, callsign="0000_Claude_Fable51")
                == "0000_Claude_Fable51 -- My chat -- claude-11111111-2222-3333-4444-555555555555")

    def test_an_empty_prefix_leaves_no_leading_separator(self):
        s = Session(source="", session_id="11111111-2222-3333-4444-555555555555", title="My chat")
        # An unknown source contributes no prefix and no "<source>-" on the
        # identity. Regression: sanitize_title("") returns "session", which had
        # turned an empty source into a literal "session-" prefix.
        assert naming.build_name(s) == "My chat -- 11111111-2222-3333-4444-555555555555"
        assert not naming.build_name(s).startswith(("_", "-", " "))

    def test_model_tokens_match_agentnamer_format(self):
        """Verified against a real registry record on this machine, which stores
        {"harness": "Claude", "model": "Fable51"} — the fallback must agree."""
        assert naming.normalize_model("claude-fable-5-1") == "Fable51"
        assert naming.normalize_model("claude-opus-4-8") == "Opus48"
        assert naming.normalize_model("claude-haiku-4-5-20251001") == "Haiku45"
        assert naming.normalize_model("gpt-5.6-terra") == "GPT56-Terra"
        assert naming.normalize_model("") == ""

    def test_a_subagent_fallback_is_marked_sub(self):
        s = Session(source="claude", session_id="agent-a109fa34123bba393",
                    title="t", model="claude-sonnet-5", is_subagent=True)
        assert naming.build_name(s).startswith("Claude_Sonnet5_Sub -- ")

    def test_template_reproduces_pre_0_2_0_names(self):
        assert naming.build_name(self._session(), template="{title}") == "My chat"

    def test_unknown_field_is_a_clear_error(self):
        with pytest.raises(ValueError) as exc:
            naming.build_name(self._session(), template="{nope}")
        assert "{nope}" in str(exc.value) and "{title}" in str(exc.value)

    def test_only_the_title_gives_ground_to_max_name(self):
        long_title = "z" * 300
        name = naming.build_name(self._session(title=long_title),
                                 callsign="0007_Claude_Opus5", max_name=90)
        assert len(name) <= 90
        assert name.startswith("0007_Claude_Opus5 -- ")
        assert name.endswith(" -- claude-11111111-2222-3333-4444-555555555555")   # the id is never what gets trimmed

    def test_the_identity_is_never_trimmed_to_fit(self):
        """Roy chose full session ids, so a max_name smaller than the identity is
        unsatisfiable. Correctness wins: the id survives and the caller is warned."""
        s = self._session(title="z" * 200)
        s.model = "claude-opus-5"
        name = naming.build_name(s, max_name=40)
        assert name.endswith("claude-11111111-2222-3333-4444-555555555555")
        assert len(name) > 40          # exceeded on purpose, not silently truncated

    def test_short_id_strips_the_agent_prefix(self):
        assert naming.short_id("agent-a109fa34123bba393") == "a109fa34"
        assert naming.short_id(CLAUDE_SESSION_ID) == "11111111"
        assert naming.short_id(CLAUDE_SESSION_ID, tail=True) == "55555555"

    def test_env_var_supplies_the_default_template(self, monkeypatch):
        monkeypatch.setenv("XEXPORT_NAME_TEMPLATE", "{source}-{title}")
        assert naming.build_name(self._session()) == "claude-My chat"


# ============================================================================ layout
class TestLayout:
    def test_html_lands_under_the_html_subdir(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        r = _run("current", "--session-id", CLAUDE_SESSION_ID,
                 "--format", "html", "--out", str(out))
        assert r.exit_code == 0, r.output
        folders = list((out / "html").iterdir())
        assert len(folders) == 1
        assert (folders[0] / "index.html").is_file()
        assert not list(out.glob("*/index.html"))   # nothing left at the top level

    def test_both_puts_md_at_the_root_and_html_below(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        r = _run("current", "--session-id", CLAUDE_SESSION_ID,
                 "--format", "both", "--out", str(out))
        assert r.exit_code == 0, r.output
        assert len(list(out.glob("*.md"))) == 1          # findable, not buried
        assert list((out / "html").glob("*/index.html"))
        assert not list((out / "html").glob("*/*.md"))   # no longer nested inside

    def test_empty_html_subdir_restores_the_flat_layout(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        r = _run("current", "--session-id", CLAUDE_SESSION_ID, "--format", "html",
                 "--html-subdir", "", "--out", str(out))
        assert r.exit_code == 0, r.output
        assert list(out.glob("*/index.html"))
        assert not (out / "html").exists()


# ============================================================================ append
class TestAppend:
    def test_append_adds_only_the_new_turns(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        assert _export_md(out).exit_code == 0
        md = next(out.glob("*.md"))
        first = md.read_text(encoding="utf-8")

        _append_entries(claude_store, _turn("Third prompt", "Third answer"))
        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output

        second = md.read_text(encoding="utf-8")
        assert second.startswith(first)              # nothing already written was touched
        assert second.count("Third prompt") == 1
        assert second.count("Great, thanks!") == 1   # earlier turn not re-rendered
        assert "## ➕ Addendum 2" in second
        assert len(list(out.glob("*.md"))) == 1      # one file, not two

    def test_append_with_nothing_new_is_a_no_op(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        assert _export_md(out).exit_code == 0
        md = next(out.glob("*.md"))
        before, mtime = md.read_text(encoding="utf-8"), md.stat().st_mtime

        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert "Up to date" in r.output
        assert md.read_text(encoding="utf-8") == before
        assert md.stat().st_mtime == mtime   # the autosave hook runs this every turn

    def test_append_creates_the_file_when_none_exists(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert "no previous export" in r.output
        assert len(list(out.glob("*.md"))) == 1

    def test_a_toolonly_delta_is_counted_in_messages_not_prompts(self, claude_store,
                                                                 tmp_path):
        """Found in live testing: a long turn of tool work appends real content but
        no new user prompt, and the header read "0 new prompts" — wrong-sounding and
        uninformative. The unit switches to messages when there is no prompt."""
        out = tmp_path / "exports"
        _export_md(out)
        _append_entries(claude_store, [
            {"type": "assistant", "sessionId": CLAUDE_SESSION_ID,
             "timestamp": "2026-07-17T11:30:00.000Z",
             "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "id": "toolu_9", "name": "Bash",
                  "input": {"command": "pytest", "description": "Run tests"}}]}},
        ])
        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        body = next(out.glob("*.md")).read_text(encoding="utf-8")
        assert "1 new message" in body
        assert "0 new prompts" not in body

    def test_seamless_appends_without_a_banner(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        _export_md(out)
        _append_entries(claude_store, _turn("Fourth prompt", "Fourth answer"))
        r = _export_md(out, "--mode", "append", "--seamless")
        assert r.exit_code == 0, r.output
        body = next(out.glob("*.md")).read_text(encoding="utf-8")
        assert "Fourth prompt" in body
        assert "Addendum" not in body

    def test_append_survives_a_retitle_and_notes_it(self, claude_store, tmp_path):
        """Titles are rewritten mid-chat, so lookup is by session id, not by name."""
        out = tmp_path / "exports"
        _export_md(out)
        original = next(out.glob("*.md"))

        _append_entries(claude_store, [
            {"type": "custom-title", "customTitle": "Renamed Later",
             "sessionId": CLAUDE_SESSION_ID},
            *_turn("Fifth prompt", "Fifth answer"),
        ])
        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert len(list(out.glob("*.md"))) == 1
        assert next(out.glob("*.md")) == original          # not renamed
        assert 'now "Renamed Later"' in original.read_text(encoding="utf-8")

    def test_a_shifted_transcript_refuses_to_append(self, claude_store, tmp_path):
        """Fail safe: a duplicate file is recoverable, a transcript with a hole is not.

        The anchor covers the message the cursor stopped on, so this rewrites THAT
        message. See test_edits_behind_the_cursor_are_not_detected for the limit.
        """
        out = tmp_path / "exports"
        _export_md(out)
        original = next(out.glob("*.md"))

        # Same message count, different content at the cursor -> anchor mismatch.
        entries = claude_entries()
        entries[-2]["message"]["content"][0]["text"] = "A completely different answer"
        _jsonl(_transcript(claude_store), entries)

        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output          # a hook must not fail the turn
        assert "Warning" in r.output
        assert len(list(out.glob("*.md"))) == 2    # fell back to a fresh export
        assert "A completely different answer" not in original.read_text(encoding="utf-8")

    def test_a_truncated_transcript_refuses_to_append(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        _export_md(out)
        _jsonl(_transcript(claude_store), claude_entries()[:3])

        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert "Warning" in r.output
        assert len(list(out.glob("*.md"))) == 2

    def test_edits_behind_the_cursor_are_not_detected(self, claude_store, tmp_path):
        """Documents the limit of a one-message anchor, so nobody assumes more.

        The cursor fingerprints only the message it stopped on. A rewrite *behind*
        that point is invisible — acceptable because every store xexport reads is
        append-only, and the alternative (hashing the whole history on every turn)
        costs more than the failure it would catch.
        """
        out = tmp_path / "exports"
        _export_md(out)
        entries = claude_entries()
        entries[-3]["message"]["content"][0]["text"] = "Silently rewritten earlier turn"
        _jsonl(_transcript(claude_store), entries)

        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert "Up to date" in r.output             # not a warning: by design
        assert len(list(out.glob("*.md"))) == 1

    def test_replace_overwrites_in_place(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        _export_md(out)
        md = next(out.glob("*.md"))
        _append_entries(claude_store, _turn("Sixth prompt", "Sixth answer"))

        r = _export_md(out, "--mode", "replace")
        assert r.exit_code == 0, r.output
        body = md.read_text(encoding="utf-8")
        assert len(list(out.glob("*.md"))) == 1
        assert "Sixth prompt" in body
        assert "Addendum" not in body              # a replace is one clean render

    def test_new_mode_still_snapshots(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        _export_md(out, "--mode", "new")
        _export_md(out, "--mode", "new")
        assert len(list(out.glob("*.md"))) == 2
        assert list(out.glob("* (2).md"))

    def test_the_default_mode_updates_rather_than_forking(self, claude_store, tmp_path):
        """Q8: both the CLI and the skills default to updating an existing export.

        Bug this catches: a default of `new` makes a bare re-run write a second
        " (2)" file, which is the behaviour Q8 explicitly replaced.
        """
        out = tmp_path / "exports"
        _export_md(out)
        _append_entries(claude_store, _turn("Eighth prompt", "Eighth answer"))
        r = _export_md(out)
        assert r.exit_code == 0, r.output
        assert len(list(out.glob("*.md"))) == 1
        assert "Eighth prompt" in next(out.glob("*.md")).read_text(encoding="utf-8")

    def test_html_append_regenerates_in_place(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        _run("current", "--session-id", CLAUDE_SESSION_ID, "--format", "html",
             "--out", str(out))
        _append_entries(claude_store, _turn("Seventh prompt", "Seventh answer"))
        r = _run("current", "--session-id", CLAUDE_SESSION_ID, "--format", "html",
                 "--mode", "append", "--out", str(out))
        assert r.exit_code == 0, r.output
        folders = list((out / "html").iterdir())
        assert len(folders) == 1
        assert "Seventh prompt" in (folders[0] / "index.html").read_text(encoding="utf-8")


class TestAppendSafety:
    """The one property that matters: an append never drops or duplicates content."""

    def test_html_append_refuses_a_mismatch_instead_of_overwriting(self, claude_store,
                                                                   tmp_path):
        """REGRESSION: _export_html treated only 'unsupported' as fatal, so a
        'mismatch' fell through to a full re-render **in place** — overwriting
        index.html and unlinking the now-surplus page-NNN.html files. An export of a
        longer transcript was destroyed with no warning and exit 0, while the Markdown
        path refused in the identical situation."""
        out = tmp_path / "exports"
        for i in range(6):
            _append_entries(claude_store, _turn(f"Prompt {i}", "x" * 50))
        _run("current", "--session-id", CLAUDE_SESSION_ID, "--format", "html",
             "--out", str(out))
        folder = next((out / "html").iterdir())
        pages_before = sorted(p.name for p in folder.glob("page-*.html"))
        assert len(pages_before) >= 2, "need multi-page output for this test"

        _jsonl(_transcript(claude_store), claude_entries()[:3])   # simulate a rewrite

        r = _run("current", "--session-id", CLAUDE_SESSION_ID, "--format", "html",
                 "--mode", "append", "--out", str(out))
        assert r.exit_code == 0, r.output
        assert "Warning" in r.output
        assert sorted(p.name for p in folder.glob("page-*.html")) == pages_before
        assert len(list((out / "html").iterdir())) == 2   # fresh export beside it

    def test_a_mismatch_forks_once_not_once_per_run(self, claude_store, tmp_path):
        """REGRESSION: _best ranked by message count, so the stale pre-shortening
        export always won selection, always failed validation, and forced another
        ' (n)' file. Under the recommended per-turn Stop hook that is one new file
        every assistant turn — the exact opposite of the point of this change."""
        out = tmp_path / "exports"
        _export_md(out)
        _jsonl(_transcript(claude_store), claude_entries()[:3])

        for _ in range(4):
            r = _export_md(out, "--mode", "append")
            assert r.exit_code == 0, r.output
            _append_entries(claude_store, _turn("more", "more"))

        assert len(list(out.glob("*.md"))) == 2   # the stale one + one live one

    def test_changing_render_options_refuses_to_append(self, claude_store, tmp_path):
        """REGRESSION: --brief skips tool and thinking blocks, so a delta of pure tool
        traffic rendered to nothing while the cursor still advanced past it. Those
        turns were then skipped permanently, including by a later full append."""
        out = tmp_path / "exports"
        assert _export_md(out, "--brief").exit_code == 0
        _append_entries(claude_store, _turn("New prompt", "New answer"))

        r = _export_md(out, "--mode", "append")     # full render over a --brief file
        assert r.exit_code == 0, r.output
        assert "Warning" in r.output
        assert "render options" in r.output
        assert len(list(out.glob("*.md"))) == 2

    def test_a_delta_that_renders_to_nothing_does_not_move_the_cursor(self,
                                                                      claude_store,
                                                                      tmp_path):
        out = tmp_path / "exports"
        _export_md(out, "--brief")
        md = next(out.glob("*.md"))
        before = md.read_text(encoding="utf-8")
        _append_entries(claude_store, [
            {"type": "assistant", "sessionId": CLAUDE_SESSION_ID,
             "timestamp": "2026-07-17T11:40:00.000Z",
             "message": {"role": "assistant", "content": [
                 {"type": "tool_use", "id": "t1", "name": "Bash",
                  "input": {"command": "ls"}}]}},
        ])
        r = _export_md(out, "--brief", "--mode", "append")
        assert r.exit_code == 0, r.output
        assert md.read_text(encoding="utf-8") == before

        # …and the skipped turns are still reachable once a real prompt arrives.
        _append_entries(claude_store, _turn("Real prompt", "Real answer"))
        assert _export_md(out, "--brief", "--mode", "append").exit_code == 0
        assert "Real prompt" in md.read_text(encoding="utf-8")

    def test_an_interrupted_append_is_not_replayed_onto(self, claude_store, tmp_path):
        """A crash after the delta but before its marker leaves the cursor pointing at
        the old position; appending again would write the same turns twice."""
        out = tmp_path / "exports"
        _export_md(out)
        md = next(out.glob("*.md"))
        with open(md, "a", encoding="utf-8") as f:
            f.write("\n## 👤 User\n\nhalf-written delta from a crashed run\n")
        _append_entries(claude_store, _turn("Next", "Next"))

        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert "Warning" in r.output
        assert md.read_text(encoding="utf-8").count("half-written delta") == 1
        assert len(list(out.glob("*.md"))) == 2

    def test_a_marker_without_an_anchor_fails_closed(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        _export_md(out)
        md = next(out.glob("*.md"))
        with open(md, "a", encoding="utf-8") as f:
            f.write('<!-- xexport-cursor v1 {"v":1,"session_id":"%s","messages":3} -->\n'
                    % CLAUDE_SESSION_ID)
        _append_entries(claude_store, _turn("Next", "Next"))

        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert "Warning" in r.output

    def test_append_conflicting_with_an_explicit_mode_is_an_error(self, claude_store,
                                                                  tmp_path):
        out = tmp_path / "exports"
        r = _export_md(out, "--mode", "new", "--append")
        assert r.exit_code != 0
        assert "conflicts" in r.output

    def test_a_title_containing_an_html_comment_close(self, claude_store, tmp_path):
        """A title with '-->' in it would close the marker comment early, and every
        later read would see a truncated marker."""
        out = tmp_path / "exports"
        assert _export_md(out, "--name", "weird --> title }").exit_code == 0
        md = next(out.glob("*.md"))
        data = cursors.read_md_marker(md)
        assert data is not None and data["session_id"] == CLAUDE_SESSION_ID
        _append_entries(claude_store, _turn("Next", "Next"))
        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert "Warning" not in r.output
        assert len(list(out.glob("*.md"))) == 1


class TestCursorRecord:
    def test_marker_is_the_last_line_and_is_readable(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        _export_md(out)
        md = next(out.glob("*.md"))
        data = cursors.read_md_marker(md)
        assert data["session_id"] == CLAUDE_SESSION_ID
        assert data["run"] == 1
        assert data["messages"] > 0
        assert md.read_text(encoding="utf-8").rstrip().endswith("-->")

    def test_a_future_marker_version_is_not_guessed_at(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        _export_md(out)
        md = next(out.glob("*.md"))
        with open(md, "a", encoding="utf-8") as f:
            f.write('<!-- xexport-cursor v99 {"v":99,"session_id":"%s","messages":1} -->\n'
                    % CLAUDE_SESSION_ID)
        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert "Warning" in r.output
        assert len(list(out.glob("*.md"))) == 2


# ========================================================================= subagents
class TestSubagents:
    def test_subagent_transcript_parses(self, subagent_store):
        """REGRESSION: every line of a subagent transcript carries isSidechain=true,
        and the filter that keeps a PARENT transcript clean was silently emptying
        subagent exports — parse_file returned a Session with zero messages."""
        path = (subagent_store / "projects" / "C--proj" / CLAUDE_SESSION_ID
                / "subagents" / f"{SUBAGENT_ID}.jsonl")
        session = claude.parse_file(path)
        assert len(session.messages) == 2
        assert session.is_subagent
        assert session.parent_session_id == CLAUDE_SESSION_ID

    def test_parent_transcript_still_drops_sidechain_lines(self, claude_store):
        """The sidechain filter must only lift for the subagent's OWN file."""
        session = claude.parse_file(_transcript(claude_store))
        assert not session.is_subagent
        rendered = " ".join(b.text for m in session.messages for b in m.blocks)
        assert "subagent internal prompt" not in rendered

    def test_title_comes_from_the_meta_sidecar(self, subagent_store):
        path = (subagent_store / "projects" / "C--proj" / CLAUDE_SESSION_ID
                / "subagents" / f"{SUBAGENT_ID}.jsonl")
        assert claude.parse_file(path).title == "Summarize existing canonical skills"

    def test_subagents_are_findable(self, subagent_store):
        """REGRESSION: list_sessions globbed one level deep and filtered on a UUID
        stem, and find_session globbed */<id>.jsonl — so `agent-*` transcripts two
        levels down were unreachable by either route."""
        assert claude.find_session(SUBAGENT_ID) is not None
        assert claude.find_session("a109fa34") is not None
        infos = claude.list_subagents(CLAUDE_SESSION_ID)
        assert [i.session_id for i in infos] == [SUBAGENT_ID]

    def test_subagents_land_beside_the_main_exports_and_are_self_marking(
            self, subagent_store, tmp_path):
        """Roy 2026-09-04: one place to look. The name has to carry the marking
        that the folder used to."""
        out = tmp_path / "exports"
        r = _run("subagents", "--session-id", CLAUDE_SESSION_ID,
                 "--format", "md", "--out", str(out))
        assert r.exit_code == 0, r.output
        assert not (out / "subagents").exists()
        written = list(out.glob("*.md"))
        assert len(written) == 1
        assert "Summarize existing canonical skills" in written[0].name
        assert "claude-agent-" in written[0].name   # marks it without a folder

    def test_auto_callsign_prefers_the_subagents_own_name(self, subagent_store,
                                                          tmp_path, monkeypatch):
        """REGRESSION: --callsign auto asked AgentNamer first and short-circuited on
        success. A subagent shares its PARENT's session id, so whoami answered with
        the parent's callsign and every subagent export was stamped with it. The
        earlier version of this test hid the bug by pointing XEXPORT_AGENTNAMER at a
        file that did not exist, so whoami never ran at all."""
        fake = tmp_path / "fake_claim.py"
        fake.write_text(
            "print('CALLSIGN 0007_Claude_Opus5  handle=0007_claude_opus5')\n",
            encoding="utf-8",
        )
        monkeypatch.setenv("XEXPORT_AGENTNAMER", str(fake))
        out = tmp_path / "exports"
        r = _run("subagents", "--session-id", CLAUDE_SESSION_ID, "--format", "md",
                 "--callsign", "auto", "--out", str(out))
        assert r.exit_code == 0, r.output
        written = list(out.glob("*.md"))
        assert written[0].name.startswith("0009_Claude_Sonnet5_Sub_Explorer -- ")
        assert "0007_Claude_Opus5" not in written[0].name   # the parent's callsign

    def test_auto_callsign_uses_whoami_for_a_normal_session(self, claude_store,
                                                            tmp_path, monkeypatch):
        fake = tmp_path / "fake_claim.py"
        fake.write_text("print('CALLSIGN 0007_Claude_Opus5  handle=x')\n",
                        encoding="utf-8")
        monkeypatch.setenv("XEXPORT_AGENTNAMER", str(fake))
        out = tmp_path / "exports"
        assert _export_md(out, "--callsign", "auto").exit_code == 0
        assert next(out.glob("*.md")).name.startswith("0007_Claude_Opus5 -- ")

    def test_from_hook_ignores_a_non_subagent_transcript_path(self, subagent_store,
                                                              tmp_path):
        """REGRESSION: `subagents --from-hook` trusted payload transcript_path
        blindly. SubagentStop may hand over the MAIN transcript, which meant the
        parent was exported into the Stop hook's own file while the command reported
        nothing — a completely silent wrong result."""
        out = tmp_path / "exports"
        payload = json.dumps({"transcript_path": str(_transcript(subagent_store)),
                              "session_id": CLAUDE_SESSION_ID})
        r = CliRunner().invoke(
            main, ["subagents", "--from-hook", "--format", "md", "--out", str(out)],
            input=payload)
        assert r.exit_code == 0, r.output
        written = list(out.glob("*.md"))
        assert len(written) == 1
        assert "Summarize existing canonical skills" in written[0].name
        assert all("agent-" in w.name for w in written)  # parent NOT exported

    def test_subagents_are_hidden_from_the_default_listing(self, subagent_store):
        plain = CliRunner().invoke(main, ["list", "--source", "claude"])
        withsub = CliRunner().invoke(main, ["list", "--source", "claude", "--subagents"])
        assert "Summarize existing canonical skills" not in plain.output
        assert "Summarize existing canonical skills" in withsub.output
