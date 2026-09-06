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

from xexport import agentnamer, cursors, naming
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
    def test_a_refresh_holds_the_whole_conversation_in_one_file(self, claude_store,
                                                                tmp_path):
        """The requested outcome: one file per chat, always current.

        0.2.0 appended a delta under an "Addendum" banner; the merged design
        re-renders the whole document and swaps it in atomically. The visible seam
        is gone on purpose - a re-render is what makes an edited or compacted
        transcript come out right, which an append can only refuse.
        """
        out = tmp_path / "exports"
        _export_md(out)
        _append_entries(claude_store, _turn("Third prompt", "Third answer"))

        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert len(list(out.glob("*.md"))) == 1        # one file, not two
        body = next(out.glob("*.md")).read_text(encoding="utf-8")
        assert "Third prompt" in body and "Third answer" in body
        assert "please fix the bug" in body            # earlier turns still present
        assert body.count("- **Session:**") == 1       # rendered once, not doubled

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

    def test_a_turn_of_pure_tool_work_still_refreshes(self, claude_store, tmp_path):
        """A long turn of tool work adds real content but no new user prompt.

        Under the old append this produced a header reading "0 new prompts"; under
        re-render it simply has to land in the file rather than being mistaken for
        "nothing happened".
        """
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
        assert "Up to date" not in r.output
        assert "Run tests" in next(out.glob("*.md")).read_text(encoding="utf-8")

    def test_a_refresh_renames_the_file_to_follow_the_chat_title(self, claude_store,
                                                                 tmp_path):
        """Roy Q3: the export follows the current chat title.

        Identity lives in the id suffix, so the file is still found after it moves.
        """
        out = tmp_path / "exports"
        _export_md(out)
        original = next(out.glob("*.md"))
        assert "My Renamed Chat v2" in original.name

        _append_entries(claude_store, [
            {"type": "custom-title", "customTitle": "Renamed Later",
             "sessionId": CLAUDE_SESSION_ID},
            *_turn("Fifth prompt", "Fifth answer"),
        ])
        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output

        written = list(out.glob("*.md"))
        assert len(written) == 1                       # renamed, not duplicated
        assert not original.exists()
        assert "Renamed Later" in written[0].name
        # identity survived the move, which is how the next run still finds it
        assert written[0].stem.endswith("claude-" + CLAUDE_SESSION_ID)
        assert "Fifth prompt" in written[0].read_text(encoding="utf-8")

    def test_an_edited_turn_is_re_rendered_rather_than_refused(self, claude_store,
                                                               tmp_path):
        """The reason the merge took re-render over append.

        An append could only detect that a transcript had been rewritten and refuse,
        leaving a stale export plus a duplicate. Re-rendering just produces the
        correct document - same message count, different content, one file.
        """
        out = tmp_path / "exports"
        _export_md(out)
        original = next(out.glob("*.md"))
        assert "You're welcome." in original.read_text(encoding="utf-8")

        entries = claude_entries()
        entries[-2]["message"]["content"][0]["text"] = "A completely different answer"
        _jsonl(_transcript(claude_store), entries)

        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert "Warning" not in r.output
        assert len(list(out.glob("*.md"))) == 1
        body = next(out.glob("*.md")).read_text(encoding="utf-8")
        assert "A completely different answer" in body
        assert "You're welcome." not in body

    def test_a_truncated_transcript_refuses_to_append(self, claude_store, tmp_path):
        out = tmp_path / "exports"
        _export_md(out)
        _jsonl(_transcript(claude_store), claude_entries()[:3])

        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert "Warning" in r.output
        assert len(list(out.glob("*.md"))) == 2

    def test_an_edit_that_leaves_the_message_count_alone_is_still_caught(
            self, claude_store, tmp_path):
        """what_bug_this_catches: keying "up to date" on a message count.

        0.2.0 fingerprinted only the boundary message, so a rewrite behind it was
        invisible and the export silently kept the old text. The content digest
        covers every message and block, so an unchanged count is no longer mistaken
        for nothing having happened.
        """
        out = tmp_path / "exports"
        _export_md(out)
        entries = claude_entries()
        entries[-3]["message"]["content"][0]["text"] = "Silently rewritten earlier turn"
        _jsonl(_transcript(claude_store), entries)

        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert "Up to date" not in r.output
        assert len(list(out.glob("*.md"))) == 1
        assert "Silently rewritten earlier turn" in next(
            out.glob("*.md")).read_text(encoding="utf-8")

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

    def test_a_refresh_that_changes_nothing_leaves_the_file_untouched(
            self, claude_store, tmp_path):
        """The autosave hook runs this on every turn, so a no-op must cost nothing.

        Not merely "writes the same bytes": the file is never opened for writing, so
        its mtime does not move and OneDrive has nothing to sync.
        """
        out = tmp_path / "exports"
        assert _export_md(out, "--brief").exit_code == 0
        md = next(out.glob("*.md"))
        before, mtime = md.read_bytes(), md.stat().st_mtime_ns

        r = _export_md(out, "--brief", "--mode", "append")
        assert r.exit_code == 0, r.output
        assert "Up to date" in r.output
        assert md.read_bytes() == before
        assert md.stat().st_mtime_ns == mtime

    def test_a_failed_refresh_leaves_the_previous_export_intact(self, claude_store,
                                                                tmp_path, monkeypatch):
        """what_bug_this_catches: a half-written export.

        An in-place append could be interrupted mid-write and leave a partial delta
        that the next run would either replay or refuse. A refresh renders to a temp
        file in the same directory and swaps it in with os.replace, so a failure
        anywhere leaves the previous complete export exactly as it was.
        """
        out = tmp_path / "exports"
        _export_md(out)
        md = next(out.glob("*.md"))
        before = md.read_bytes()
        _append_entries(claude_store, _turn("Doomed prompt", "Doomed answer"))

        def fail_replace(source, destination):
            raise OSError("simulated publication failure")

        monkeypatch.setattr("xexport.publish.os.replace", fail_replace)
        _export_md(out, "--mode", "append")
        assert md.read_bytes() == before              # nothing was lost
        assert not list(out.glob("*.tmp"))            # and no debris left behind

    def test_a_marker_from_an_unknown_future_version_is_not_overwritten(
            self, claude_store, tmp_path):
        """Fail safe on a marker this build cannot reason about.

        A newer xexport may record things this one does not understand, so refuse
        the file and write beside it rather than rewriting it on a guess.
        """
        out = tmp_path / "exports"
        _export_md(out)
        md = next(out.glob("*.md"))
        md.write_text(md.read_text(encoding="utf-8").replace('{"v":1,', '{"v":99,'),
                      encoding="utf-8")
        _append_entries(claude_store, _turn("Later prompt", "Later answer"))

        r = _export_md(out, "--mode", "append")
        assert r.exit_code == 0, r.output
        assert "Warning" in r.output
        assert len(list(out.glob("*.md"))) == 2
        assert "Later prompt" not in md.read_text(encoding="utf-8")

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

    def test_auto_callsign_reads_a_registry_without_mutating_it(self, tmp_path,
                                                                monkeypatch):
        """--callsign auto resolves a top-level session from AgentNamer's registry.

        xexport is a read-only consumer: it must never init, claim or rewrite a
        registry, so the whole tree is compared byte-for-byte afterwards.
        """
        monkeypatch.delenv("XEXPORT_CALLSIGN", raising=False)
        registry = tmp_path / ".agent-registry"
        (registry / "ids").mkdir(parents=True)
        (registry / "config.json").write_text("{}", encoding="utf-8")
        (registry / "ids" / "0007.json").write_text(json.dumps({
            "name": "0007_Claude_Opus5", "session_id": CLAUDE_SESSION_ID,
            "sub": False, "status": "active",
        }), encoding="utf-8")
        before = {p: p.read_bytes() for p in registry.rglob("*") if p.is_file()}

        assert agentnamer.callsign_from_registry(
            CLAUDE_SESSION_ID, tmp_path) == "0007_Claude_Opus5"
        assert {p: p.read_bytes() for p in registry.rglob("*") if p.is_file()} == before

    def test_auto_callsign_ignores_a_subagent_record_for_a_main_session(self, tmp_path,
                                                                        monkeypatch):
        """A _Sub record must never name the parent's own export."""
        monkeypatch.delenv("XEXPORT_CALLSIGN", raising=False)
        registry = tmp_path / ".agent-registry"
        (registry / "ids").mkdir(parents=True)
        (registry / "config.json").write_text("{}", encoding="utf-8")
        (registry / "ids" / "0009.json").write_text(json.dumps({
            "name": "0009_Claude_Sonnet5_Sub_Explorer", "session_id": CLAUDE_SESSION_ID,
            "sub": True, "status": "active",
        }), encoding="utf-8")
        assert agentnamer.callsign_from_registry(CLAUDE_SESSION_ID, tmp_path) == ""

    def test_auto_callsign_yields_no_prefix_without_a_registry(self, claude_store,
                                                              tmp_path, monkeypatch):
        """Eight of nine project roots have no registry; that must cost nothing."""
        monkeypatch.delenv("XEXPORT_CALLSIGN", raising=False)
        out = tmp_path / "exports"
        assert _export_md(out, "--callsign", "auto").exit_code == 0
        # {agent} falls back to harness+model, so the export still names its agent.
        assert next(out.glob("*.md")).name.startswith("Claude_Opus48 -- ")

    def test_a_callsign_merely_mentioned_is_not_adopted(self, tmp_path, monkeypatch):
        """REGRESSION: a prompt that discusses a callsign is not an assignment.

        Without the stricter assignment pattern, asking an agent to review
        "0007_Claude_Sonnet5_Sub_Reviewer" would stamp that name onto the export.
        """
        monkeypatch.delenv("XEXPORT_CALLSIGN", raising=False)
        transcript = tmp_path / "subagent.jsonl"
        _jsonl(transcript, [{"type": "user", "message": {"role": "user", "content":
               "Discuss this example callsign: 0007_Claude_Sonnet5_Sub_Reviewer"}}])
        assert agentnamer.detect_callsign(
            "child", tmp_path, transcript, subagent=True) == ""

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
