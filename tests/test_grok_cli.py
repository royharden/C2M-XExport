"""Grok CLI wiring: source sniffing, current-session resolution, listing, subagents,
and how the parser treats system entries.

tests/test_parsers.py::TestGrokParser covers jsonl -> IR. This file covers what the
CLI does with that parser, which had no dedicated tests when Grok was added (0.2.3).
Every id and path here is synthetic.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from urllib.parse import quote

import pytest
from click.testing import CliRunner

from xexport import detect
from xexport.cli import _sniff_source, main
from xexport.model import RAW
from xexport.sources import grok

from conftest import GROK_SESSION_ID, _jsonl

PARENT = GROK_SESSION_ID
CHILD = "01a00000-0000-7000-8000-000000000002"
STRANGER = "01a00000-0000-7000-8000-000000000003"
OLDER = "01a00000-0000-7000-8000-000000000004"
FIXTURE_CWD = "C:\\proj"          # the folder grok_store files its session under


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch):
    """Nothing the host shell exports may leak into a session-resolution test."""
    for key in ("CLAUDE_CODE_SESSION_ID", "CODEX_THREAD_ID", "CODEX_SESSION_ID",
                "CURSOR_CONVERSATION_ID", "GROK_SESSION_ID", "XEXPORT_CALLSIGN",
                "XEXPORT_AGENTNAMER"):
        monkeypatch.delenv(key, raising=False)


def _run(*args):
    return CliRunner().invoke(main, list(args))


def _add_session(home: Path, session_id: str, prompt: str, *, cwd: str = FIXTURE_CWD,
                 age_seconds: float = 0.0) -> Path:
    """Write a minimal Grok session; `age_seconds` pushes its mtime into the past."""
    folder = home / "sessions" / quote(cwd, safe="") / session_id
    path = _jsonl(folder / "chat_history.jsonl", [
        {"type": "user", "content": [{"type": "text", "text": prompt}]},
        {"type": "assistant", "content": f"Answer to: {prompt}", "model_id": "grok-4.6"},
    ])
    if age_seconds:
        then = time.time() - age_seconds
        os.utime(path, (then, then))
    return path


def _markdown(out: Path) -> list[Path]:
    return sorted(out.glob("*.md"))


class TestSniffSource:
    def test_chat_history_jsonl_is_grok(self, grok_store):
        # what_bug_this_catches: `xexport <path>` on a Grok transcript fell through to
        # the Claude parser, which found no Claude entries and exported an empty file.
        assert _sniff_source(grok.find_session(PARENT)) == "grok"

    def test_a_path_under_dot_grok_is_grok(self, tmp_path):
        assert _sniff_source(tmp_path / ".grok" / "sessions" / "x" / "y.jsonl") == "grok"

    def test_export_by_path_autodetects_grok(self, grok_store, tmp_path):
        out = tmp_path / "exports"
        r = _run("export", str(grok.find_session(PARENT)), "--format", "md", "--out", str(out))
        assert r.exit_code == 0, r.output
        (written,) = _markdown(out)
        text = written.read_text(encoding="utf-8")
        assert "Please export this Grok chat." in text
        assert "Grok CLI" in text and PARENT in text


class TestCurrent:
    def test_env_session_id_selects_the_grok_session(self, grok_store, tmp_path, monkeypatch):
        # what_bug_this_catches: without the GROK_SESSION_ID branch, `current` in a Grok
        # agent shell fell back to the newest session for the cwd, which is another
        # task's when several share a working directory.
        _add_session(grok_store, STRANGER, "A different task entirely")
        monkeypatch.setenv("GROK_SESSION_ID", PARENT)
        out = tmp_path / "exports"
        r = _run("current", "--format", "md", "--out", str(out))
        assert r.exit_code == 0, r.output
        (written,) = _markdown(out)
        text = written.read_text(encoding="utf-8")
        assert "Please export this Grok chat." in text
        assert "A different task entirely" not in text

    def test_explicit_session_id(self, grok_store, tmp_path):
        out = tmp_path / "exports"
        r = _run("current", "--source", "grok", "--session-id", PARENT,
                 "--format", "md", "--out", str(out))
        assert r.exit_code == 0, r.output
        assert len(_markdown(out)) == 1

    def test_unknown_session_id_is_an_error_not_an_empty_export(self, grok_store, tmp_path):
        out = tmp_path / "exports"
        r = _run("current", "--source", "grok", "--session-id", STRANGER,
                 "--format", "md", "--out", str(out))
        assert r.exit_code != 0
        assert not out.exists() or not _markdown(out)

    def test_cwd_fallback_picks_the_newest_session_and_says_it_guessed(
            self, grok_store, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cwd = str(Path.cwd())                      # whatever spelling the OS reports
        _add_session(grok_store, OLDER, "Older chat", cwd=cwd, age_seconds=3600)
        _add_session(grok_store, STRANGER, "Newer chat", cwd=cwd)
        out = tmp_path / "exports"
        r = _run("current", "--source", "grok", "--format", "md", "--out", str(out))
        assert r.exit_code == 0, r.output
        (written,) = _markdown(out)
        text = written.read_text(encoding="utf-8")
        assert "Newer chat" in text and "Older chat" not in text
        assert "could not identify the active session exactly" in r.output

    def test_detect_grok_prefers_a_confirmed_env_id(self, grok_store, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        cwd = str(Path.cwd())
        older = _add_session(grok_store, OLDER, "Older chat", cwd=cwd, age_seconds=3600)
        _add_session(grok_store, STRANGER, "Newer chat", cwd=cwd)
        monkeypatch.setenv("GROK_SESSION_ID", OLDER)
        assert detect.detect_grok(cwd) == (older, True)

    def test_detect_grok_finds_nothing_for_an_unknown_cwd(self, grok_store):
        assert detect.detect_grok("C:\\nowhere") == (None, False)


class TestList:
    def test_list_shows_grok_sessions(self, grok_store):
        r = _run("list", "--source", "grok")
        assert r.exit_code == 0, r.output
        assert "grok" in r.output
        assert "Grok export fixture" in r.output      # the summary.json title
        assert f"id: {PARENT}" in r.output


class TestSubagents:
    """Grok children are sibling session folders, linked only by the AgentNamer registry."""

    def _registry(self, root: Path) -> None:
        registry = root / ".agent-registry"
        (registry / "ids").mkdir(parents=True)
        (registry / "config.json").write_text("{}", encoding="utf-8")
        records = {
            "0001": {"id": "0001", "name": "0001_Grok_Parent", "session_id": PARENT,
                     "sub": False, "status": "active"},
            "0002": {"id": "0002", "name": "0002_Grok_Sub_Reviewer", "session_id": CHILD,
                     "sub": True, "parent_id": "0001", "status": "active"},
            "0003": {"id": "0003", "name": "0003_Grok_Sub_Other", "session_id": STRANGER,
                     "sub": True, "parent_id": "0009", "status": "active"},
        }
        for rid, record in records.items():
            (registry / "ids" / f"{rid}.json").write_text(json.dumps(record), encoding="utf-8")

    @pytest.fixture
    def family(self, grok_store, tmp_path, monkeypatch):
        _add_session(grok_store, CHILD, "Review the parser")
        _add_session(grok_store, STRANGER, "Somebody else's subagent")
        self._registry(tmp_path)
        monkeypatch.chdir(tmp_path)
        return tmp_path

    def test_exports_only_children_registered_to_the_parent(self, family):
        out = family / "exports"
        r = _run("subagents", "--source", "grok", "--session-id", PARENT,
                 "--format", "md", "--out", str(out))
        assert r.exit_code == 0, r.output
        (written,) = _markdown(out)
        assert CHILD in written.name
        text = written.read_text(encoding="utf-8")
        assert "Review the parser" in text
        assert "Somebody else's subagent" not in text

    def test_defaults_to_the_grok_session_id_from_the_environment(self, family, monkeypatch):
        monkeypatch.setenv("GROK_SESSION_ID", PARENT)
        out = family / "exports"
        r = _run("subagents", "--source", "grok", "--format", "md", "--out", str(out))
        assert r.exit_code == 0, r.output
        assert len(_markdown(out)) == 1

    def test_without_a_registry_there_is_nothing_to_export(self, grok_store, tmp_path,
                                                          monkeypatch):
        _add_session(grok_store, CHILD, "Review the parser")
        monkeypatch.chdir(tmp_path)                 # no .agent-registry anywhere above it
        out = tmp_path / "exports"
        r = _run("subagents", "--source", "grok", "--session-id", PARENT,
                 "--format", "md", "--out", str(out))
        assert r.exit_code == 0, r.output
        assert "No subagent transcripts found" in r.output
        assert not out.exists() or not _markdown(out)


class TestSystemEntries:
    """Only the opening system preamble is boilerplate; later system entries are content."""

    def _session(self, home: Path, extra: list[dict]) -> Path:
        entries = [
            {"type": "system", "content": "You are Grok, released by xAI."},
            {"type": "user", "content": [{"type": "text", "text": "Hello"}]},
            {"type": "assistant", "content": "Hi.", "model_id": "grok-4.6"},
            *extra,
        ]
        return _jsonl(home / "sessions" / quote(FIXTURE_CWD, safe="") / PARENT
                      / "chat_history.jsonl", entries)

    def test_leading_preamble_is_skipped(self, tmp_path):
        session = grok.parse_file(self._session(tmp_path, []))
        texts = [b.text for m in session.messages for b in m.blocks]
        assert not any("You are Grok" in t for t in texts)

    def test_a_later_system_entry_is_kept_as_a_raw_block(self, tmp_path):
        # what_bug_this_catches: every `system` entry was dropped, so a mid-conversation
        # system message (a context notice, an injected instruction) vanished from the
        # export without a trace. Only the opening preamble is boilerplate.
        session = grok.parse_file(self._session(
            tmp_path, [{"type": "system", "content": "Context was compacted."}]))
        raw = [b for m in session.messages for b in m.blocks if b.kind == RAW]
        assert len(raw) == 1
        assert raw[0].name == "system" and "Context was compacted." in raw[0].text
