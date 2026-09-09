# LESSONS.md

Defects that were expensive to find, and what each one changed. A lesson earns a place
here when it would not be obvious from reading the code afterwards — usually because the
bug was **silent**: right exit code, plausible output, wrong result.

Every entry names the failure, not just the fix.

---

## 1. A filter that keeps one file clean can empty another

`parse_file` dropped every line carrying `isSidechain: true`, which is correct for a main
transcript: sidechain lines are a duplicate copy of what a subagent did. But **every line
of a subagent's own transcript carries that flag**, so parsing one produced a session with
zero messages — and an export of zero messages is a valid, well-formed, empty document.
Exit 0. No warning. It went unnoticed until 0.2.0.

**Changed:** `parse_file(path, *, include_sidechain=None)` defaults to "yes if this file
*is* a subagent transcript". The predicate is the path, not the content.

**The general lesson:** a filter defined by what it removes is only correct relative to
what it is reading. When the same parser serves two roles, the filter has to know which
role it is in.

---

## 2. Discovery and parsing must be fixed together

The sidechain fix made subagent transcripts parse. They were still unreachable:
`list_sessions` globbed one level deep and filtered on a UUID-shaped stem, while
`find_session` globbed `*/<id>.jsonl`. Subagent files live two levels down and are named
`agent-<hex>`. Two independent reasons the feature could not work, and fixing either alone
looks like it did nothing.

**Changed:** `list_subagents(parent)`, and both lookups reach the nested path.

**The general lesson:** when a feature is "can't see it" and "can't read it" at once, a fix
that only addresses one produces no observable change — which is easily mistaken for the
fix not working, or for the bug being elsewhere.

---

## 3. Two formats, one guard — the asymmetry is the bug

The Markdown path refused to extend an export whose transcript no longer matched. The HTML
path, in the identical situation, fell through to re-rendering the folder **in place**,
overwriting `index.html` and unlinking the surplus `page-NNN.html` files. An export of a
longer transcript was destroyed, with exit 0 and no warning, by a code path whose sibling
refused the same operation.

**Changed:** both formats take the same verdict from the same function, and every guard
that stops one stops the other.

**The general lesson:** when the same decision is implemented twice, the two copies drift,
and the drift shows up as one of them being dangerous. Decide once, in one function, and
have both callers ask it.

---

## 4. The privacy flags were format-specific, so they were a leak

`--brief`, `--no-tools` and `--no-thinking` were collected into a property named
`md_kwargs` and consumed only by the Markdown renderer. `render_html` took no filter
arguments at all. `--format both --brief` therefore wrote a redacted `.md` beside an HTML
export containing every thinking block, tool argument, tool result and raw entry — and,
because the HTML renderer does no truncation, containing them in full where the Markdown
would have capped them.

Proven, not theorised: credentials seeded into a fixture were absent from the `.md` and
present verbatim in `page-001.html`.

**Changed:** `render/__init__.filtered` is the single content predicate, applied inside
both renderers.

**The general lesson:** a flag whose name describes *what to exclude* is a privacy control,
and a privacy control that applies to one of two requested outputs is worse than none — the
user believes the content is gone. The variable name `md_kwargs` was the whole bug, visible
in plain sight, for as long as it took someone to ask what HTML did with it.

---

## 5. A short-circuit that asks the wrong question first

`--callsign auto` asked AgentNamer "who is this session?" and returned on the first answer.
A Claude subagent **shares its parent's session id**, so that call answered with the
parent's callsign, and the transcript fallback beneath it was unreachable code. Every
subagent export was stamped with its parent's name.

**And the test passed.** It pointed `XEXPORT_AGENTNAMER` at a file that did not exist, so
the lookup never ran and the fallback was reached for the wrong reason.

**Changed:** a subagent reads the callsign its parent wrote into its opening task record
and never consults the registry. The test installs a stub that *succeeds* with a parent
callsign and asserts that name does not appear.

**The general lesson:** a test that passes because a dependency was unavailable is not a
test of the behaviour; it is a test of the dependency being unavailable. When mocking a
lookup, make it **succeed with the wrong answer** — that is the only version that can fail
when the ordering is wrong.

---

## 6. A guard can swallow the evidence of an off-by-one

Cursor's cwd was inferred with `path.parents[N]`, and a draft used `parents[3]` where
`parents[2]` was meant. The next line was `if encoded and encoded != "projects"`. Since the
wrong index landed exactly on `projects`, the guard rejected it — so instead of a wrong
cwd, `session.cwd` was silently never set, **for every Cursor export**. No error, no
failing test, and no symptom anyone would look at.

**Changed:** the index is derived from whether the path is a subagent transcript, and the
two shapes are asserted directly.

**The general lesson:** a validity guard downstream of an index error converts a loud bug
into a silent one. When a guard rejects a value, it is worth knowing whether it rejected a
*hostile* value or a *computed-wrong* one.

---

## 7. Counting messages is not detecting change

The first 0.2.0 decided "nothing to do" by comparing a stored message **count** against the
transcript, and verified the boundary with a fingerprint of one message. An edited, retried
or reordered turn leaves the count alone, so the export silently kept the old text and
reported "Up to date". The limitation was known and was documented in a test rather than
fixed.

**Changed:** a digest over every message and block. These stores are small and local;
hashing the whole conversation costs microseconds, and it removed the blind spot instead of
describing it.

**The general lesson:** "we documented the limitation" is only a good answer when closing it
is expensive. Measure before assuming it is.

---

## 8. Re-render beats append — but it will happily write a worse export

Replacing the append machinery with "re-parse, re-render, atomically replace" deleted an
entire bug class: boundary anchors, trailing-byte detection, interrupted-write recovery,
deltas that render to nothing. All of it existed only because bytes were being appended in
place.

What re-render does **not** give you is any reason to refuse. It will cheerfully overwrite
a complete export with a shorter one (a compacted transcript) or rewrite a full-fidelity
transcript as `--brief` (a hook running one way, a hand-run export the other). Both are
reachable in ordinary use, and both destroy something the user wanted.

**Changed:** the record each export keeps of what it was made from is checked before any
overwrite — refuse a shorter transcript, refuse different filters, and let `--mode replace`
mean "yes, I know".

**The general lesson:** when a simpler mechanism removes a class of bugs, check what the old
mechanism's *guards* were protecting before deleting those too. The complexity was wrong;
the caution usually was not.

---

## 9. A skill that emits a flag the CLI removed breaks the turn, not just the export

The `xexport-auto` hook recipes passed `--seamless` after that flag was removed. An unknown
flag is a click `UsageError`, which exits **2** — and exit 2 from a `Stop` hook does not
merely fail the export: it blocks the stop and feeds stderr back to the model, producing
another turn, another hook, and a loop.

**Changed:** the recipes were dry-run against the installed CLI before shipping, and every
flag the three skills emit is checked against `--help`.

**The general lesson:** the skills and the CLI are one product with a version skew problem.
Install the CLI before the skills, never the reverse, and treat "does this command still
parse?" as part of shipping a skill — the blast radius of a usage error inside a hook is
much larger than the command that produced it.

---

## 10. A guard you cannot reach is not a guard

The candidate ranking in `cursors._best` exists for one reason, written in its own
docstring: so that a break costs *exactly one* extra file instead of one per turn. The
re-render merge added a faster lookup — match the export by its identity suffix — and
returned its hit directly. That lookup, by construction, never matches a numbered
companion. So the refused original was re-selected every run, refused again, and forked
again: one file, or one whole paginated HTML folder, per assistant turn, forever.

The ranking was still there. Still tested. Still passing. Just unreachable.

Two reviewers found it independently, both by *running* it rather than reading it. The
test that should have caught it passed because its fixture grew the transcript back past
the stale count within four iterations — it measured recovery, not the loop.

**And the second half of the same bug:** once the companion existed, refreshing it
rewrote its marker *without* the flag that marked it as a companion. It stopped being
adoptable the moment it was used, and the next refusal forked again.

**The general lesson:** when you add a faster path in front of an existing decision, the
question is not "is the fast path correct?" but "what did the slow path decide that this
one no longer gets to?" A short-circuit is a silent deletion of everything downstream of
it. And a flag that survives being written but not being *re-written* is a flag that only
works once.

---

## 11. Fail-closed has to survive the rewrite that removes the thing it checked

The append design failed closed on an unverifiable cursor: no marker meant refuse, on the
grounds that appending blind is exactly the write that loses turns. Re-render made the
cursor much less load-bearing, so the missing-marker branch was relaxed to "nothing to
compare against, so a refresh is safe".

It is not safe. Re-render cannot lose turns *relative to the transcript*, but it will
happily overwrite a long export with a compacted one — which is precisely what the
shrink guard was added to prevent, and the shrink guard needs the marker. So the one
path that had no marker was the one path with no guards at all, and its own docstring
advertised that path as supported ("works on a file whose marker was lost").

**The general lesson:** when a redesign makes a safety check look redundant, re-derive
what the check was actually protecting against before relaxing it. "This mechanism can't
have that failure any more" is a claim about the mechanism; the guard was usually about
the *data*.
