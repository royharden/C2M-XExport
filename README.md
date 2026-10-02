# C2M-XExport

Export **Claude Code**, **Codex** (Codex CLI / ChatGPT desktop), **Cursor**
Agent, and **Grok CLI** chat sessions to **paginated HTML** or **Markdown** transcripts — from
inside the chat via the `xexport-html` / `xexport-md` skills, or from any
terminal via the `xexport` CLI. HTML pages are cut by size, so each one is a single
whole read for an agent's file-read tool.

Exports land in `<project-root>\.chatexports` by default:

```text
.chatexports\
├── Claude_Opus5 -- Update xexport skills -- claude-a68ce6ac-...-d5b23c60437f.md
├── Claude_Sonnet5_Sub -- Summarize canonical skills -- claude-agent-a109fa34.md
└── html\
    └── Claude_Opus5 -- Update xexport skills -- claude-a68ce6ac-...\
        ├── index.html                 <- page map, then one card per prompt
        ├── page-001.html ...          <- full content, each sized for one read
        ├── full.html                  <- everything in one file, for searching
        ├── xexport.css, xexport.js    <- shared by the index and the pages
        └── .xexport-cursor.json       <- what the export was made from
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
xexport                       # interactive picker across Claude + Codex + Cursor + Grok
xexport current               # export the active session (CURSOR_CONVERSATION_ID / CODEX_THREAD_ID / GROK_SESSION_ID)
xexport list                  # recent sessions: title, id, date, source
xexport <session-id>          # export a specific session (any source)
xexport <path\to\file.jsonl>  # export a transcript file directly
xexport subagents             # every subagent transcript of one parent session

Options:
  --source claude|codex|cursor|grok|auto   which store to look in (default: auto)
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

- `--format html` → `.chatexports\html\<name>\` holding `index.html`,
  `page-NNN.html` and `full.html` (works offline; see "HTML layout" below)
- `--format md` → `.chatexports\<name>.md`
- `--format both` → both of the above, each in its own home. **Changed in 0.2.0:** the
  `.md` is no longer nested inside the HTML folder.

## HTML layout

An HTML export is built to be read back by an agent as well as by a person, and an
agent's file-read tool does not return a large file whole. Claude Code's Read tool
(observed 2026-10) returns only the first part of a file over 25,000 tokens by its own
count, refuses a file over about 256 KB outright, and documents a default of 2,000
lines. The tokens run out first: on export HTML it counted 2.0 to 2.6 bytes per token,
so 25,000 tokens is only 50 to 65 KB.

- **`page-NNN.html`** holds the complete content: prompts, answers, thinking, tool
  calls and tool results. A page closes before it reaches **20,000 estimated tokens,
  1,500 lines or 200 KB**, whichever comes first, measured on the file as written.
  Pages break between prompts when they can. A prompt too large for one page is split
  between its messages, and each continuation page says which prompt it continues. A
  single message larger than a page gets a page to itself, marked oversize. Nothing is
  ever truncated or dropped to make a page fit.
- **`index.html`** opens with a **page map**: for each page, its prompts, its message
  anchors, its first timestamp, its lines, KB and estimated tokens, and a note (oversize,
  continuation, a very long line). Below the map is one card per prompt, as before, each
  linking to the page that holds it. On a long session the index is itself larger than
  one read, which is why the map comes first.
- **`full.html`** is every message in one file, with the same `#msg-N` anchors as the
  pages and a marker (`id="page-N"`) where each page starts. It is for searching and
  skimming. On a long session it is far too large for a model to read whole, and says so
  on its second line, ahead of everything else, and again in a banner at the top of the
  page.
- Past 999 pages the name simply grows (`page-1000.html`), so page order comes from the
  page map, not from sorting file names.
- The index and the pages share `xexport.css` and `xexport.js`; `full.html` carries its
  own copy so that it still works when sent on its own.

A page that already has a successor is never rewritten by a later refresh: appending
turns changes the last page, `full.html` and the index, and adds pages. Links into
earlier pages stay valid, and a synced folder re-uploads only what changed.

The budget can be moved with `XEXPORT_PAGE_MAX_TOKENS`, `XEXPORT_PAGE_MAX_LINES` and
`XEXPORT_PAGE_MAX_BYTES`. They are environment variables rather than flags on purpose:
an unknown flag in a hook command is a usage error, and a usage error in a `Stop` hook
blocks the turn. A value that is not a positive whole number is ignored. An export is
re-laid-out whenever the budget it was written with differs from the budget of the run
refreshing it, so set an override for every process that exports the session (the hook
included), or each will undo the other's layout.

Tokens are estimated, not counted: no tokenizer ships with the CLI, and the one that
matters cannot be run locally. The estimate weights each class of character and each
run of letters or digits, and was calibrated against the counts Claude Code's Read tool
reports on 25 kinds of content and 34 real pages;
`scripts/validate_token_estimate.py` holds the calibration data and re-checks it. On
the real pages it is 4% to 19% over. It can still be well under on content unlike
anything it was calibrated on (strings of random lowercase letters are the known case),
and other readers count differently, so the limits are a margin, not a guarantee.

The title shown on a page is capped at 80 characters, and a page carries neither the
xexport version nor the export time, so that retitling a chat or upgrading xexport
never moves a page boundary.

An export written by 0.2.3 or earlier (five prompts per page, no `full.html`) is
re-laid-out in place the next time that session is exported. The reverse is not clean:
an older CLI refreshing a 0.3.0 export rewrites the index and pages in the old layout
and leaves `full.html` behind, stale. Upgrade the installed CLI everywhere an export is
refreshed. An index with no page map at its top marks a folder last written by an older
version.

The `xexport-read` skill tells an agent how to read an export: which file to open
first, how to find the page it needs, and how to slice a file that is too large.

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
  count has not moved. An HTML export also records the layout it was written in, and is
  re-rendered when that differs from the current one even if the conversation has not
  changed.
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

- Reading an export back, in any harness: the `xexport-read` skill.
- Autosave for every session and subagent of a project: the `xexport-auto` skill, which
  writes HTML by default.
- **Claude Code** (CLI, desktop, VS Code): `/xexport-html` or `/xexport-md`
- **Cursor** (Agent): `/xexport-html` or `/xexport-md` (uses `CURSOR_CONVERSATION_ID`)
- **Codex** (CLI, ChatGPT desktop): `$xexport-html` or `$xexport-md` (Codex invokes
  skills with `$name`; `/skills` lists them)

Skills are thin wrappers around the CLI; the canonical copies live in the PJ-OD skills
directory (`PJ-OD\skills\skills-bts\`) and are mirrored per-agent by the `sync-skills-across-agents` workflow.

## Where sessions come from

| Source | Transcripts | Chat title |
|---|---|---|
| Claude Code | `~\.claude\projects\<encoded-cwd>\<session-id>.jsonl` | last `ai-title` line in the transcript |
| Claude Code subagents | `...\<session-id>\subagents\agent-<hex>.jsonl` (+ `.meta.json`) | `description` from the meta sidecar |
| Codex | `~\.codex\sessions\YYYY\MM\DD\rollout-*.jsonl` (+ `archived_sessions`) | `~\.codex\session_index.jsonl` / `state_5.sqlite` |
| Cursor | `~\.cursor\projects\<encoded-cwd>\agent-transcripts\<uuid>\<uuid>.jsonl` | first user prompt (wrappers stripped) |
| Cursor subagents | `...\<uuid>\subagents\*.jsonl` | first user prompt |
| Grok CLI | `~\.grok\sessions\<url-encoded-cwd>\<session-id>\chat_history.jsonl` | `generated_title` (or `session_summary`) in the sibling `summary.json`, else the first prompt |
| Grok subagents | sibling session folders, found through the AgentNamer registry (`parent_id`) | same as Grok CLI |

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


### Native Codex children (0.2.1)

`xexport subagents --source codex --session-id <exact-parent-thread-id>` exports
explicitly linked **direct children** from live and archived rollout stores. For
grandchildren, invoke it with each intermediate child's ID. It does not infer
relationships from workspace, recency, or an inherited environment value.

Native child `session_meta.payload.id` is the export identity; `session_id` may
identify its parent. A child can use `current --source codex --session-id <child-id>`
when its runtime `CODEX_THREAD_ID`, metadata and rollout filename agree. Older
main-session metadata with only `session_id` remains supported. Conflicting IDs,
parent fields or ambiguous references fail before output lookup/write, including
`--mode replace`. An exact ID is required by `current`; `export` still accepts a
unique partial ID. Callsigns never select a conversation. Child labels come from
the opening task's assignment, ignoring inherited process labels.

Verify the reported session ID on every result, including `Up to date`. Version
0.2.0 cannot safely export native children even by exact child ID. Append remains
a full re-render with shrink/filter guards, whose warnings and companion provenance
must be retained. Autosave skills specify active-work checkpoints with a configurable
10-minute maximum age; they do not install persistent timers.


### Full-history native Codex children (0.2.2)

A child may start with its validated envelope followed by copied ancestor metadata.
0.2.1 incorrectly rejected the inherited parent header. 0.2.2 keeps the first envelope
as canonical and accepts only a contiguous, explicitly linked ancestor-header chain
before content. Unrelated, malformed, cyclic or late ancestor metadata still fails.
No rollout repairs or replace-mode bypasses are needed.

Copied history is retained in both formats with an **Inherited context** notice and
ancestor IDs; these are full rollout snapshots, not child-only work records. Because
opening prompts, assignments and models may come from ancestors, inherited snapshots
use neutral `Codex_Sub` labels and only the child's own title index/envelope (or ID).
Fresh-context child callsign detection remains unchanged.
