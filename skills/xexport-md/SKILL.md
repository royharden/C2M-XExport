---
name: xexport-md
description: Export the current chat session to a single Markdown transcript under .chatexports using the xexport CLI, either as a new file or by refreshing this session's existing export. Use when the user runs /xexport-md (Claude Code or Cursor), $xexport-md (Codex), or asks to export, save, archive, update, or add to this chat/session/conversation as Markdown. In Codex use CODEX_THREAD_ID; in Cursor use CURSOR_CONVERSATION_ID.
---

# xexport-md

Export the CURRENT session to a single Markdown file via the `xexport` CLI from
`C:\Users\Roy Harden\OneDrive\PJ-OD\C2M\C2M-XExport`.

Markdown exports land at the top level of `.chatexports\` — HTML exports go one level
down in `.chatexports\html\`, so this folder stays scannable.

## Steps

1. **Pick the mode** from what the user said (this is the first decision, not an
   afterthought):

   | The user said | Use |
   |---|---|
   | `/xexport-md` on its own | `--mode append` |
   | "just add to the existing" / "top it up" / "update it" / "refresh it" | `--mode append` |
   | "make a whole copy of everything" / "a fresh copy" / "a separate one" / "snapshot it" | `--mode new` |
   | "redo it" / "rebuild it" / "overwrite the existing one" | `--mode replace` |
   | "as a new file called X" | `--mode new --name "X"` |

   **`--mode append` is the default.** It refreshes this session's existing
   export by re-parsing and re-rendering the whole transcript, or creates one if
   absent. It is not literal line appending. An unchanged transcript prints
   "Up to date" and does not touch the file.

   Shrink and content-filter guards protect the existing export; a refusal may
   write a separate file with a warning and provenance. Preserve both. Explicit
   `--mode replace` can override content guards, never identity validation.

   **Substitute the mode you picked for `<MODE>` in the command below.** The
   commands are templates, not literals — copying one unchanged is the most likely
   way to get this wrong, and it fails silently: "make a whole copy of everything"
   would quietly append instead.

2. **Output directory:** `$ARGUMENTS` if the user passed one, else the project root's
   `.chatexports` folder (Claude Code: `${CLAUDE_PROJECT_DIR}\.chatexports`;
   Codex / Cursor: `<workspace root>\.chatexports`).

3. **Callsign: you do not need to pass one.** The `{agent}` field resolves itself —
   the AgentNamer callsign when this project has a registry, `<Harness>_<Model>` when it
   does not, so the export names its agent either way. Pass
   `--callsign "<your full callsign>"` only to override what it finds, and never invent
   one or run AgentNamer just to get one.

4. **Pick the command for your harness:**

   - **Claude Code** — the placeholder on the next line is auto-substituted with the
     real session id. If it still looks like a literal `${...}` placeholder, you are NOT
     running in Claude Code; use the Cursor or Codex form instead.

     ```
     xexport current --source claude --session-id ${CLAUDE_SESSION_ID} --format md --mode <MODE> --out "<output-dir>"
     ```

   - **Cursor** (Agent / IDE) — resolve the active conversation id from
     `CURSOR_CONVERSATION_ID` (`$env:CURSOR_CONVERSATION_ID` in PowerShell or
     `$CURSOR_CONVERSATION_ID` in a POSIX shell). Pass it explicitly.

     ```
     xexport current --source cursor --session-id "<CURSOR_CONVERSATION_ID>" --format md --mode <MODE> --out "<output-dir>"
     ```

     If `CURSOR_CONVERSATION_ID` is absent, do not guess. Run
     `xexport list --source cursor` and ask the user to identify the intended session id.

   - **Codex** (CLI or desktop) — resolve the active task id from `CODEX_THREAD_ID`
     (`$env:CODEX_THREAD_ID` in PowerShell or `$CODEX_THREAD_ID` in a POSIX shell). Pass
     it explicitly. Do **not** select the newest rollout for the working directory:
     several Codex tasks can share that directory.

     ```
     xexport current --source codex --session-id "<CODEX_THREAD_ID>" --format md --mode <MODE> --out "<output-dir>"
     ```

     If `CODEX_THREAD_ID` is absent, do not guess. Run `xexport list --source codex` and
     ask the user to identify the intended session id.

5. If `xexport` is not on PATH, use the repo directly:

   ```
   uv run --project "C:\Users\Roy Harden\OneDrive\PJ-OD\C2M\C2M-XExport" xexport current ...
   ```

6. Verify that the reported session id equals the requested id, including an
   "Up to date" result. Exit 0 or `wrote/updated` alone is insufficient. If the ID
   differs or is missing, preserve existing exports, stop this write path, inspect
   only the intended rollout's identity metadata, and report the failure. Never use
   `--mode replace` to repair an identity mismatch. Callsigns label exports; they
   do not select a conversation.

   Report whether the export was created, refreshed, or already current, and link
   its path. Relay every `Warning:` verbatim and identify any separate guard-created
   file; retain its provenance and the protected original.

## Notes

- The export is a snapshot up to the moment the command runs; the turn that invoked this
  skill is only partially captured. Running it again later with `--mode append` picks up
  everything that followed.
- A refresh rewrites the whole file, so there is no addendum seam to look for; the
  export simply holds the conversation as it stands.
- The previous export is found by the session id in its **name**, not by its title, so a
  refresh still finds it after the chat has been retitled — and renames the file to
  follow the new title. A deliberate `--mode new` numbered copy is not adopted as the file to refresh;
  guard-created companions carry provenance that allows later recovery refreshes.
- `--brief` gives user + assistant text only (no tool calls or thinking).
- `--full` disables truncation of long tool output.
- `--name "Custom Name"` overrides the title part of the file name; collisions get " (2)".
- Paginated HTML instead: use the sibling skill `xexport-html`.
- **Native Codex children require xexport 0.2.2 or later.** Verify the executable
  version before using either child export path. In 0.2.0 even an exact child ID
  can be parsed as its parent; do not retry against production exports or use
  `--mode replace`. Report the limitation until a tested fixed CLI is available.
- Parent-driven Codex export:
  `xexport subagents --source codex --session-id "<exact parent CODEX_THREAD_ID>" --format md --mode append --out "<output-dir>"`.
  This finds **direct children only**, through explicit `thread_spawn.parent_thread_id`.
  For grandchildren, invoke it again with each child's exact ID as parent.
- A native Codex child can export itself using the `current` command above when
  its runtime `CODEX_THREAD_ID` matches metadata `payload.id` and the rollout filename.
  `CODEX_SESSION_ID` / metadata `session_id` may identify the parent. Never substitute
  them for the child's identity. If runtime identity is inherited or unavailable,
  the parent must select the child's verified exact thread ID. A callsign assignment
  in a fresh-context child's opening task labels its export; bulk child exports ignore inherited
  callsign flags/environment values.
- Claude/Cursor children use the harness-specific parent-driven command:
  `xexport subagents --source <claude|cursor> --session-id "<parent id>" --format md --mode append --out "<output-dir>"`.
  Do not assume their child environment exposes an independent identity. Child
  exports land beside the main exports in the chosen format; `--subagent-subdir
  subagents` optionally separates them. Claude IDs use `claude-agent-<hex>`;
  native Codex children retain `codex-<full child thread id>`.
- To make exports happen automatically for every session and every subagent in a
  project, use the sibling skill `xexport-auto`.


## Native Codex inherited history (0.2.2)

Version 0.2.1 supports fresh-context children but rejects valid full-history children
whose second metadata record belongs to the parent. Use 0.2.2 or later for these
exports. The first validated child envelope determines identity, title lookup and
destination. Only the contiguous, explicitly linked ancestor-header chain before
conversation content is accepted as inherited metadata; unrelated, conflicting or
late ancestor headers still fail before writes.

The export preserves copied history and labels it as **Inherited context** with
ancestor IDs. It is a full rollout snapshot, not solely the child's own work. Its
opening prompt/callsign and model can belong to an ancestor, so xexport uses a neutral
`Codex_Sub` label and the child's own indexed/envelope title (or child ID) when no
reliable child-only boundary is available. Do not substitute the parent's callsign,
rewrite rollout metadata, or treat a missing child callsign as an identity failure.
