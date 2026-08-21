"""Shared fixtures: synthetic session stores in tmp dirs (hermetic via env overrides)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

CLAUDE_SESSION_ID = "11111111-2222-3333-4444-555555555555"
CODEX_SESSION_ID = "019f0000-aaaa-bbbb-cccc-dddddddddddd"
CURSOR_SESSION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"


def _jsonl(path: Path, entries: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for e in entries:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    return path


def claude_entries() -> list[dict]:
    sid = CLAUDE_SESSION_ID
    return [
        {"type": "queue-operation", "operation": "enqueue", "sessionId": sid},
        {"type": "ai-title", "aiTitle": "AI generated title", "sessionId": sid},
        {"type": "user", "sessionId": sid, "cwd": "C:\\proj",
         "timestamp": "2026-07-17T10:00:00.000Z",
         "message": {"role": "user", "content": "Hello — please fix the bug"}},
        {"type": "assistant", "sessionId": sid,
         "timestamp": "2026-07-17T10:00:05.000Z",
         "message": {"role": "assistant", "model": "claude-opus-4-8", "content": [
             {"type": "thinking", "thinking": "Let me look at the code first."},
             {"type": "text", "text": "I'll check the file."},
             {"type": "tool_use", "id": "toolu_1", "name": "Bash",
              "input": {"command": "ls -la", "description": "List files"}},
         ]}},
        {"type": "user", "sessionId": sid,
         "timestamp": "2026-07-17T10:00:08.000Z",
         "message": {"role": "user", "content": [
             {"type": "tool_result", "tool_use_id": "toolu_1",
              "content": "file_a.py\nfile_b.py", "is_error": False},
         ]}},
        # Sidechain (subagent) content must be excluded
        {"type": "user", "isSidechain": True, "sessionId": sid,
         "message": {"role": "user", "content": "subagent internal prompt"}},
        # Unknown metadata line type -> RAW block, never a crash
        {"type": "mystery-future-type", "sessionId": sid, "data": {"x": 1}},
        {"type": "assistant", "sessionId": sid,
         "timestamp": "2026-07-17T10:00:15.000Z",
         "message": {"role": "assistant", "model": "claude-opus-4-8", "content": [
             {"type": "text", "text": "Done. `file_a.py` had the bug — "
              "here is a very long closing answer " + "word " * 200},
         ]}},
        {"type": "user", "sessionId": sid,
         "timestamp": "2026-07-17T10:01:00.000Z",
         "message": {"role": "user", "content": [
             {"type": "text", "text": "Great, thanks! Second prompt <script>alert(1)</script>"},
         ]}},
        {"type": "assistant", "sessionId": sid,
         "timestamp": "2026-07-17T10:01:05.000Z",
         "message": {"role": "assistant", "content": [
             {"type": "text", "text": "You're welcome."},
         ]}},
        # User-set name arrives late and must win over ai-title
        {"type": "custom-title", "customTitle": "My Renamed Chat: v2?",
         "sessionId": sid},
    ]


def codex_entries() -> list[dict]:
    sid = CODEX_SESSION_ID
    return [
        {"timestamp": "2026-07-17T12:00:00.000Z", "type": "session_meta",
         "payload": {"session_id": sid, "id": sid,
                     "timestamp": "2026-07-17T11:59:59.000Z",
                     "cwd": "C:\\proj", "originator": "Codex Desktop",
                     "source": "vscode", "model_provider": "openai",
                     "first_user_message": "Review my spreadsheet"}},
        {"timestamp": "2026-07-17T12:00:00.100Z", "type": "world_state",
         "payload": {"full": True, "state": {"agents_md": {"text": "big noise"}}}},
        {"timestamp": "2026-07-17T12:00:01.000Z", "type": "turn_context",
         "payload": {"model": "gpt-5.6-terra"}},
        {"timestamp": "2026-07-17T12:00:02.000Z", "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [
             {"type": "input_text",
              "text": "<recommended_plugins>\nnoise\n</recommended_plugins>"}]}},
        {"timestamp": "2026-07-17T12:00:02.500Z", "type": "response_item",
         "payload": {"type": "message", "role": "user", "content": [
             {"type": "input_text", "text": "Review my spreadsheet"}]}},
        {"timestamp": "2026-07-17T12:00:03.000Z", "type": "response_item",
         "payload": {"type": "reasoning", "summary": [
             {"type": "summary_text", "text": "Reading the workbook now."}]}},
        {"timestamp": "2026-07-17T12:00:04.000Z", "type": "response_item",
         "payload": {"type": "function_call", "name": "shell", "call_id": "c1",
                     "arguments": "{\"command\": [\"ls\"]}"}},
        {"timestamp": "2026-07-17T12:00:05.000Z", "type": "response_item",
         "payload": {"type": "function_call_output", "call_id": "c1",
                     "output": "{\"output\": \"book.xlsx\", \"metadata\": {}}"}},
        # event_msg entries duplicate response_items and must be skipped
        {"timestamp": "2026-07-17T12:00:05.500Z", "type": "event_msg",
         "payload": {"type": "agent_message", "message": "dup"}},
        {"timestamp": "2026-07-17T12:00:06.000Z", "type": "response_item",
         "payload": {"type": "message", "role": "assistant", "content": [
             {"type": "output_text", "text": "The workbook has **3 tabs**."}]}},
    ]


def cursor_entries() -> list[dict]:
    return [
        {"role": "user", "message": {"content": [
            {"type": "text", "text": (
                "<timestamp>Thursday, Aug 20, 2026, 3:34 PM (UTC-4)</timestamp>\n"
                "<user_query>\nExport this Cursor chat as Markdown please.\n</user_query>"
            )},
        ]}},
        {"role": "assistant", "message": {"content": [
            {"type": "text", "text": "I'll run xexport for this conversation."},
            {"type": "tool_use", "name": "Shell",
             "input": {"command": "xexport current --source cursor --format md",
                       "description": "Export current Cursor chat"}},
        ]}},
        {"role": "assistant", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "call_1",
             "content": "wrote: .chatexports\\Export this Cursor chat.md",
             "is_error": False},
        ]}},
        {"role": "assistant", "message": {"content": [
            {"type": "text", "text": "Done — exported the transcript."},
        ]}},
        {"role": "user", "message": {"content": [
            {"type": "text", "text": "<user_query>\nThanks, also HTML?\n</user_query>"},
        ]}},
        {"role": "assistant", "message": {"content": [
            {"type": "text", "text": "Sure."},
        ]}},
    ]


@pytest.fixture
def claude_store(tmp_path, monkeypatch):
    home = tmp_path / "claude-home"
    proj = home / "projects" / "C--proj"
    _jsonl(proj / f"{CLAUDE_SESSION_ID}.jsonl", claude_entries())
    monkeypatch.setenv("XEXPORT_CLAUDE_HOME", str(home))
    return home


@pytest.fixture
def codex_store(tmp_path, monkeypatch):
    home = tmp_path / "codex-home"
    day = home / "sessions" / "2026" / "07" / "17"
    _jsonl(day / f"rollout-2026-07-17T12-00-00-{CODEX_SESSION_ID}.jsonl",
           codex_entries())
    _jsonl(home / "session_index.jsonl",
           [{"id": CODEX_SESSION_ID, "thread_name": "Spreadsheet review chat",
             "updated_at": "2026-07-17T12:00:06Z"}])
    monkeypatch.setenv("XEXPORT_CODEX_HOME", str(home))
    return home


@pytest.fixture
def cursor_store(tmp_path, monkeypatch):
    home = tmp_path / "cursor-home"
    proj = home / "projects" / "c-proj"
    transcript = proj / "agent-transcripts" / CURSOR_SESSION_ID
    _jsonl(transcript / f"{CURSOR_SESSION_ID}.jsonl", cursor_entries())
    # Subagent noise must not appear in listings
    _jsonl(transcript / "subagents" / "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb.jsonl",
           [{"role": "user", "message": {"content": [
               {"type": "text", "text": "<user_query>\nsubagent only\n</user_query>"},
           ]}}])
    monkeypatch.setenv("XEXPORT_CURSOR_HOME", str(home))
    return home
