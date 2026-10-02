# AGENTS.md — C2M-XExport

Python CLI (`xexport`) + agent skills that export Claude Code, Codex, Cursor Agent, and Grok CLI
chat sessions to HTML (size-packed pages, an index with a page map, and one full.html) or Markdown. Sibling of the C2M Chat-to-Markdown
Chrome extension (same mission, different surface: local session stores instead of
browser DOM).

## Ground rules

- Committed docs are lean and stable: README.md, this file, NOTICE, LESSONS.md.
  Agent-only plans, reviews, scratch memory, and generated indexes go in gitignored
  `.agent-work/`. No second truths.
- Recon before edit: the parsed formats (Claude `~\.claude\projects\*.jsonl`, Codex
  rollout jsonl, Cursor `~\.cursor\projects\*\agent-transcripts\*\*.jsonl`, Grok
  `~\.grok\sessions\*\*\chat_history.jsonl`) are
  **internal and version-drifting**. Before changing a parser, re-verify the live
  format on this machine; fixtures in `tests/fixtures/` pin the currently observed
  shape so drift shows up as a failing test.
- Every file open uses `encoding="utf-8"` (reads: `errors="replace"`). Never rely on
  the Windows default codepage — that's the cp1252 crash class this tool exists to avoid.
- Unknown jsonl entry types must degrade to Raw blocks, never crash an export.
- Bug fixed ⇒ regression test added, with a comment saying what bug it catches.
- **Subagent transcripts are sidechains by definition.** Every line of
  `...\<session>\subagents\agent-*.jsonl` carries `isSidechain: true`. The filter that
  keeps a *parent* transcript clean must stay lifted for a subagent's own file — see
  `claude.parse_file(include_sidechain=...)`. Getting this backwards produces an export
  with zero messages and no error, which is how it went unnoticed until 0.2.0.
- **An export is refreshed by re-rendering it, never by appending to it.** Re-parse,
  render the whole document, write it through `publish.atomic_write_text`. That is what
  makes an edited, retried or compacted transcript come out right. If you find yourself
  reaching for `open(path, "a")`, the design has drifted.
- **A refresh may never quietly produce a worse export.** Re-render will happily overwrite
  a good export with a lesser one, so before writing over an existing file check its
  cursor record: refuse a transcript shorter than what was exported, and refuse a
  different set of content filters. Only `--mode replace` overrides those.
- **The content filters are privacy controls, not Markdown styling.** `--brief`,
  `--no-tools` and `--no-thinking` must reach every format a run produces. Filter on the
  neutral model in `render/__init__.filtered`, never in one renderer.
- **An HTML page is one whole read for an agent's file-read tool.** Pages are packed by
  size (`render/html.py`: estimated tokens, lines, bytes; whichever is reached first),
  never by a prompt count, and never by cutting content: an oversize message gets its
  own flagged page. The packing is prefix-stable, so a page that has a successor is
  byte-identical on every later refresh. Keep anything that changes per run (export
  time, page total, a list of all pages) out of the page template, or that stops being
  true. Changing what an export folder holds, or how pages are cut, means bumping
  `HTML_LAYOUT` so existing exports are re-rendered.
- **No new flags in the hook recipes.** An unknown flag against an older installed CLI
  is a usage error (exit 2), which in a `Stop` hook is a blocked turn. Tunables that a
  hook might need are environment variables (`XEXPORT_PAGE_MAX_*`).

## Layout

- `src/xexport/model.py` — neutral IR (Session/Message/Block). Parse once, render twice.
- `src/xexport/sources/` — `claude.py`, `codex.py`, `cursor.py`, `grok.py`: store discovery, title
  resolution, jsonl → IR.
- `src/xexport/render/` — `markdown.py`; `html.py` + `templates/` (adapted from
  claude-code-transcripts, Apache-2.0 — keep the NOTICE attribution). `html.py` writes
  `page-NNN.html`, then `full.html`, then `index.html` last, plus the shared
  `xexport.css` / `xexport.js`; it also owns the page budget, the token estimate and
  the layout version.
- `scripts/validate_token_estimate.py` — dev-only: the calibration data for the token
  estimate and the check against it (`uv run python scripts/validate_token_estimate.py
  --synthetic`; add `--with tiktoken` and an export folder for a per-file table).
- `src/xexport/titles.py` — Windows-safe name sanitization; `detect.py` — current-session detection.
- `src/xexport/naming.py` — export names: the `{agent} -- {title} -- {identity}` template
  and AgentNamer callsign resolution. `agentnamer.py` — read-only lookup of an existing
  callsign registry. `cursors.py` — the record each export keeps of what it was made
  from. `publish.py` — atomic writes and the per-session lock.
- `src/xexport/cli.py` — click CLI; console script `xexport`.
- Skills: canonical copies live in `PJ-OD\skills\skills-bts\xexport-html|md|auto|read\` (NOT in this repo);
  `skills/` here holds the templates they are generated from. Follow the
  `sync-skills-across-agents` skill for any skill change.

## Commands

```
uv sync                 # create venv + install deps
uv run pytest           # tests must be green before commit
uv run xexport --help
uv tool install --force -e .   # refresh the global command after changes
```


## Native Codex identity (0.2.1, 2026-09-09)

- Native children use `session_meta.payload.id`; inherited `session_id` is not
  their identity. Keep `thread_spawn.parent_thread_id` and `agent_path` separately.
- Validate requested identity, filename, and metadata before export lookup/write.
  `replace` never bypasses identity validation. Discovery includes direct children
  only, including archived rollouts; test grandchildren exclusion explicitly.
- Skill source remains `PJ-OD/skills/skills-bts/xexport-{md,html,auto}/SKILL.md`; `skills/` is
  the synchronized template copy. Use `sync-skills-across-agents` after changes.


## Inherited Codex metadata (0.2.2, 2026-09-09)

A native child can contain copied ancestor headers immediately after its own envelope.
Keep the first validated identity; admit only the contiguous explicitly linked ancestor
chain before content. Preserve copied history with provenance. Never infer a child's
callsign/title/model from ancestor opening records. Regressions:
`tests/test_codex_inherited_history.py`. Skill updates retain the canonical paths above.
