---
name: xexport-md
description: Export the current chat session to a single Markdown transcript in .chatexports/<chat title>.md using the xexport CLI. Use when the user runs /xexport-md (Claude Code) or $xexport-md (Codex), or asks to export, save, or archive this chat/session/conversation as Markdown.
argument-hint: "[output-dir]"
allowed-tools: ["Bash"]
---

# xexport-md

Export the CURRENT session to a single Markdown file via the `xexport` CLI from
`C:\Users\Roy Harden\OneDrive\PJ-OD\C2M\C2M-XExport`.

## Steps

1. Output directory: `$ARGUMENTS` if the user passed one, else the project
   root's `.chatexports` folder (in Claude Code that is
   `${CLAUDE_PROJECT_DIR}\.chatexports`; in Codex use `<workspace root>\.chatexports`).
2. Pick the command for your harness:
   - **Claude Code** — the placeholder on the next line is auto-substituted with
     the real session id. If it still looks like a literal `${...}` placeholder,
     you are NOT running in Claude Code; use the Codex form instead.

     ```
     xexport current --source claude --session-id ${CLAUDE_SESSION_ID} --format md --out "<output-dir>"
     ```

   - **Codex** (CLI or desktop) — current-session detection matches the newest
     rollout for this working directory:

     ```
     xexport current --source codex --format md --out "<output-dir>"
     ```

3. If `xexport` is not on PATH, use the repo directly:

   ```
   uv run --project "C:\Users\Roy Harden\OneDrive\PJ-OD\C2M\C2M-XExport" xexport current ...
   ```

4. Relay the "wrote:" path from the output to the user. The export is a
   snapshot up to the moment the command runs.

## Notes

- `--brief` gives user + assistant text only (no tool calls or thinking).
- `--full` disables truncation of long tool output.
- `--name "Custom Name"` overrides the file name; collisions get " (2)".
- Paginated HTML instead: use the sibling skill `xexport-html`.
