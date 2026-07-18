---
name: xexport-html
description: Export the current chat session to a paginated HTML transcript in .chatexports/<chat title>/ using the xexport CLI. Use when the user runs /xexport-html (Claude Code) or $xexport-html (Codex), or asks to export, save, or archive this chat/session/conversation as HTML.
argument-hint: "[output-dir]"
allowed-tools: ["Bash"]
---

# xexport-html

Export the CURRENT session to paginated HTML (index.html + page-NNN.html, in the
claude-code-transcripts visual style) via the `xexport` CLI from
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
     xexport current --source claude --session-id ${CLAUDE_SESSION_ID} --format html --out "<output-dir>"
     ```

   - **Codex** (CLI or desktop) — current-session detection matches the newest
     rollout for this working directory:

     ```
     xexport current --source codex --format html --out "<output-dir>"
     ```

3. If `xexport` is not on PATH, use the repo directly:

   ```
   uv run --project "C:\Users\Roy Harden\OneDrive\PJ-OD\C2M\C2M-XExport" xexport current ...
   ```

4. Relay the "wrote:" paths from the output to the user. The export is a
   snapshot up to the moment the command runs; the turn invoking this skill is
   only partially captured.

## Notes

- Add `--open` if the user wants it opened in the browser immediately.
- `--name "Custom Name"` overrides the folder name; collisions get " (2)".
- Markdown instead: use the sibling skill `xexport-md`.
