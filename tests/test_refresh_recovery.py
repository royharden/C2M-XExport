"""Recovery after a refusal, and the guards that must not be bypassable.

Both scenarios here were found by adversarial review of the re-render merge, and
both had been reproduced live before the fix. They share one property: the wrong
behaviour is silent, and under the recommended per-turn autosave hook it repeats
on every assistant turn.
"""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from xexport import cursors
from xexport.cli import main

from conftest import CLAUDE_SESSION_ID, _jsonl, claude_entries


def _transcript(store):
    return store / "projects" / "C--proj" / f"{CLAUDE_SESSION_ID}.jsonl"


def _turn(prompt, reply):
    sid = CLAUDE_SESSION_ID
    return [
        {"type": "user", "sessionId": sid, "timestamp": "2026-07-17T11:00:00.000Z",
         "message": {"role": "user", "content": [{"type": "text", "text": prompt}]}},
        {"type": "assistant", "sessionId": sid, "timestamp": "2026-07-17T11:00:05.000Z",
         "message": {"role": "assistant", "content": [{"type": "text", "text": reply}]}},
    ]


def _export(out, *extra, fmt="md"):
    return CliRunner().invoke(main, [
        "current", "--session-id", CLAUDE_SESSION_ID, "--format", fmt,
        "--out", str(out), *extra])


def _grow(store, entries):
    _jsonl(_transcript(store), claude_entries() + entries)


class TestForkOnce:
    """A refusal costs exactly one extra file, no matter how many times it runs."""

    def test_a_shrunk_transcript_forks_once_not_once_per_turn(self, claude_store,
                                                              tmp_path):
        """what_bug_this_catches: looking the existing export up by NAME only.

        The identity-suffix lookup always returns the canonical export and, by
        construction, never a " (2)" companion. So the refused original was
        re-selected every run, refused again, and forked another file -- one per
        assistant turn under the autosave hook, forever, which is the exact failure
        the candidate ranking was written to prevent.
        """
        out = tmp_path / "exports"
        for i in range(6):
            _grow(claude_store, _turn(f"Prompt {i}", f"Answer {i}"))
        assert _export(out).exit_code == 0

        _jsonl(_transcript(claude_store), claude_entries()[:3])   # compaction

        for _ in range(5):
            r = _export(out, "--mode", "append")
            assert r.exit_code == 0, r.output

        written = sorted(p.name for p in out.glob("*.md"))
        assert len(written) == 2, written        # the original + ONE companion

    def test_a_fidelity_flip_forks_once_not_once_per_turn(self, claude_store,
                                                          tmp_path):
        """The likelier trigger: an autosave hook running --brief over a full export.

        Every run finds the canonical full-fidelity export, refuses to downgrade it,
        and writes beside it. Without recovery that never converges.
        """
        out = tmp_path / "exports"
        assert _export(out).exit_code == 0
        for _ in range(4):
            r = _export(out, "--brief", "--mode", "append")
            assert r.exit_code == 0, r.output
            _grow(claude_store, _turn("more", "more"))

        assert len(list(out.glob("*.md"))) == 2

    def test_the_companion_is_the_file_later_runs_refresh(self, claude_store,
                                                          tmp_path):
        """Recovery has to actually land on the companion, not merely stop forking."""
        out = tmp_path / "exports"
        assert _export(out).exit_code == 0
        _jsonl(_transcript(claude_store), claude_entries()[:3])
        assert _export(out, "--mode", "append").exit_code == 0

        original = max(out.glob("*.md"), key=lambda p: p.stat().st_size)
        companion = next(p for p in out.glob("*.md") if p != original)
        assert cursors.read_md_marker(companion).get("forked") is True

        _grow(claude_store, _turn("After the break", "Carried on"))
        assert _export(out, "--mode", "append").exit_code == 0
        assert len(list(out.glob("*.md"))) == 2
        assert "After the break" in companion.read_text(encoding="utf-8")

    def test_html_forks_once_too(self, claude_store, tmp_path):
        """A whole paginated folder per turn is the expensive version of this bug."""
        out = tmp_path / "exports"
        for i in range(6):
            _grow(claude_store, _turn(f"Prompt {i}", "x" * 50))
        assert _export(out, fmt="html").exit_code == 0
        _jsonl(_transcript(claude_store), claude_entries()[:3])

        for _ in range(4):
            r = _export(out, "--mode", "append", fmt="html")
            assert r.exit_code == 0, r.output

        assert len(list((out / "html").iterdir())) == 2


class TestSnapshotsAreNotAdopted:
    """A deliberate --mode new copy is a point-in-time record, not a live document."""

    def test_a_deliberate_snapshot_is_never_adopted_even_by_the_marker_scan(
            self, claude_store, tmp_path):
        """what_bug_this_catches: the marker scan matched " (2)" names.

        With a --name-template that omits {identity} the name lookup never matches,
        so the marker scan runs -- and it happily adopted a snapshot, renamed it onto
        the canonical name, and silently froze the real export.
        """
        out = tmp_path / "exports"
        assert _export(out, "--name-template", "{title}").exit_code == 0
        assert _export(out, "--name-template", "{title}", "--mode", "new").exit_code == 0
        snapshot = next(p for p in out.glob("*.md") if p.name.endswith(" (2).md"))
        frozen = snapshot.read_bytes()

        _grow(claude_store, _turn("Newest turn", "Newest answer"))
        r = _export(out, "--name-template", "{title}", "--mode", "append")
        assert r.exit_code == 0, r.output

        assert snapshot.read_bytes() == frozen        # the snapshot stayed a snapshot
        canonical = out / "My Renamed Chat v2.md"
        assert "Newest turn" in canonical.read_text(encoding="utf-8")


class TestUnverifiableFailsClosed:
    """No marker means no way to tell what a refresh would destroy."""

    def test_an_export_whose_marker_was_lost_is_not_silently_overwritten(
            self, claude_store, tmp_path):
        """what_bug_this_catches: an empty marker validating as "ok".

        The name lookup finds an export whose trailing marker was lost -- a hand
        edit, a formatter, a merge -- and with no marker to compare against, every
        guard was skipped. A compacted transcript then overwrote a long export in
        place, no warning, exit 0.
        """
        out = tmp_path / "exports"
        for i in range(6):
            _grow(claude_store, _turn(f"Prompt {i}", f"Answer {i}"))
        assert _export(out).exit_code == 0
        md = next(out.glob("*.md"))
        body = "\n".join(line for line in md.read_text(encoding="utf-8").splitlines()
                         if "xexport-cursor" not in line)
        md.write_text(body, encoding="utf-8")
        before = md.read_bytes()

        _jsonl(_transcript(claude_store), claude_entries()[:3])
        r = _export(out, "--mode", "append")

        assert r.exit_code == 0, r.output
        assert "Warning" in r.output
        assert md.read_bytes() == before               # untouched
        assert len(list(out.glob("*.md"))) == 2

    def test_notes_appended_past_the_marker_are_not_destroyed(self, claude_store,
                                                              tmp_path):
        """A marker pushed out of reach by the reader's tail window reads as absent,
        which must fail closed rather than overwrite the user's own additions."""
        out = tmp_path / "exports"
        assert _export(out).exit_code == 0
        md = next(out.glob("*.md"))
        md.write_text(md.read_text(encoding="utf-8") + "\n\n" + ("my own notes " * 500),
                      encoding="utf-8")
        before = md.read_bytes()

        _grow(claude_store, _turn("Later", "Later answer"))
        r = _export(out, "--mode", "append")

        assert r.exit_code == 0, r.output
        assert md.read_bytes() == before
        assert "my own notes" in md.read_text(encoding="utf-8")


class TestSubagentCallsign:
    """A subagent receipt must never be labelled with its parent's callsign."""

    @pytest.fixture
    def subagents(self, claude_store):
        folder = (claude_store / "projects" / "C--proj" / CLAUDE_SESSION_ID
                  / "subagents")
        _jsonl(folder / "agent-abc123.jsonl", [
            {"isSidechain": True, "type": "user", "sessionId": CLAUDE_SESSION_ID,
             "timestamp": "2026-07-17T10:30:00.000Z",
             "message": {"role": "user", "content": "Go and read the repo."}},
            {"isSidechain": True, "type": "assistant", "sessionId": CLAUDE_SESSION_ID,
             "timestamp": "2026-07-17T10:30:09.000Z",
             "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "Done."}]}},
        ])
        (folder / "agent-abc123.meta.json").write_text(
            json.dumps({"description": "Read the repo"}), encoding="utf-8")
        return claude_store

    def test_the_env_callsign_never_labels_a_subagent(self, subagents, tmp_path,
                                                      monkeypatch):
        """what_bug_this_catches: XEXPORT_CALLSIGN was read before asking whether
        this was a subagent -- and xexport-auto tells users to set exactly that
        variable. Every subagent receipt was then stamped with the parent's name,
        which misattributes what an agent said."""
        monkeypatch.setenv("XEXPORT_CALLSIGN", "0007_Claude_Opus5")
        out = tmp_path / "exports"
        r = CliRunner().invoke(main, [
            "subagents", "--session-id", CLAUDE_SESSION_ID, "--format", "md",
            "--out", str(out)])
        assert r.exit_code == 0, r.output
        written = [p.name for p in out.glob("*.md")]
        assert written
        assert not any("0007_Claude_Opus5" in n for n in written), written

    def test_an_explicit_callsign_never_labels_a_subagent(self, subagents, tmp_path,
                                                          monkeypatch):
        """`xexport subagents --callsign X` exports many children; one name cannot
        be right for all of them, and it is always the parent's."""
        monkeypatch.delenv("XEXPORT_CALLSIGN", raising=False)
        out = tmp_path / "exports"
        r = CliRunner().invoke(main, [
            "subagents", "--session-id", CLAUDE_SESSION_ID, "--format", "md",
            "--callsign", "0007_Claude_Opus5", "--out", str(out)])
        assert r.exit_code == 0, r.output
        assert not any("0007_Claude_Opus5" in p.name for p in out.glob("*.md"))


class TestSubagentTitleFallback:
    def test_the_parents_assignment_line_does_not_become_the_title(self, claude_store,
                                                                   tmp_path):
        """what_bug_this_catches: a subagent with no .meta.json sidecar took its
        title from its first user message, which AgentNamer requires the parent to
        open with "Your callsign is ... (parent ...). Do not claim ..." -- so every
        such export was titled with assignment boilerplate instead of the task."""
        folder = (claude_store / "projects" / "C--proj" / CLAUDE_SESSION_ID
                  / "subagents")
        _jsonl(folder / "agent-nometa.jsonl", [
            {"isSidechain": True, "type": "user", "sessionId": CLAUDE_SESSION_ID,
             "timestamp": "2026-07-17T10:30:00.000Z",
             "message": {"role": "user", "content":
                         "Your callsign is 0009_Claude_Sonnet5_Sub_Explorer "
                         "(parent 0007). Do not claim another.\n"
                         "Audit the retry logic in the uploader."}},
            {"isSidechain": True, "type": "assistant", "sessionId": CLAUDE_SESSION_ID,
             "timestamp": "2026-07-17T10:30:09.000Z",
             "message": {"role": "assistant",
                         "content": [{"type": "text", "text": "Done."}]}},
        ])   # deliberately NO .meta.json sidecar
        out = tmp_path / "exports"
        r = CliRunner().invoke(main, [
            "subagents", "--session-id", CLAUDE_SESSION_ID, "--format", "md",
            "--out", str(out)])
        assert r.exit_code == 0, r.output
        written = [p.name for p in out.glob("*nometa*.md")]
        assert written, [p.name for p in out.glob("*.md")]
        assert "Audit the retry logic" in written[0]
        assert "callsign" not in written[0].lower()
