"""what_bug_this_catches: native children must never refresh a parent's export."""
import json

import pytest
from click.testing import CliRunner

from xexport.cli import main
from xexport.sources import codex

PARENT = "019f0000-aaaa-bbbb-cccc-000000000001"
CHILD = "019f0000-aaaa-bbbb-cccc-000000000002"
OTHER = "019f0000-aaaa-bbbb-cccc-000000000003"
GRAND = "019f0000-aaaa-bbbb-cccc-000000000004"


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("XEXPORT_CODEX_HOME", str(tmp_path))
    monkeypatch.setenv("XEXPORT_CALLSIGN", "0000_Codex_Parent")
    monkeypatch.delenv("XEXPORT_NAME_TEMPLATE", raising=False)
    return tmp_path


def rollout(store, sid, parent="", **overrides):
    meta = {"id": sid, "cwd": str(store)}
    if parent:
        meta.update(session_id=parent, thread_source="subagent", parent_thread_id=parent,
                    source={"subagent": {"thread_spawn": {
                        "parent_thread_id": parent, "depth": 1, "agent_path": "/root/child"}}})
    meta.update(overrides)
    path = store / "sessions" / f"rollout-test-{sid}.jsonl"
    path.parent.mkdir(exist_ok=True)
    entries = [{"type": "session_meta", "payload": meta},
               {"type": "response_item", "payload": {"type": "message", "role": "user",
                "content": [{"type": "input_text", "text":
                             f"Your callsign is 0002_Codex_Child.\nTask {sid}"}]}}]
    path.write_text("".join(json.dumps(e) + "\n" for e in entries), encoding="utf-8")
    return path


def test_child_identity_and_parent(store):
    s = codex.parse_file(rollout(store, CHILD, PARENT))
    assert (s.session_id, s.parent_session_id, s.is_subagent) == (CHILD, PARENT, True)
    assert s.agent_path == "/root/child"


def test_direct_children_only(store):
    rollout(store, PARENT)
    rollout(store, CHILD, PARENT)
    rollout(store, OTHER, PARENT)
    rollout(store, GRAND, CHILD)
    assert {i.session_id for i in codex.list_subagents(PARENT)} == {CHILD, OTHER}


@pytest.mark.parametrize("fmt", ["md", "html", "both"])
def test_distinct_exports_retitle_and_noop(store, fmt):
    for sid, parent in [(PARENT, ""), (CHILD, PARENT), (OTHER, PARENT)]:
        rollout(store, sid, parent)
    out = store / "out"
    runner = CliRunner()
    args = ["--source", "codex", "--format", fmt, "--out", str(out)]
    result = runner.invoke(main, ["current", "--session-id", PARENT, *args])
    assert result.exit_code == 0, result.output
    result = runner.invoke(main, ["subagents", "--session-id", PARENT, *args])
    assert result.exit_code == 0, result.output
    assert CHILD in result.output and OTHER in result.output
    files = list(out.glob("*.md")) if fmt != "html" else list((out / "html").iterdir())
    assert len(files) == 3
    child = next(p for p in files if CHILD in p.name)
    assert "0002_Codex_Child" in child.name
    (store / "session_index.jsonl").write_text(json.dumps({"id": CHILD, "thread_name": "Retitled child"}) + "\n", encoding="utf-8")
    result = runner.invoke(main, ["current", "--session-id", CHILD, *args])
    assert result.exit_code == 0, result.output
    assert CHILD in result.output
    result = runner.invoke(main, ["current", "--session-id", CHILD, *args])
    assert result.exit_code == 0 and CHILD in result.output
    assert "Up to date" in result.output
    files = list(out.glob("*.md")) if fmt != "html" else list((out / "html").iterdir())
    assert len(files) == 3
    assert any("Retitled child" in p.name and CHILD in p.name for p in files)


@pytest.mark.parametrize("override", [dict(id=OTHER), dict(parent_thread_id=OTHER), dict(session_id=OTHER)])
@pytest.mark.parametrize("mode", ["append", "new", "replace"])
def test_conflict_aborts_before_writing(store, override, mode):
    rollout(store, CHILD, PARENT, **override)
    out = store / "out"
    result = CliRunner().invoke(main, ["current", "--source", "codex", "--session-id", CHILD,
                                      "--format", "both", "--mode", mode, "--out", str(out)])
    assert result.exit_code != 0
    assert "identity" in result.output.lower()
    assert not out.exists()


def test_requested_identity_checked_independently(store, monkeypatch):
    path = rollout(store, OTHER)
    monkeypatch.setattr(codex, "find_session", lambda _: path)
    result = CliRunner().invoke(main, ["current", "--source", "codex", "--session-id", CHILD,
                                      "--out", str(store / "out")])
    assert result.exit_code != 0
    assert not (store / "out").exists()


def test_legacy_session_id_and_partial_tail(store):
    path = rollout(store, CHILD, id="", session_id=CHILD)
    with path.open("a", encoding="utf-8") as f:
        f.write('{"type": "response_item"')
    s = codex.parse_file(path)
    assert s.session_id == CHILD and not s.is_subagent
    assert len(s.messages) == 2  # Partial bytes are preserved as Raw, not silently lost.


def test_larger_mismatched_child_preserves_existing_parent(store, monkeypatch):
    # what_bug_this_catches: a longer wrong transcript bypassed the old shrink guard.
    runner = CliRunner()
    parent = rollout(store, PARENT)
    out = store / "out"
    args = ["--source", "codex", "--format", "both", "--out", str(out)]
    assert runner.invoke(main, ["current", "--session-id", PARENT, *args]).exit_code == 0
    before = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    child = rollout(store, CHILD, PARENT)
    with child.open("a", encoding="utf-8") as f:
        for _ in range(30):
            f.write(json.dumps({"type": "response_item", "payload": {
                "type": "message", "role": "assistant", "content": [
                    {"type": "output_text", "text": "long child response"}]}}) + "\n")
    monkeypatch.setattr(codex, "find_session", lambda _: child)
    result = runner.invoke(main, ["current", "--session-id", PARENT, "--mode", "replace", *args])
    assert result.exit_code != 0
    after = {p.relative_to(out): p.read_bytes() for p in out.rglob("*") if p.is_file()}
    assert before == after and parent.exists()


def test_archived_child_empty_search_and_runtime_id(store, monkeypatch):
    p = rollout(store, CHILD, PARENT)
    archive = store / "archived_sessions"
    archive.mkdir()
    p.rename(archive / p.name)
    rollout(store, OTHER)  # unrelated task in the same workspace
    monkeypatch.setenv("CODEX_THREAD_ID", PARENT)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", OTHER)
    args = ["subagents", "--source", "codex", "--format", "md", "--out", str(store / "out")]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0 and CHILD in result.output
    result = CliRunner().invoke(main, [*args, "--session-id", GRAND])
    assert result.exit_code == 0 and "No subagent transcripts" in result.output


def test_ambiguous_partial_and_exact_child_export(store):
    rollout(store, CHILD, PARENT)
    rollout(store, OTHER, PARENT)
    args = ["--source", "codex", "--format", "md", "--out", str(store / "out")]
    result = CliRunner().invoke(main, ["export", "019f0000", *args])
    assert result.exit_code != 0 and not (store / "out").exists()
    result = CliRunner().invoke(main, ["export", CHILD[-12:], *args])
    assert result.exit_code == 0 and CHILD in result.output


def test_entire_batch_validated_before_any_write(store):
    rollout(store, CHILD, PARENT)
    rollout(store, OTHER, PARENT, id=GRAND)
    result = CliRunner().invoke(main, ["subagents", "--source", "codex", "--session-id", PARENT,
                                      "--out", str(store / "out")])
    assert result.exit_code != 0 and not (store / "out").exists()


def test_identity_change_after_compaction_fails(store):
    p = rollout(store, CHILD, PARENT)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "compacted", "payload": {}}) + "\n")
        f.write(json.dumps({"type": "session_meta", "payload": {"id": PARENT}}) + "\n")
    with pytest.raises(ValueError, match="identity conflict"):
        codex.parse_file(p)


def test_self_export_checks_runtime_thread_and_ignores_parent_label(store, monkeypatch):
    rollout(store, CHILD, PARENT)
    monkeypatch.setenv("CODEX_THREAD_ID", CHILD)
    monkeypatch.setenv("CODEX_SESSION_ID", PARENT)
    args = ["current", "--source", "codex", "--format", "both", "--out", str(store / "out"),
            "--callsign", "0000_Codex_Parent"]
    result = CliRunner().invoke(main, args)
    assert result.exit_code == 0 and CHILD in result.output
    md = next((store / "out").glob("*.md"))
    assert "0002_Codex_Child" in md.name and CHILD in md.name
    text = md.read_text(encoding="utf-8", errors="replace")
    assert f"**Subagent of:** `{PARENT}`" in text and "/root/child" in text
    html = next((store / "out" / "html").rglob("index.html"))
    assert PARENT in html.read_text(encoding="utf-8", errors="replace")


def test_hook_path_cannot_override_explicit_codex_identity(store):
    p = rollout(store, OTHER)
    result = CliRunner().invoke(main, ["current", "--from-hook", "--session-id", CHILD,
                                      "--out", str(store / "out")],
                                input=json.dumps({"transcript_path": str(p)}))
    assert result.exit_code != 0 and not (store / "out").exists()
