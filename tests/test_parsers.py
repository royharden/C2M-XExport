"""Parser tests: jsonl → IR for both sources."""

from __future__ import annotations

from xexport.model import (
    ASSISTANT_TEXT, RAW, THINKING, TOOL_CALL, TOOL_RESULT, USER_TEXT,
)
from xexport.sources import claude, codex, cursor, grok

from conftest import CLAUDE_SESSION_ID, CODEX_SESSION_ID, CURSOR_SESSION_ID, GROK_SESSION_ID


def _kinds(session):
    return [(m.role, b.kind) for m in session.messages for b in m.blocks]


class TestClaudeParser:
    def test_parses_store_session(self, claude_store):
        path = claude.find_session(CLAUDE_SESSION_ID)
        assert path is not None
        s = claude.parse_file(path)
        assert s.source == "claude"
        assert s.session_id == CLAUDE_SESSION_ID
        assert s.cwd == "C:\\proj"
        assert s.model == "claude-opus-4-8"
        kinds = _kinds(s)
        assert ("user", USER_TEXT) in kinds
        assert ("assistant", THINKING) in kinds
        assert ("assistant", TOOL_CALL) in kinds
        assert ("tool", TOOL_RESULT) in kinds

    def test_custom_title_beats_ai_title(self, claude_store):
        s = claude.parse_file(claude.find_session(CLAUDE_SESSION_ID))
        assert s.title == "My Renamed Chat: v2?"

    def test_sidechain_excluded(self, claude_store):
        s = claude.parse_file(claude.find_session(CLAUDE_SESSION_ID))
        texts = [b.text for m in s.messages for b in m.blocks]
        assert not any("subagent internal prompt" in t for t in texts)

    def test_unknown_line_type_becomes_raw(self, claude_store):
        # what_bug_this_catches: a new Claude Code release adding a line type
        # must degrade to a collapsed RAW block, never crash the export
        s = claude.parse_file(claude.find_session(CLAUDE_SESSION_ID))
        raws = [b for m in s.messages for b in m.blocks if b.kind == RAW]
        assert any(b.name == "mystery-future-type" for b in raws)

    def test_prompt_groups(self, claude_store):
        s = claude.parse_file(claude.find_session(CLAUDE_SESSION_ID))
        groups = s.prompt_groups()
        assert len(groups) == 2  # two real user prompts; tool results don't split

    def test_listing_uses_custom_title(self, claude_store):
        infos = claude.list_sessions()
        assert len(infos) == 1
        assert infos[0].title == "My Renamed Chat: v2?"

    def test_undecodable_bytes_dont_crash(self, tmp_path):
        # what_bug_this_catches: codex-export crashed with UnicodeDecodeError
        # (cp1252) on byte 0x9d; we read utf-8 with errors="replace"
        p = tmp_path / "bad.jsonl"
        line = b'{"type":"user","message":{"role":"user","content":"smart \x9d quote"}}\n'
        p.write_bytes(line)
        s = claude.parse_file(p)
        assert any(b.kind == USER_TEXT for m in s.messages for b in m.blocks)


class TestCodexParser:
    def test_parses_store_session(self, codex_store):
        path = codex.find_session(CODEX_SESSION_ID)
        assert path is not None
        s = codex.parse_file(path)
        assert s.source == "codex"
        assert s.session_id == CODEX_SESSION_ID
        assert s.cwd == "C:\\proj"
        assert s.model == "gpt-5.6-terra"
        assert s.app == "Codex Desktop"

    def test_title_from_session_index(self, codex_store):
        s = codex.parse_file(codex.find_session(CODEX_SESSION_ID))
        assert s.title == "Spreadsheet review chat"

    def test_system_noise_filtered(self, codex_store):
        # what_bug_this_catches: <recommended_plugins> and world_state blocks
        # appeared as fake user messages / giant raw dumps in early exports
        s = codex.parse_file(codex.find_session(CODEX_SESSION_ID))
        all_text = " ".join(b.text + b.output for m in s.messages for b in m.blocks)
        assert "recommended_plugins" not in all_text
        assert "agents_md" not in all_text
        user_prompts = [b.text for m in s.messages for b in m.blocks
                        if b.kind == USER_TEXT]
        assert user_prompts == ["Review my spreadsheet"]

    def test_tool_call_and_output_unwrapped(self, codex_store):
        s = codex.parse_file(codex.find_session(CODEX_SESSION_ID))
        calls = [b for m in s.messages for b in m.blocks if b.kind == TOOL_CALL]
        results = [b for m in s.messages for b in m.blocks if b.kind == TOOL_RESULT]
        assert calls and calls[0].name == "shell"
        # {"output": "..."} wrappers get unwrapped to the inner text
        assert results and results[0].output == "book.xlsx"

    def test_event_msg_skipped(self, codex_store):
        s = codex.parse_file(codex.find_session(CODEX_SESSION_ID))
        texts = [b.text for m in s.messages for b in m.blocks]
        assert not any("dup" == t for t in texts)

    def test_reasoning_becomes_thinking(self, codex_store):
        s = codex.parse_file(codex.find_session(CODEX_SESSION_ID))
        thinking = [b for m in s.messages for b in m.blocks if b.kind == THINKING]
        assert thinking and "Reading the workbook" in thinking[0].text

    def test_assistant_text(self, codex_store):
        s = codex.parse_file(codex.find_session(CODEX_SESSION_ID))
        answers = [b for m in s.messages for b in m.blocks
                   if b.kind == ASSISTANT_TEXT]
        assert answers and "3 tabs" in answers[0].text


class TestCursorParser:
    def test_parses_store_session(self, cursor_store):
        path = cursor.find_session(CURSOR_SESSION_ID)
        assert path is not None
        s = cursor.parse_file(path)
        assert s.source == "cursor"
        assert s.session_id == CURSOR_SESSION_ID
        assert s.app == "Cursor"
        kinds = _kinds(s)
        assert ("user", USER_TEXT) in kinds
        assert ("assistant", TOOL_CALL) in kinds
        assert ("tool", TOOL_RESULT) in kinds
        assert ("assistant", ASSISTANT_TEXT) in kinds

    def test_strips_user_query_wrapper(self, cursor_store):
        # what_bug_this_catches: Cursor harness wraps prompts in <user_query>
        # and timestamps; those must not appear as transcript noise
        s = cursor.parse_file(cursor.find_session(CURSOR_SESSION_ID))
        prompts = [b.text for m in s.messages for b in m.blocks if b.kind == USER_TEXT]
        assert prompts[0] == "Export this Cursor chat as Markdown please."
        assert "user_query" not in prompts[0]
        assert "timestamp" not in prompts[0].lower()

    def test_title_from_first_prompt(self, cursor_store):
        s = cursor.parse_file(cursor.find_session(CURSOR_SESSION_ID))
        assert s.title == "Export this Cursor chat as Markdown please"

    def test_listing_skips_subagents(self, cursor_store):
        infos = cursor.list_sessions()
        assert len(infos) == 1
        assert infos[0].session_id == CURSOR_SESSION_ID
        assert "subagent" not in infos[0].title.lower()

    def test_encode_project_dir(self):
        assert (
            cursor.encode_project_dir(r"C:\Users\Roy Harden\OneDrive\PJ-OD\skills")
            == "c-Users-Roy-Harden-OneDrive-PJ-OD-skills"
        )

    def test_prompt_groups(self, cursor_store):
        s = cursor.parse_file(cursor.find_session(CURSOR_SESSION_ID))
        assert len(s.prompt_groups()) == 2


class TestGrokParser:
    def test_parses_store_session(self, grok_store):
        # what_bug_this_catches: Grok CLI sessions lived in ~/.grok/sessions
        # with no xexport source, so PracticeHub Grok AgentOps never produced
        # .chatexports files named with Grok callsigns.
        path = grok.find_session(GROK_SESSION_ID)
        assert path is not None
        s = grok.parse_file(path)
        assert s.source == "grok"
        assert s.session_id == GROK_SESSION_ID
        assert s.app == "Grok CLI"
        assert s.cwd == "C:\\proj"
        assert s.model == "grok-4.6"
        assert s.title == "Grok export fixture"
        kinds = _kinds(s)
        assert ("user", USER_TEXT) in kinds
        assert ("assistant", THINKING) in kinds
        assert ("assistant", TOOL_CALL) in kinds
        assert ("tool", TOOL_RESULT) in kinds
        assert ("assistant", ASSISTANT_TEXT) in kinds

    def test_skips_system_preamble(self, grok_store):
        s = grok.parse_file(grok.find_session(GROK_SESSION_ID))
        texts = [b.text for m in s.messages for b in m.blocks]
        assert not any(t.startswith("You are Grok 4.6") for t in texts)

    def test_listing(self, grok_store):
        infos = grok.list_sessions()
        assert len(infos) == 1
        assert infos[0].session_id == GROK_SESSION_ID
        assert infos[0].title == "Grok export fixture"

    def test_null_user_text_item_does_not_crash(self, grok_store):
        # what_bug_this_catches: a user content item {"type": "text", "text": null}
        # raised AttributeError in the user branch (the assistant branch already
        # guarded it), turning an odd entry into a failed export instead of a
        # degraded one.
        import json
        path = grok.find_session(GROK_SESSION_ID)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"type": "user", "content": [{"type": "text", "text": None}]}) + "\n")
        assert grok.parse_file(path).session_id == GROK_SESSION_ID

    def test_title_falls_back_to_first_prompt_without_summary_title(self, grok_store):
        # what_bug_this_catches: _summary_title returned info.id when summary.json had
        # no generated_title/session_summary (a fresh session), so the first-prompt
        # fallback never ran and the export was titled with the session UUID.
        import json
        path = grok.find_session(GROK_SESSION_ID)
        (path.parent / "summary.json").write_text(
            json.dumps({"info": {"id": GROK_SESSION_ID, "cwd": "C:\\proj"}}), encoding="utf-8")
        assert grok.parse_file(path).title == "Please export this Grok chat."
