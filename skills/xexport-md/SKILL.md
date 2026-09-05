---
name: xexport-md
description: Export the current chat session to a single Markdown transcript under .chatexports using the xexport CLI, either as a new file or as an addendum appended to this session's existing export. Use when the user runs /xexport-md (Claude Code or Cursor), $xexport-md (Codex), or asks to export, save, archive, update, or add to this chat/session/conversation as Markdown. In Codex use CODEX_THREAD_ID; in Cursor use CURSOR_CONVERSATION_ID.
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
   | "just add to the existing" / "addendum" / "top it up" / "update it" | `--mode append` |
   | "make a whole copy of everything" / "a fresh copy" / "a separate one" / "snapshot it" | `--mode new` |
   | "redo it" / "rebuild it" / "overwrite the existing one" | `--mode replace` |
   | "as a new file called X" | `--mode new --name "X"` |

   **`--mode append` is the default** because it is always safe: it adds only the turns
   that are new since the last export, and if no export exists yet it simply creates
   one. Re-running it costs nothing — with no new turns it prints "Up to date" and
   leaves the file alone.

   **Substitute the mode you picked for `<MODE>` in the command below.** The
   commands are templates, not literals — copying one unchanged is the most likely
   way to get this wrong, and it fails silently: "make a whole copy of everything"
   would quietly append instead.

2. **Output directory:** `$ARGUMENTS` if the user passed one, else the project root's
   `.chatexports` folder (Claude Code: `${CLAUDE_PROJECT_DIR}\.chatexports`;
   Codex / Cursor: `<workspace root>\.chatexports`).

3. **Add your callsign if you hold one.** If you claimed an AgentNamer callsign in this
   session, pass `--callsign "<your full callsign>"` so the file is named after you
   (`0007_Claude_Opus5_Update xexport skills_a68ce6ac.md`). If this project does not use
   AgentNamer, omit the flag entirely — do not invent a callsign, and do not run
   AgentNamer just to get one.

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

6. Verify that the reported session id equals the requested id, then relay the result:
   `wrote:` for a new file, `added:` + `wrote:` for an addendum, `Up to date` when
   nothing changed. Say which of the three happened in one line — the user asked for an
   export and deserves to know whether a file was created or extended.

## Notes

- The export is a snapshot up to the moment the command runs; the turn that invoked this
  skill is only partially captured. Running it again later with `--mode append` picks up
  everything that followed.
- An addendum is separated by a `## ➕ Addendum N` heading. `--seamless` suppresses it
  when you want one continuous transcript (used by the automation hooks).
- Append finds the previous export by **session id**, not by file name, so it still works
  after the chat has been retitled — and the addendum notes the new title.
- `--brief` gives user + assistant text only (no tool calls or thinking).
- `--full` disables truncation of long tool output.
- `--name "Custom Name"` overrides the title part of the file name; collisions get " (2)".
- Paginated HTML instead: use the sibling skill `xexport-html`.
- To export a session's **subagent** transcripts (which cannot export themselves —
  a subagent shares its parent's session id): `xexport subagents --session-id <parent
  id> --format md --mode append --out "<output-dir>"`. They land flat beside the main
  exports, marked by their `claude-agent-<hex>` id suffix; `--subagent-subdir subagents`
  puts them in their own folder instead.
- To make exports happen automatically for every session and every subagent in a
  project, use the sibling skill `xexport-auto`.
