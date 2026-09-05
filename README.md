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
  --mode new|append|replace    fresh / add only new turns / overwrite   (default: append)
  --append                     shorthand for --mode append
  --seamless                   in append mode, omit the "Addendum N" header
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

## Incremental exports

Every export records where it stopped, inside the export itself: an HTML comment on the
last line of a Markdown transcript, `.xexport-cursor.json` inside an HTML folder.

```text
xexport current --format md --mode append
```

`--mode append` is the default. It finds this session's existing export **by session
id** — so it still works after the chat has been retitled — and adds only the turns that
are new, under a `## Addendum N` heading (`--seamless` omits the heading). If there is no
export yet it creates one. With nothing new it prints "Up to date" and does not touch the
file, which makes it safe to run on every turn from a hook. `--mode new` writes a
separate full copy instead.

If the transcript no longer lines up with where the last export stopped, append refuses
and writes a fresh export with a warning naming both files: a duplicate file is
recoverable, a transcript with a hole in it is not. Note that after a deliberate
`--mode new` copy, later appends follow the newest file — the snapshot becomes the living
document and the original freezes.

## Subagents

A Claude Code subagent shares its parent's session id, so it cannot export itself.
Export the whole set from the parent, or from a `SubagentStop` hook:

```text
xexport subagents --session-id <parent session id> --format md
```

Transcripts land beside the main exports, titled from the one-line task description the
parent recorded in the transcript's `.meta.json` sidecar. `--subagent-subdir subagents`
puts them in their own folder instead. Combined with the default append mode the command
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
