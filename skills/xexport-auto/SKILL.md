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

**Grok CLI (xexport 0.2.3+)** reads `~/.grok/sessions/<encoded-cwd>/<GROK_SESSION_ID>/chat_history.jsonl`.
Use `--source grok` and `GROK_SESSION_ID`. Grok has Stop/SubagentStop hooks (`.grok/hooks/`);
install the same recipe as Claude, with `--source grok` and an absolute `--out`. Grok children
have their own session ids; `subagents --source grok` finds them via AgentNamer `parent_id`.

## Freshness during active work

For an installed autosave policy, refresh at session start (an empty session may
produce only a header), after each completed work batch or child handoff, and before
final reporting. During active work, check age at the next available execution step:
use a configurable maximum age, default **10 minutes**. Refresh stale exports on user
status requests. No timer can guarantee capture of work not yet written by the harness.

Record the last successful verified export time separately for parent and children,
plus pending failures, in the task's existing status/handoff notes. Carry these and
the next freshness checkpoint across compaction. Advance success time only after
the reported identity matches; a guard-created companion must be recorded with its
warning and provenance. Export failure does not complete production work or disappear
from progress tracking. On a status request, report last success and outstanding
child failures when relevant.

These are agent execution checkpoints. Do not install a watcher or scheduled
automation unless the user authorizes one. Claude's Stop/SubagentStop hooks provide
additional turn/completion checkpoints, but do not keep a single long active turn fresh.

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
  "Your callsign is …" line its parent wrote. Claude child session fields can
  identify the parent; native Codex uses the distinct child `id`. In either case,
  inherited process labels must not replace the child's assigned callsign. Without a callsign, the label falls back to the harness and available model.
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
- `SessionStart` can create an initial receipt; ongoing freshness still needs the checkpoints above.

### Cost

One parse of the session jsonl per turn. Measure it on the largest real transcript in the
project before installing this in a repo with very long sessions:

```bash
xexport current --session-id <big session> --format md --mode append --out .chatexports
```

## Codex / Cursor: the boot section

These harnesses have no hooks, so the instruction file is the mechanism — exactly as
AgentNamer does it. Grok CLI both reads `AGENTS.md` and has Stop hooks; keep the boot
section so a missed hook still exports. Append a marked
block to the project's root `AGENTS.md` (and make sure `CLAUDE.md` imports `AGENTS.md`, or
carries the block itself):

```markdown
<!-- xexport-auto:begin -->
## Chat autosave

Export at session start, after each completed batch or child handoff, before final
reporting, and at the next available execution step when the last verified export
is older than 10 minutes (configurable). On a user status request, refresh if stale.

    xexport current --source <claude|codex|cursor|grok> --session-id "<exact own session id>" \
        --format md --mode append --out "<project root>\.chatexports"

- Session id: `CODEX_THREAD_ID` (Codex), `CURSOR_CONVERSATION_ID` (Cursor),
  `CLAUDE_CODE_SESSION_ID` (Claude Code), `GROK_SESSION_ID` (Grok). Do not use recency when an ID is absent.
- Verify the returned session ID on every result, including "Up to date"; exit 0
  alone is insufficient. On missing/mismatched identity, preserve existing exports,
  stop this path, inspect only the intended session metadata, and report the failure.
  Never use `--mode replace` to repair identity.
- Add `--callsign "<your callsign>"` for your own main session if assigned. A callsign
  affects labeling only. Fresh-context child exports use their own opening assignment.
  Full-history child exports preserve and label inherited context; they use neutral
  `Codex_Sub` labeling when the child's assignment cannot be separated from copied history.
- `--mode append` re-parses and re-renders the whole transcript with shrink/fidelity
  guards. It is not literal line appending; unchanged content is a no-op. Preserve
  guard warnings verbatim, protected originals, and any companion's provenance.
- For children, require xexport **0.2.2+** on Codex. Parent-driven export is:
  `xexport subagents --source <claude|codex|cursor> --session-id "<exact parent id>" --format md --mode append --out "<project root>\.chatexports"`.
  Codex discovery includes direct children only; run again for each child with
  children of its own. Verify each returned ID against the explicitly linked child.
- Native Codex children may self-export when runtime `CODEX_THREAD_ID` matches
  metadata `payload.id` and the rollout filename. Metadata `session_id` and runtime
  `CODEX_SESSION_ID` can identify the parent. If the runtime ID is inherited/missing,
  have the parent use the verified exact child thread ID. Before 0.2.2, even an exact
  child ID is unsafe: report the unsupported path instead of attempting the write.
- Claude/Cursor children without a verified independent identity rely on their
  parent/hook; do not run `current` with an inherited parent ID and a child's callsign.
- Preserve parent/child last-success times, pending failures, and next freshness
  checkpoint in existing task status/handoff notes across compaction. Update success
  only after verification; report stale/failed children on status requests. This is
  execution policy, not authorization to install a watcher or scheduled automation.
- A receipt path you quote to another agent is a location, not permission to read it:
  transcripts hold the user's prompts and tool output.
<!-- xexport-auto:end -->
```

Keep the `xexport-auto:begin/end` markers: they are what makes re-running this skill
idempotent instead of appending a second copy.

**Migration:** replace an existing marked block instead of appending a second one.
Older blocks prohibit all Codex child self-export based on an inherited-ID assumption;
that does not describe native Codex children with independently verified thread IDs.
Update only projects in the user's requested scope; do not sweep unrelated constitutions.

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
  reads the registry directly. Without a callsign, labels fall back to the harness and available model.
- `XEXPORT_CALLSIGN` overrides the lookup for a **main** session. It is deliberately
  ignored for subagent receipts: it describes the process the parent is running in, and
  labelling a child's receipt with the parent's name misattributes what an agent said.

## Checklist

1. **Confirm `xexport --version` reports 0.2.2 or later for native Codex children**
   (0.2.0 suffices for the older Claude/Cursor hooks).
   `--mode`, `--from-hook`, `--callsign` and `subagents` do not exist in 0.1.1, and a
   hook built on them against an older CLI is the exit-2 turn loop described above.
   While you are there, dry-run the exact recipe you are about to install and check it
   exits 0 and reports the intended session ID (omit `--quiet` for this verification):

   ```
   echo '{"session_id":"<a real session id>"}' | xexport current --from-hook --format md --out .chatexports
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
   exports, named with its own full source/session identity — and that the parent transcript did
   **not** get a second copy written under that name.
8. Follow `sync-skills-across-agents` if the project keeps skill mirrors.


## Native Codex inherited history (0.2.2)

Version 0.2.1 supports fresh-context children but rejects valid full-history children
whose second metadata record belongs to the parent. Use 0.2.2 or later for these
exports. The first validated child envelope determines identity, title lookup and
destination. Only the contiguous, explicitly linked ancestor-header chain before
conversation content is accepted as inherited metadata; unrelated, conflicting or
late ancestor headers still fail before writes.

The export preserves copied history and labels it as **Inherited context** with
ancestor IDs. It is a full rollout snapshot, not solely the child's own work. Its
opening prompt/callsign and model can belong to an ancestor, so xexport uses a neutral
`Codex_Sub` label and the child's own indexed/envelope title (or child ID) when no
reliable child-only boundary is available. Do not substitute the parent's callsign,
rewrite rollout metadata, or treat a missing child callsign as an identity failure.
