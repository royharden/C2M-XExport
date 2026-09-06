---
name: xexport-auto
description: Install a standing chat-autosave policy in a project so every session — and every subagent — is exported to .chatexports without anyone asking. Use when the user wants chats saved automatically, mentions "always export", "autosave the chat", "save subagent chats", "run xexport at the start of every session", or asks to add chat export to a project's priming/constitution skill, AGENTS.md, CLAUDE.md, or Claude Code hooks. Not for exporting one chat right now — that is xexport-md / xexport-html.
---

# xexport-auto

`xexport-md` and `xexport-html` export **this** chat, now. This skill installs a standing
policy in a project so exports happen **without being asked**, including for subagents
nobody can talk to directly.

Two mechanisms, because only one harness has hooks:

| Harness | Mechanism | Can it be forgotten? |
|---|---|---|
| **Claude Code** | hooks in `.claude\settings.json` | No — the harness runs them |
| Codex / Cursor | a marked boot section in the project's `AGENTS.md` | Only if the agent ignores its instructions |

**Grok is not supported.** The `xexport` CLI reads the Claude Code, Codex and Cursor
session stores only; there is no Grok store to export. Grok CLI reads `AGENTS.md`, so it
will *see* the boot section — say plainly that it does not apply to it rather than
letting an agent try and fail.

## Read this before installing: "export at the start" does not do what it sounds like

At session start the transcript is empty — there is nothing to export, and a start-time
export writes a file whose only content is a header. The actual goal ("never lose a
chat") is delivered by exporting **at the end of every turn** with `--mode append`:

- the file exists from the first turn onward,
- it is current after every turn, so a crash loses nothing,
- `--mode append` only renders the turns that are new, so re-running is near-free,
- and with no new turns it is a no-op that does not even touch the file.

If the user explicitly wants a file to exist the moment a session opens, say what that
file will contain and let them decide.

## Claude Code: the hooks

**First: find out which `.claude\settings.json` owns the policy.** A project can have
more than one — a repo root and a nested subproject each with their own. Installing at
the nested level, or at both, writes transcripts into two different `.chatexports` trees.
Pick one deliberately and say which in the project's AGENTS.md.

Then merge, **backing the file up first**. If AgentNamer has already merged its hooks
into this project, copy the exact shape from there rather than trusting the sketch below —
that file is a known-good example.

```jsonc
{
  "hooks": {
    "Stop": [
      { "hooks": [ { "type": "command",
        "command": "xexport current --from-hook --format md --mode append --quiet --callsign auto --out \"$CLAUDE_PROJECT_DIR/.chatexports\" || exit 0"
      } ] }
    ],
    "SubagentStop": [
      { "hooks": [ { "type": "command",
        "command": "xexport subagents --from-hook --format md --mode append --quiet --callsign auto --out \"$CLAUDE_PROJECT_DIR/.chatexports\" || exit 0"
      } ] }
    ]
  }
}
```

Every part of that command line is load-bearing:

- **`|| exit 0` is not optional.** A `Stop` hook that exits non-zero blocks the stop and
  feeds its stderr back to the model, producing another turn, which fires the hook again.
  An unknown flag is a click usage error (exit 2), and that is reachable in practice —
  update the skills before the CLI, or roll back an editable install, and every turn
  becomes a loop. `|| exit 0` makes the hook incapable of doing that.
- **`--out "$CLAUDE_PROJECT_DIR/.chatexports"`.** Without it the output directory is
  `cwd/.chatexports`, and the hook's working directory is not something to assume — least
  of all in a project with nested settings files.
- **`--callsign auto`** — there is no agent in the loop to pass its own callsign. For a
  subagent this reads the "Your callsign is …" line its parent wrote; for a main session
  it asks AgentNamer's `whoami`. A project with no registry produces no prefix, which is
  the correct result. If `claim.py` is not under `~\.claude\skills\AgentNamer\`, point
  `XEXPORT_AGENTNAMER` at the canonical copy — that is what the existing AgentNamer hooks
  in this ecosystem do.
- **`--from-hook`** reads the hook JSON on stdin. `current` uses `transcript_path` when
  present, else `session_id`, else normal detection. `subagents` accepts a
  `transcript_path` **only** if it really is a subagent transcript, because a
  `SubagentStop` payload may carry the main transcript — trusting it blindly would export
  the parent while reporting that no subagents were found.
- **`--mode append`** refreshes this session's export rather than writing a second file:
  it re-renders the whole transcript and swaps it in atomically, so the receipt always
  reads as one continuous document with no per-turn seams. A turn that changed nothing
  does not touch the file at all.
- **`last_assistant_message`** in a `Stop` payload is spliced in when the transcript does
  not carry the final answer yet, so a per-turn receipt is not permanently one answer
  behind. It is never applied to a subagent receipt, because a `SubagentStop` payload may
  carry the *parent's* last message.
- **`--quiet`** keeps hook output out of the conversation. It suppresses `Note:` lines but
  never `Warning:` — a fork always stays visible.
- Want HTML too: add `--format both`. The Markdown stays at `.chatexports\<name>.md` and
  the HTML goes to `.chatexports\html\<name>\`.

### Fidelity: these receipts are complete, not summaries

The recipe above records **everything** — reasoning, every tool call, every tool result.
That is a deliberate choice (Roy, 2026-09-04): a receipt that omits the tool work omits
most of what a subagent actually did, and the two risks that argue for trimming it are
already handled here — project roots go only to private git, and OneDrive backup of
secrets is intentional.

Two things that still follow from it, and should be said out loud when setting this up in
a **new** project rather than assumed:

- An export file carries whatever was on screen. Sending one to someone, or attaching it
  to an issue, sends the credentials and customer data that passed through that chat too.
- `.chatexports\` must be gitignored unless the user wants transcripts committed. It is
  **not** currently ignored in Chat-to-Markdown or PracticeHub. Check, do not assume.

`--brief` (user and assistant text only) remains available for any project where that
trade lands differently.

**Verify the payload before relying on it.** Run the hook command by hand once with a
sample payload and confirm the file it writes is the session you expect:

```bash
echo '{"session_id":"<a real session id>"}' | xexport current --from-hook --format md --mode append --out .chatexports
```

If `--from-hook` picks the wrong session, fall back to the payload-independent form:
`xexport current --session-id "$CLAUDE_CODE_SESSION_ID" ...`.

### Alternatives to `Stop`

- `SessionEnd` fires once per session — quieter, but it does not fire when the process
  dies, so a crashed session is lost. Use it only if the per-turn cost is a problem.
- `SessionStart` is not recommended; see the section above.

### Cost

One parse of the session jsonl per turn. Measure it on the largest real transcript in the
project before installing this in a repo with very long sessions:

```bash
xexport current --session-id <big session> --format md --mode append --out .chatexports
```

## Codex / Cursor: the boot section

These harnesses have no hooks, so the instruction file is the mechanism — exactly as
AgentNamer does it. (Grok CLI reads `AGENTS.md` too, but xexport cannot read Grok
sessions, so the block says so rather than sending it on a fool's errand.) Append a marked
block to the project's root `AGENTS.md` (and make sure `CLAUDE.md` imports `AGENTS.md`, or
carries the block itself):

```markdown
<!-- xexport-auto:begin -->
## Chat autosave

Before your final reply in any session, export this chat:

    xexport current --source <claude|codex|cursor> --session-id "<your session id>" \
        --format md --mode append --out "<project root>\.chatexports"

- Session id: `CODEX_THREAD_ID` (Codex), `CURSOR_CONVERSATION_ID` (Cursor),
  `CLAUDE_CODE_SESSION_ID` (Claude Code).
- Add `--callsign "<your callsign>"` if you hold an AgentNamer callsign.
- Running it mid-session is free: `--mode append` writes only what is new, and prints
  "Up to date" when there is nothing to add.
- If you spawned subagents, also run:
  `xexport subagents --session-id "<your session id>" --format md --mode append --out "<project root>\.chatexports"`
<!-- xexport-auto:end -->
```

Keep the `xexport-auto:begin/end` markers: they are what makes re-running this skill
idempotent instead of appending a second copy.

## Adding it to a priming / constitution skill

If the project has a priming skill (`prime-*`), the autosave instruction belongs in the
**boot section of `AGENTS.md`**, not in the priming skill body. The priming skill runs
when an agent invokes it; the instruction file is read by every session of every harness
whether or not anyone remembers to prime. Point the priming skill at the boot section
rather than duplicating the commands — two copies of a command string is a second truth,
and they will drift.

## With AgentNamer

The two are independent: AgentNamer names agents, xexport names files. The join is one
optional flag.

- An agent that holds a callsign passes `--callsign "<its callsign>"`, and the export is
  named `0007_Claude_Opus5 -- <chat title> -- claude-<session id>.md`. Without a
  callsign the same field falls back to `<Harness>_<Model>`, so every export still names
  its agent and adopting AgentNamer later only adds the id.
- In a hook there is no agent to ask, so use `--callsign auto`: it runs AgentNamer's
  `whoami` and, for a subagent transcript, falls back to reading the
  "Your callsign is …" line the parent wrote at the top of the subagent's prompt.
  A project without a registry produces no prefix at all, which is the correct result.
- Set `XEXPORT_CALLSIGN` in the environment instead if you prefer not to repeat the flag.

## Checklist

1. **Confirm `xexport --version` reports 0.2.0 or later** before anything else.
   `--mode`, `--from-hook`, `--callsign` and `subagents` do not exist in 0.1.1, and a
   hook built on them against an older CLI is the exit-2 turn loop described above.
2. Confirm `.chatexports\` is in the project's `.gitignore`. Add it if not — do not skip
   this because "it probably is". These receipts are full-fidelity (see above), so an
   unignored `.chatexports\` commits tool output and anything sensitive in it.
3. Find every `.claude\settings.json` in the project and decide which one owns the
   policy. Install in exactly one.
4. Back up that file, merge the hooks, restart the session, and confirm a file appears
   in `.chatexports\` after the next turn.
5. Other harnesses: append the marked boot section to `AGENTS.md`.
6. Run one subagent and confirm its transcript lands in `.chatexports\` beside the main
   exports, named `... -- claude-agent-<hex>.md` — and that the parent transcript did
   **not** get a second copy written under that name.
7. Follow `sync-skills-across-agents` if the project keeps skill mirrors.
