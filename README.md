# C2M-XExport

Export **Claude Code**, **Codex** (Codex CLI / ChatGPT desktop), and **Cursor**
Agent chat sessions to **paginated HTML** or **Markdown** transcripts — from
inside the chat via the `xexport-html` / `xexport-md` skills, or from any
terminal via the `xexport` CLI.

Exports land in `<project-root>\.chatexports` by default:

```text
.chatexports\
├── Claude_Opus5 -- Update xexport skills -- claude-a68ce6ac-...-d5b23c60437f.md
├── Claude_Sonnet5_Sub -- Summarize canonical skills -- claude-agent-a109fa34.md
└── html\
    └── Claude_Opus5 -- Update xexport skills -- claude-a68ce6ac-...\   <- index + page-NNN
```

Markdown sits at the root and HTML one level down in `html\`, so a folder full of HTML
exports never buries the Markdown files. Subagent transcripts sit flat beside the main
exports; their `agent-<hex>` id marks them.

The name is `{agent} -- {title} -- {identity}`: the agent that ran the export (its
AgentNamer callsign where the project has a registry, otherwise `<Harness>_<Model>`), the
chat title shown in the apps, and `<source>-<session id>`. The id is never truncated, so
`xexport <any unique part of it>` resolves straight back to the source transcript.

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
xexport subagents             # every subagent transcript of one parent session

Options:
  --source claude|codex|cursor|auto   which store to look in     (default: auto)
  --format html|md|both        output format                    (default: html)
  --out DIR                    output directory                 (default: .\.chatexports)
  --name NAME                  override the {title} field       (default: chat title)
  --mode new|append|replace    fresh copy / refresh this session's export / force
                               the refresh past its guards          (default: append)
  --append                     shorthand for --mode append
  --callsign NAME|auto         AgentNamer callsign for the {agent} name field
  --name-template TEXT         default: {agent} -- {title} -- {identity}
  --html-subdir TEXT           default: html   ("" writes HTML flat, as in 0.1.x)
  --subagent-subdir TEXT       default: "" (subagents sit beside the main exports)
  --from-hook                  read Claude Code hook JSON on stdin
  --quiet                      suppress normal output (warnings and errors still show)
  --session-id ID              exact session id (use to force a specific export)
  --brief                      user + assistant text only (no tools / thinking)
  --no-tools / --no-thinking   granular filtering
  --full                       disable truncation of long tool output
  --open                       open index.html when done
  --json                       copy the raw .jsonl next to the output
  --limit N                    list/picker: how many sessions to show
```

Output shapes:

- `--format html` → `.chatexports\html\<name>\index.html` + `page-NNN.html`
  (self-contained, works offline, client-side search)
- `--format md` → `.chatexports\<name>.md`
- `--format both` → both of the above, each in its own home. **Changed in 0.2.0:** the
  `.md` is no longer nested inside the HTML folder.

## Keeping an export current

```text
xexport current --format md
```

`--mode append` is the default, and it **refreshes** this session's export: it re-parses
the transcript, re-renders the whole document, and swaps it in atomically. Re-rendering
rather than appending is what makes an edited, retried or compacted transcript come out
right — an append can only notice that the source no longer lines up and refuse.

The export is found by the `{identity}` suffix in its name, so it is still found after
the chat has been retitled, and the file is **renamed** to follow the new title. If there
is no export yet, one is created. `--mode new` writes a separate full copy instead, and a
numbered copy is never adopted as the file to refresh.

Each export records what it was made from: an HTML comment on the last line of a Markdown
transcript, `.xexport-cursor.json` inside an HTML folder. That record is what makes the
next refresh safe, and it is checked before anything is overwritten:

- **Nothing changed** → "Up to date", and the file is not opened for writing at all, so
  its mtime does not move. Safe to run on every turn from a hook. Change detection is a
  digest over every message and block, so an edited turn is caught even when the message
  count has not moved.
- **The transcript is now shorter than the export** → refused. This is the one case a
  re-render would lose content, so the longer export is kept and the shorter one written
  beside it with a warning naming both.
- **The export was written with different content filters** → refused, rather than
  silently rewriting a full-fidelity transcript as `--brief` or the reverse. An autosave
  hook and a hand-run export resolve to the same file, so this happens in ordinary use.

`--mode replace` overrides both refusals, which is what "overwrite it" means.

## Subagents

A Claude Code subagent shares its parent's session id, so it cannot export itself.
Export the whole set from the parent, or from a `SubagentStop` hook:

```text
xexport subagents --session-id <parent session id> --format md
```

Transcripts land beside the main exports, titled from the one-line task description the
parent recorded in the transcript's `.meta.json` sidecar. `--subagent-subdir subagents`
puts them in their own folder instead. Combined with the default refresh mode the command
is idempotent, so re-running it costs nothing.

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
| Claude Code subagents | `...\<session-id>\subagents\agent-<hex>.jsonl` (+ `.meta.json`) | `description` from the meta sidecar |
| Codex | `~\.codex\sessions\YYYY\MM\DD\rollout-*.jsonl` (+ `archived_sessions`) | `~\.codex\session_index.jsonl` / `state_5.sqlite` |
| Cursor | `~\.cursor\projects\<encoded-cwd>\agent-transcripts\<uuid>\<uuid>.jsonl` | first user prompt (wrappers stripped) |
| Cursor subagents | `...\<uuid>\subagents\*.jsonl` | first user prompt |

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
