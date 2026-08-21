# AGENTS.md — C2M-XExport

Python CLI (`xexport`) + agent skills that export Claude Code, Codex, and Cursor Agent
chat sessions to paginated HTML or Markdown. Sibling of the C2M Chat-to-Markdown
Chrome extension (same mission, different surface: local session stores instead of
browser DOM).

## Ground rules

- Committed docs are lean and stable: README.md, this file, NOTICE, LESSONS.md.
  Agent-only plans, reviews, scratch memory, and generated indexes go in gitignored
  `.agent-work/`. No second truths.
- Recon before edit: the parsed formats (Claude `~\.claude\projects\*.jsonl`, Codex
  rollout jsonl, Cursor `~\.cursor\projects\*\agent-transcripts\*\*.jsonl`) are
  **internal and version-drifting**. Before changing a parser, re-verify the live
  format on this machine; fixtures in `tests/fixtures/` pin the currently observed
  shape so drift shows up as a failing test.
- Every file open uses `encoding="utf-8"` (reads: `errors="replace"`). Never rely on
  the Windows default codepage — that's the cp1252 crash class this tool exists to avoid.
- Unknown jsonl entry types must degrade to Raw blocks, never crash an export.
- Bug fixed ⇒ regression test added, with a comment saying what bug it catches.

## Layout

- `src/xexport/model.py` — neutral IR (Session/Message/Block). Parse once, render twice.
- `src/xexport/sources/` — `claude.py`, `codex.py`, `cursor.py`: store discovery, title
  resolution, jsonl → IR.
- `src/xexport/render/` — `markdown.py`; `html.py` + `templates/` (adapted from
  claude-code-transcripts, Apache-2.0 — keep the NOTICE attribution).
- `src/xexport/titles.py` — Windows-safe name sanitization; `detect.py` — current-session detection.
- `src/xexport/cli.py` — click CLI; console script `xexport`.
- Skills: canonical copies live in `PJ-OD\skills\xexport-html|md\` (NOT in this repo);
  `skills/` here holds the templates they are generated from. Follow the
  `sync-skills-across-agents` skill for any skill change.

## Commands

```
uv sync                 # create venv + install deps
uv run pytest           # tests must be green before commit
uv run xexport --help
uv tool install --force -e .   # refresh the global command after changes
```
