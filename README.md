# C2M-XExport

Export **Claude Code**, **Codex** (Codex CLI / ChatGPT desktop), and **Cursor**
Agent chat sessions to **paginated HTML** or **Markdown** transcripts — from
inside the chat via the `xexport-html` / `xexport-md` skills, or from any
terminal via the `xexport` CLI.

Exports land in `<project-root>\.chatexports\<chat title>` by default, named after the
chat title shown in the apps (Cursor uses the first user prompt as the title).

## Install

```powershell
# from the repo root
uv tool install .          # installs the `xexport` command on PATH
# during development:
uv tool install --force -e .
```

## CLI usage

```text
xexport                       # interactive picker across Claude + Codex + Cursor
xexport current               # export the active session (CURSOR_CONVERSATION_ID / CODEX_THREAD_ID)
xexport list                  # recent sessions: title, id, date, source
xexport <session-id>          # export a specific session (any source)
xexport <path\to\file.jsonl>  # export a transcript file directly

Options:
  --source claude|codex|cursor|auto   which store to look in     (default: auto)
  --format html|md|both        output format                    (default: html)
  --out DIR                    output directory                 (default: .\.chatexports)
  --name NAME                  override the export name         (default: chat title)
  --session-id ID              exact session id (use to force a specific export)
  --brief                      user + assistant text only (no tools / thinking)
  --no-tools / --no-thinking   granular filtering
  --full                       disable truncation of long tool output
  --open                       open index.html when done
  --json                       copy the raw .jsonl next to the output
  --limit N                    list/picker: how many sessions to show
```

Output shapes:

- `--format html` → `.chatexports\<Title>\index.html` + `page-NNN.html` (self-contained,
  works offline, client-side search)
- `--format md` → `.chatexports\<Title>.md`
- `--format both` → the HTML folder, with the `.md` inside it

## In-chat usage

- **Claude Code** (CLI, desktop, VS Code): `/xexport-html` or `/xexport-md`
- **Cursor** (Agent): `/xexport-html` or `/xexport-md` (uses `CURSOR_CONVERSATION_ID`)
- **Codex** (CLI, ChatGPT desktop): `$xexport-html` or `$xexport-md` (Codex invokes
  skills with `$name`; `/skills` lists them)

Skills are thin wrappers around the CLI; the canonical copies live in the PJ-OD skills
directory and are mirrored per-agent by the `sync-skills-across-agents` workflow.

## Where sessions come from

| Source | Transcripts | Chat title |
|---|---|---|
| Claude Code | `~\.claude\projects\<encoded-cwd>\<session-id>.jsonl` | last `ai-title` line in the transcript |
| Codex | `~\.codex\sessions\YYYY\MM\DD\rollout-*.jsonl` (+ `archived_sessions`) | `~\.codex\session_index.jsonl` / `state_5.sqlite` |
| Cursor | `~\.cursor\projects\<encoded-cwd>\agent-transcripts\<uuid>\<uuid>.jsonl` | first user prompt (wrappers stripped) |

Formats are internal to their apps and can change between releases. Parsers are
defensive: unknown entry types become collapsed raw-JSON blocks instead of crashes.

For Cursor Agent, `xexport current` uses `CURSOR_CONVERSATION_ID` when the shell
exposes it. For Codex Desktop, it uses `CODEX_THREAD_ID`. This prevents another
newer task in the same working directory from being exported. If that variable is
unavailable, `current` warns that it is using a working-directory heuristic; use
`--session-id` or `xexport list` in that case.

## Credits

HTML output styling and page structure are adapted from
[claude-code-transcripts](https://github.com/simonw/claude-code-transcripts) by Simon
Willison (Apache-2.0) — see [NOTICE](NOTICE). Markdown shape inspired by the
`codex-export` skill.
