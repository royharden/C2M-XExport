"""what_bug_this_catches: native children can begin with copied ancestor metadata."""
import json

import pytest
from click.testing import CliRunner

from xexport.cli import main
from xexport.sources import codex
from test_codex_native_children import CHILD, GRAND, OTHER, PARENT, rollout, store


def inherited(store, *, ancestor=PARENT, grandparent=""):
    p = rollout(store, CHILD, PARENT)
    rows = p.read_text(encoding="utf-8", errors="replace").splitlines()
    meta = {"id": ancestor, "session_id": ancestor, "source": "vscode", "thread_source": "user",
            "cwd": "ancestor-workspace", "first_user_message": "Ancestor title"}
    if grandparent:
        meta.update(session_id=grandparent, thread_source="subagent", source={"subagent": {
            "thread_spawn": {"parent_thread_id": grandparent, "agent_path": "/root/ancestor"}}})
    rows.insert(1, json.dumps({"type": "session_meta", "payload": meta}))
    if grandparent:
        rows.insert(2, json.dumps({"type": "session_meta", "payload": {
            "id": grandparent, "session_id": grandparent, "source": "vscode", "thread_source": "user"}}))
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return p


def test_native_two_header_reproduction(store):
    s = codex.parse_file(inherited(store))
    assert s.session_id == CHILD and s.parent_session_id == PARENT
    assert s.inherited_session_ids == [PARENT]
    assert s.cwd == str(store) and s.title != "Ancestor title"
    assert len(s.messages) == 1  # Copied content remains in the full-fidelity snapshot.


def test_multilevel_explicit_chain(store):
    s = codex.parse_file(inherited(store, grandparent=GRAND))
    assert s.inherited_session_ids == [PARENT, GRAND]
    assert s.session_id == CHILD and s.parent_session_id == PARENT


@pytest.mark.parametrize("mode", ["append", "replace", "new"])
@pytest.mark.parametrize("command", ["current", "export", "path", "subagents"])
def test_inherited_identity_all_cli_paths(store, mode, command):
    p = inherited(store)
    out = store / "out"
    if command == "path":
        args = ["export", str(p)]
    elif command == "export":
        args = [command, CHILD]
    else:
        args = [command, "--session-id", PARENT if command == "subagents" else CHILD]
    result = CliRunner().invoke(main, [*args, "--source", "codex", "--format", "both",
                                      "--mode", mode, "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert CHILD in result.output
    md = next(out.glob("*.md"))
    assert CHILD in md.name and "0002_Codex_Child" not in md.name  # Assignment could be inherited.
    text = md.read_text(encoding="utf-8", errors="replace")
    assert "Inherited context" in text and PARENT in text
    html = next((out / "html").rglob("index.html"))
    assert "Inherited context" in html.read_text(encoding="utf-8", errors="replace")


@pytest.mark.parametrize("case", ["unrelated", "late_parent", "wrong_parent", "cycle"])
def test_conflicting_metadata_still_rejected(store, case):
    p = inherited(store, ancestor=OTHER if case == "unrelated" else PARENT,
                  grandparent=CHILD if case == "cycle" else "")
    rows = p.read_text(encoding="utf-8", errors="replace").splitlines()
    if case == "late_parent":
        rows.append(rows.pop(1))
    if case == "wrong_parent":
        row = json.loads(rows[1]); row["payload"]["parent_thread_id"] = OTHER
        rows[1] = json.dumps(row)
    p.write_text("\n".join(rows) + "\n", encoding="utf-8")
    result = CliRunner().invoke(main, ["current", "--source", "codex", "--session-id", CHILD,
                                      "--mode", "replace", "--out", str(store / "out")])
    assert result.exit_code != 0 and "identity" in result.output.lower()
    assert not (store / "out").exists()


def test_full_history_refresh_retitle_preserves_parent(store):
    rollout(store, PARENT)
    inherited(store)
    runner = CliRunner()
    out = store / "out"
    args = ["--source", "codex", "--format", "both", "--out", str(out)]
    assert runner.invoke(main, ["current", "--session-id", PARENT, *args]).exit_code == 0
    parent_file = next(out.glob(f"*{PARENT}.md"))
    parent_bytes = parent_file.read_bytes()
    assert runner.invoke(main, ["subagents", "--session-id", PARENT, *args]).exit_code == 0
    (store / "session_index.jsonl").write_text(json.dumps({"id": CHILD, "thread_name": "Child title"}) + "\n", encoding="utf-8")
    assert runner.invoke(main, ["current", "--session-id", CHILD, *args]).exit_code == 0
    result = runner.invoke(main, ["current", "--session-id", CHILD, *args])
    assert result.exit_code == 0 and "Up to date" in result.output and CHILD in result.output
    assert len(list(out.glob("*.md"))) == 2
    assert any("Child title" in p.name and CHILD in p.name for p in out.glob("*.md"))
    assert parent_file.read_bytes() == parent_bytes


def test_batch_with_valid_inherited_child_and_malformed_sibling(store):
    inherited(store)
    rollout(store, OTHER, PARENT, id=GRAND)
    result = CliRunner().invoke(main, ["subagents", "--source", "codex", "--session-id", PARENT,
                                      "--format", "both", "--out", str(store / "out")])
    assert result.exit_code != 0 and not (store / "out").exists()
