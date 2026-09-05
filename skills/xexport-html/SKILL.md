---
name: xexport-html
description: Export the current chat session to a paginated HTML transcript under .chatexports\html using the xexport CLI, either as a new export or by refreshing this session's existing one. Use when the user runs /xexport-html (Claude Code or Cursor), $xexport-html (Codex), or asks to export, save, archive, update, or add to this chat/session/conversation as HTML. In Codex use CODEX_THREAD_ID; in Cursor use CURSOR_CONVERSATION_ID.
---

# xexport-html

Export the CURRENT session to paginated HTML (index.html + page-NNN.html, in the
claude-code-transcripts visual style) via the `xexport` CLI from
`C:\Users\Roy Harden\OneDrive\PJ-OD\C2M\C2M-XExport`.

HTML exports land in **`.chatexports\html\<name>\`**, one level below the Markdown
exports. That is deliberate: an HTML export is a folder, and a pile of folders at the top
level makes the Markdown files hard to find.

## Steps

1. **Pick the mode** from what the user said:

   | The user said | Use |
   |---|---|
   | `/xexport-html` on its own | `--mode append` |
   | "just update the existing" / "refresh it" / "top it up" | `--mode append` |
   | "make a whole copy of everything" / "a fresh copy" / "a separate one" | `--mode new` |
   | "redo it" / "rebuild it" / "overwrite the existing one" | `--mode replace` |
   | "as a new export called X" | `--mode new --name "X"` |

   **For HTML, `--mode append` means "re-render this session's existing folder in
   place".** The output is a paginated, index-linked site derived wholly from the
   session, so regenerating it produces exactly what an incremental append would have —
   without leaving a half-written index behind. If no export exists yet it creates one.
   With no new turns it prints "Up to date" and touches nothing.

   **Substitute the mode you picked for `<MODE>` in the command below.** The
   commands are templates, not literals — copying one unchanged is the most likely
   way to get this wrong, and it fails silently: "make a whole copy of everything"
   would quietly append instead.

2. **Output directory:** `$ARGUMENTS` if the user passed one, else the project root's
   `.chatexports` folder (Claude Code: `${CLAUDE_PROJECT_DIR}\.chatexports`;
   Codex / Cursor: `<workspace root>\.chatexports`). Pass the `.chatexports` root — the
   CLI adds the `html\` level itself.

3. **Add your callsign if you hold one.** If you claimed an AgentNamer callsign in this
   session, pass `--callsign "<your full callsign>"` so the folder is named after you
   (`0007_Claude_Opus5_Update xexport skills_a68ce6ac\`). If this project does not use
   AgentNamer, omit the flag — do not invent a callsign, and do not run AgentNamer just
   to get one.

4. **Pick the command for your harness:**

   - **Claude Code** — the placeholder on the next line is auto-substituted with the
     real session id. If it still looks like a literal `${...}` placeholder, you are NOT
     running in Claude Code; use the Cursor or Codex form instead.

     ```
     xexport current --source claude --session-id ${CLAUDE_SESSION_ID} --format html --mode <MODE> --out "<output-dir>"
     ```

   - **Cursor** (Agent / IDE) — resolve the active conversation id from
     `CURSOR_CONVERSATION_ID` (`$env:CURSOR_CONVERSATION_ID` in PowerShell or
     `$CURSOR_CONVERSATION_ID` in a POSIX shell). Pass it explicitly.

     ```
     xexport current --source cursor --session-id "<CURSOR_CONVERSATION_ID>" --format html --mode <MODE> --out "<output-dir>"
     ```

     If `CURSOR_CONVERSATION_ID` is absent, do not guess. Run
     `xexport list --source cursor` and ask the user to identify the intended session id.

   - **Codex** (CLI or desktop) — resolve the active task id from `CODEX_THREAD_ID`
     (`$env:CODEX_THREAD_ID` in PowerShell or `$CODEX_THREAD_ID` in a POSIX shell). Pass
     it explicitly. Do **not** select the newest rollout for the working directory:
     several Codex tasks can share that directory.

     ```
     xexport current --source codex --session-id "<CODEX_THREAD_ID>" --format html --mode <MODE> --out "<output-dir>"
     ```

     If `CODEX_THREAD_ID` is absent, do not guess. Run `xexport list --source codex` and
     ask the user to identify the intended session id.

5. If `xexport` is not on PATH, use the repo directly:

   ```
   uv run --project "C:\Users\Roy Harden\OneDrive\PJ-OD\C2M\C2M-XExport" xexport current ...
   ```

6. Verify that the reported session id equals the requested id, then relay the "wrote:"
   path (or "Up to date") to the user in one line. The export is a snapshot up to the
   moment the command runs; the turn invoking this skill is only partially captured.

## Notes

- Add `--open` if the user wants it opened in the browser immediately.
- `--format both` writes **both** artefacts in their natural homes:
  `.chatexports\<name>.md` and `.chatexports\html\<name>\`. The Markdown is no longer
  nested inside the HTML folder.
- Append finds the previous export by **session id**, not by folder name, so it still
  works after the chat has been retitled.
- `--name "Custom Name"` overrides the title part of the folder name; collisions get " (2)".
- `--html-subdir ""` writes HTML flat into `.chatexports\` (the pre-0.2.0 layout) if the
  user explicitly asks for that.
- Markdown instead: use the sibling skill `xexport-md`.
- To export a session's **subagent** transcripts: `xexport subagents --session-id
  <parent id> --format html --mode append --out "<output-dir>"`. They land in
  `.chatexports\html\` beside the main exports, marked by their `claude-agent-<hex>` id
  suffix; `--subagent-subdir subagents` puts them in their own folder instead.
- To make exports happen automatically for every session and every subagent in a
  project, use the sibling skill `xexport-auto`.
