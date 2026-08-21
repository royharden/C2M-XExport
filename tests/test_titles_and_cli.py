"""Title sanitization + CLI integration tests."""

from __future__ import annotations

from click.testing import CliRunner

from xexport.cli import main
from xexport.titles import sanitize_title, unique_path

from conftest import CLAUDE_SESSION_ID, CODEX_SESSION_ID, CURSOR_SESSION_ID, _jsonl, codex_entries


class TestSanitize:
    def test_invalid_chars_removed(self):
        assert sanitize_title('a<b>c:d"e/f\\g|h?i*j') == "abcdefghij"

    def test_trailing_dots_and_spaces(self):
        assert sanitize_title("Ends with dot. ") == "Ends with dot"

    def test_reserved_device_names(self):
        assert sanitize_title("CON") == "_CON"
        assert sanitize_title("con.md") == "_con.md"

    def test_length_cap_and_empty(self):
        assert len(sanitize_title("x" * 300)) <= 80
        assert sanitize_title("???") == "session"
        assert sanitize_title("") == "session"

    def test_unique_path_collisions(self, tmp_path):
        first = unique_path(tmp_path, "name", ".md")
        first.write_text("x", encoding="utf-8")
        second = unique_path(tmp_path, "name", ".md")
        assert second.name == "name (2).md"


class TestCli:
    def test_list_shows_both_stores(self, claude_store, codex_store):
        result = CliRunner().invoke(main, ["list"])
        assert result.exit_code == 0, result.output
        assert "My Renamed Chat: v2?" in result.output
        assert "Spreadsheet review chat" in result.output

    def test_export_by_id_both_formats(self, claude_store, codex_store, tmp_path):
        out = tmp_path / "exports"
        result = CliRunner().invoke(
            main, [CODEX_SESSION_ID, "--format", "both", "--out", str(out)])
        assert result.exit_code == 0, result.output
        folder = out / "Spreadsheet review chat"
        assert (folder / "index.html").is_file()
        assert (folder / "Spreadsheet review chat.md").is_file()

    def test_export_md_single_file(self, claude_store, codex_store, tmp_path):
        out = tmp_path / "exports"
        result = CliRunner().invoke(
            main, [CLAUDE_SESSION_ID, "--format", "md", "--out", str(out)])
        assert result.exit_code == 0, result.output
        assert (out / "My Renamed Chat v2.md").is_file()  # sanitized title

    def test_current_with_session_id(self, claude_store, codex_store, tmp_path):
        out = tmp_path / "exports"
        result = CliRunner().invoke(
            main, ["current", "--session-id", CLAUDE_SESSION_ID,
                   "--format", "md", "--out", str(out)])
        assert result.exit_code == 0, result.output
        assert list(out.glob("*.md"))

    def test_current_prefers_active_codex_thread_id(self, codex_store, monkeypatch,
                                                     tmp_path):
        """Regression: a newer task in the same cwd must not export instead."""
        other_id = "019f9999-aaaa-bbbb-cccc-dddddddddddd"
        other_entries = codex_entries()
        other_entries[0]["payload"]["session_id"] = other_id
        other_entries[0]["payload"]["id"] = other_id
        other_entries[0]["payload"]["first_user_message"] = "Wrong newer task"
        day = codex_store / "sessions" / "2026" / "07" / "17"
        _jsonl(day / f"rollout-2026-07-17T13-00-00-{other_id}.jsonl",
               other_entries)
        monkeypatch.setenv("CODEX_THREAD_ID", CODEX_SESSION_ID)

        out = tmp_path / "exports"
        result = CliRunner().invoke(
            main, ["current", "--source", "codex", "--format", "md",
                   "--out", str(out)])

        assert result.exit_code == 0, result.output
        assert (out / "Spreadsheet review chat.md").is_file()
        assert not (out / "Wrong newer task.md").exists()

    def test_current_prefers_cursor_conversation_id(self, cursor_store, monkeypatch,
                                                     tmp_path):
        """Regression: CURSOR_CONVERSATION_ID must win over a newer cwd peer."""
        other_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        other = [
            {"role": "user", "message": {"content": [
                {"type": "text",
                 "text": "<user_query>\nWrong newer Cursor chat\n</user_query>"},
            ]}},
        ]
        folder = (cursor_store / "projects" / "c-proj" / "agent-transcripts"
                  / other_id)
        _jsonl(folder / f"{other_id}.jsonl", other)
        monkeypatch.setenv("CURSOR_CONVERSATION_ID", CURSOR_SESSION_ID)

        out = tmp_path / "exports"
        result = CliRunner().invoke(
            main, ["current", "--source", "cursor", "--format", "md",
                   "--out", str(out)])

        assert result.exit_code == 0, result.output
        assert list(out.glob("Export this Cursor chat*.md"))
        assert not list(out.glob("Wrong newer Cursor chat*.md"))

    def test_list_includes_cursor(self, cursor_store):
        result = CliRunner().invoke(main, ["list", "--source", "cursor"])
        assert result.exit_code == 0, result.output
        assert "Export this Cursor chat" in result.output
        assert CURSOR_SESSION_ID in result.output

    def test_export_path_directly(self, claude_store, codex_store, tmp_path):
        from xexport.sources import codex as codex_src
        path = codex_src.find_session(CODEX_SESSION_ID)
        out = tmp_path / "exports"
        result = CliRunner().invoke(
            main, [str(path), "--format", "md", "--out", str(out)])
        assert result.exit_code == 0, result.output
        assert (out / "Spreadsheet review chat.md").is_file()

    def test_unknown_ref_fails_cleanly(self, claude_store, codex_store):
        result = CliRunner().invoke(main, ["deadbeef-0000"])
        assert result.exit_code != 0
        assert "No session matching" in result.output
