---
name: xexport-html
description: Export the current chat session to a paginated HTML transcript under .chatexports using the xexport CLI. Use when the user runs /xexport-html (Claude Code or Cursor), $xexport-html (Codex), or asks to export, save, or archive this chat/session/conversation as HTML. In Codex use CODEX_THREAD_ID; in Cursor use CURSOR_CONVERSATION_ID.
---

# xexport-html

Export the CURRENT session to paginated HTML (index.html + page-NNN.html, in the
claude-code-transcripts visual style) via the `xexport` CLI from
`C:\Users\Roy Harden\OneDrive\PJ-OD\C2M\C2M-XExport`.

## Steps

1. Output directory: `$ARGUMENTS` if the user passed one, else the project
   root's `.chatexports` folder (Claude Code: `${CLAUDE_PROJECT_DIR}\.chatexports`;
   Codex / Cursor: `<workspace root>\.chatexports`).
2. Pick the command for your harness:
   - **Claude Code** — the placeholder on the next line is auto-substituted with
     the real session id. If it still looks like a literal `${...}` placeholder,
     you are NOT running in Claude Code; use the Cursor or Codex form instead.

     ```
     xexport current --source claude --session-id ${CLAUDE_SESSION_ID} --format html --out "<output-dir>"
     ```

   - **Cursor** (Agent / IDE) — resolve the active conversation id from
     `CURSOR_CONVERSATION_ID` (`$env:CURSOR_CONVERSATION_ID` in PowerShell or
     `$CURSOR_CONVERSATION_ID` in a POSIX shell). Pass it explicitly.

     ```
     xexport current --source cursor --session-id "<CURSOR_CONVERSATION_ID>" --format html --out "<output-dir>"
     ```

     If `CURSOR_CONVERSATION_ID` is absent, do not guess. Run
     `xexport list --source cursor` and ask the user to identify the intended
     session id.

   - **Codex** (CLI or desktop) — resolve the active task id from
     `CODEX_THREAD_ID` (`$env:CODEX_THREAD_ID` in PowerShell or
     `$CODEX_THREAD_ID` in a POSIX shell). Pass it explicitly. Do **not** select
     the newest rollout for the working directory: several Codex tasks can share
     that directory.

     ```
     xexport current --source codex --session-id "<CODEX_THREAD_ID>" --format html --out "<output-dir>"
     ```

     If `CODEX_THREAD_ID` is absent, do not guess. Run `xexport list --source codex`
     and ask the user to identify the intended session id.

3. If `xexport` is not on PATH, use the repo directly:

   ```
   uv run --project "C:\Users\Roy Harden\OneDrive\PJ-OD\C2M\C2M-XExport" xexport current ...
   ```

4. Verify that the reported session id equals the requested id, then relay the
   "wrote:" paths to the user. The export is a snapshot up to the moment the
   command runs; the turn invoking this skill is only partially captured.

## Notes

- Add `--open` if the user wants it opened in the browser immediately.
- `--name "Custom Name"` overrides the folder name; collisions get " (2)".
- Markdown instead: use the sibling skill `xexport-md`.
