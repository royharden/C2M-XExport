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
- re-running is near-free: an unchanged turn is a no-op,
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
- **`--out` must name the project root explicitly.** Without it the output directory is
  `cwd/.chatexports`, and a hook's working directory is not something to assume. But
  `$CLAUDE_PROJECT_DIR` is only right when the harness was opened at the project root.
  In Roy's layout the project root is the outer shell (`PJ-OD\C2M\`) and the repo sits
  inside it, so a session opened in the repo makes `$CLAUDE_PROJECT_DIR` the *repo* --
  and the hook would then write transcripts into the repo's working tree, which is the
  one outcome to avoid. **Resolve the project root when you install the hook and write
  that absolute path into the command**, rather than relying on the variable. Confirm it
  against where that project's existing `.chatexports\` already is.
- **`--callsign auto`** is the default, so the recipes below do not need it. xexport
  reads an existing registry directly — it never runs `claim.py`, and never creates or
  mutates a registry. For a main session it matches the session id against the registry's
  `ids/*.json`, ignoring `sub` records; for a subagent it reads only the
  "Your callsign is …" line its parent wrote, because a subagent shares its parent's
  session id and a registry lookup would answer with the parent's name. A project with no
  registry produces no prefix, which is the correct result.
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
- **Do not add `.chatexports\` to a `.gitignore` on your own initiative.** Roy's layout
  puts it at the *project root* -- the outer shell folder, one level below the projects
  directory (`PJ-OD\C2M\`, `PJ-OD\PracticeHub\`) -- while the repo or repos live
  *inside* that. A project may have more than one repo. Some projects deliberately keep a
  **private** repo at the project-root level whose whole job is to capture private
  material including `.chatexports`, so ignoring it there defeats the point.

  The thing that actually matters is that transcripts never land inside a **public**
  repo's working tree. Get that right by exporting to the project root, not by ignoring
  files after the fact. Ask before touching any `.gitignore`.

`--brief` (user and assistant text only) remains available for any project where that
trade lands differently.

### The payload, as measured

Captured from a real `SubagentStop` on 2026-09-07 by installing a hook that wrote its
stdin to a file. **Do not take this from documentation** — a docs summary consulted the
same day got two of these backwards, and both are the ones that matter:

| field | value on `SubagentStop` |
|---|---|
| `transcript_path` | the **parent's** transcript, *not* the child's |
| `agent_transcript_path` | the **child's** own transcript (`…/<parent-id>/subagents/agent-<hex>.jsonl`) |
| `session_id` | the **parent's** session id |
| `agent_id` | the child's own id (`a4795223d8b0d70c1`) |
| `last_assistant_message` | the child's final text |
| `cwd` | the project root the session was opened at |
| `stop_hook_active` | `false` — set when a `Stop` hook is already running, so a hook can avoid recursing |

Also present: `hook_event_name`, `agent_type`, `prompt_id`, `permission_mode`, `effort`,
`scratchpad_dir`, `background_tasks`, `session_crons`.

That first row is the whole reason `xexport subagents --from-hook` refuses a path that is
not under a `subagents\` directory: a `SubagentStop` hook that trusted `transcript_path`
would export the **parent** into the file the `Stop` hook maintains, and report that no
subagents were found. It reads `agent_transcript_path` first and treats the rest as
fallback.

**Hooks are re-read from disk mid-session** — a file watcher picks up edits to
`settings.json`, confirmed by the capture above firing without a restart. Convenient for
testing, and worth knowing before you edit a live project's settings.

**Exit codes:** only **2** blocks a `Stop` and feeds stderr back to the model; 1 and other
non-zero codes are non-blocking errors. That is why an unknown flag (click's usage error,
exit 2) is the dangerous one and `|| exit 0` is the backstop.

**Re-verify the payload before relying on it in a new build.** Install a hook that writes
its stdin to a file, trigger one event, read it back — that is how the table above was
made. If `--from-hook` still picks the wrong session, fall back to the payload-independent
form: `xexport current --session-id "$CLAUDE_CODE_SESSION_ID" ...`.

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
- In a hook there is no agent to ask, and none is needed: `auto` is the default and
  reads the registry directly. A project without a registry produces no prefix at all,
  which is the correct result.
- `XEXPORT_CALLSIGN` overrides the lookup for a **main** session. It is deliberately
  ignored for subagent receipts: it describes the process the parent is running in, and
  labelling a child's receipt with the parent's name misattributes what an agent said.

## Checklist

1. **Confirm `xexport --version` reports 0.2.0 or later** before anything else.
   `--mode`, `--from-hook`, `--callsign` and `subagents` do not exist in 0.1.1, and a
   hook built on them against an older CLI is the exit-2 turn loop described above.
   While you are there, dry-run the exact recipe you are about to install and check it
   exits 0:

   ```
   echo '{"session_id":"<a real session id>"}' | xexport current --from-hook --format md --quiet --out .chatexports
   ```

2. **Resolve the executable the *hook* process can run, not the one your shell can.**
   `xexport` lives in `~\.localin`, which is on an interactive shell's PATH but is
   not guaranteed to be on the PATH of a GUI-launched harness — and a hook that cannot
   find its command fails on every turn while looking, from the terminal, perfectly
   installed. Resolve it (`(Get-Command xexport).Source`, or `command -v xexport`) and
   put the **absolute path** in the hook command if there is any doubt. Verify by
   running the hook from the app you actually use, not from a terminal.
3. Confirm the export root is the **project root** (the outer shell), not a repo inside
   it. These receipts are full-fidelity, so a transcript landing in a public repo's
   working tree is the failure that matters -- not whether it is gitignored. Do not edit
   any `.gitignore` without asking.
4. Find every `.claude\settings.json` in the project and decide which one owns the
   policy. Install in exactly one.
5. Back up that file, merge the hooks, restart the session, and confirm a file appears
   in `.chatexports\` after the next turn.
6. Other harnesses: append the marked boot section to `AGENTS.md`.
7. Run one subagent and confirm its transcript lands in `.chatexports\` beside the main
   exports, named `... -- claude-agent-<hex>.md` — and that the parent transcript did
   **not** get a second copy written under that name.
8. Follow `sync-skills-across-agents` if the project keeps skill mirrors.
