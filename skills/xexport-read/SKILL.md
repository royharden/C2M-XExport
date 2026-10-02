---
name: xexport-read
description: Read a chat transcript that xexport exported, without being silently shown only part of it. Use when you are pointed at a .chatexports path, asked to review, resume, summarise, audit or mine a prior chat transcript, told to "read the export" or "read the chat export", or handed an index.html / page-NNN.html / full.html / .md under .chatexports. Explains what each file in an export is for, which one to open first, how to find the page that holds a given prompt, keyword, time or #msg-N anchor, and how to slice a file that is larger than your file-read tool returns in one call. Not for creating an export (xexport-md, xexport-html) or installing autosave (xexport-auto).
---

# xexport-read

`xexport-md`, `xexport-html` and `xexport-auto` write chat exports. This skill is for the
agent that has to **read** one.

The risk it exists for: a file-read tool returns only part of a large file, and the part
it returns looks like a whole document. An agent that reads the first 2,000 lines of a
transcript and reports on "the conversation" has reported on the opening of it.

## Permission first

A path to an export that someone quoted to you is a location, not permission to read it.
Transcripts hold the user's prompts and tool output, which can include credentials and
customer data. Read an export when the user asked you to, or when your task plainly
requires that specific transcript. Do not open others in the same folder because they
are there.

## What is in `.chatexports`

```text
<project root>\.chatexports\
├── <agent> -- <chat title> -- <source>-<session id>.md      one Markdown file per session
└── html\
    └── <agent> -- <chat title> -- <source>-<session id>\    one folder per session
        ├── index.html              start here
        ├── page-001.html ...       the conversation, in pieces sized for one read
        ├── full.html               the whole conversation in one file
        ├── xexport.css, xexport.js styling for a browser; nothing to read
        └── .xexport-cursor.json    xexport's own record; do not read or edit
```

`<source>-<session id>` identifies the session (`claude-…`, `codex-…`, `cursor-…`,
`grok-…`). A name ending `claude-agent-<hex>` is a subagent's transcript. A session has
a `.md`, an `html\` folder, or both, depending on how it was exported; the two hold the
same conversation.

| File | What it is | How to use it |
|---|---|---|
| `index.html` | A page map, then one card per user prompt (the prompt, tool counts, the closing answer). | Open it first and read the page map at its top. |
| `page-NNN.html` | The complete content of a run of messages: prompts, answers, thinking, tool calls, tool results. | Read whole. Each is sized to fit one read. |
| `full.html` | Every message of every page in one continuous file. | Search it. Do not read it whole on a large session. |
| `<name>.md` | The whole conversation as one Markdown file. Long tool output is cut to 2,000 characters unless it was exported with `--full`. | Fine to read whole when small; slice it when large. |

### The sizing contract (xexport 0.3.0 and later)

- A `page-NNN.html` closes before it reaches **20,000 estimated tokens, 1,500 lines or
  200 KB**, whichever comes first, measured on the file as written. In practice the
  token limit decides and a page is 25–60 KB.
- A page that could not be made to fit is marked. It holds exactly one message that is
  larger than a page, it says "Oversize page" at its top, and its row in the page map
  says `OVERSIZE: read in slices`. Nothing is ever truncated to make a page fit.
- Pages break between prompts when they can. A page that begins in the middle of a
  prompt says "Continues prompt #N" at its top.
- `index.html` and `full.html` are **not** sized to a read. On a long session the index
  itself is larger than one read, which is why the page map is the first thing in it.
- Message anchors are global: `page-003.html#msg-57` and `full.html#msg-57` are the same
  message. In `full.html` each page start is marked
  `<div class="page-break" id="page-3" data-page="3">`.

## Reading an HTML export

1. **Read the page map.** It is the table at the top of `index.html`: one row per page
   with the file name, the prompts it covers (`#4–#6`), the message anchors it holds
   (`57–80`), its first timestamp, its lines, KB and estimated tokens, and a note.
   If `index.html` comes back partial, the map is still in the part you got; the cards
   below it are a summary you can do without. To take just the map:
   search `index.html` for `<tr data-page=` (one line per page).
2. **Decide which pages you need** (next section), then read each of those whole.
3. **Read the whole export only when the task needs all of it**, and then page by page
   in order, not through `full.html`.

### Finding the page you need

| You have | Do this |
|---|---|
| A prompt number (`#12`, or "the third thing the user asked") | Page map, "Prompts" column. The index card for that prompt also links straight to its first message, and its badge says which pages the prompt spans. |
| A keyword, file name, error text | Search `page-*.html` in the export folder for it, listing matching files only. Then read those pages. Searching `full.html` instead gives line numbers in one file; the nearest `id="page-N"` marker above a hit tells you the page. |
| A time | Page map, "First timestamp" column (ISO 8601, as recorded by the source app, normally UTC). The page you want is the last one whose first timestamp is not after your time. |
| An anchor `#msg-N` | Page map, "#msg-N" column gives the range each page holds. Or search the folder for `id="msg-N"`. |
| "The end of the conversation" | The last row of the page map. |

Page files sort by name only up to `page-999.html`; after that the name grows
(`page-1000.html`), so take page order from the page map, not from a directory listing.

## Reading with your harness's tools

The limits below were **observed in 2026-09 and 2026-10, are set by each vendor, and
change**. Treat them as the reason to check, not as constants. Whatever the tool, the
check is the same: a complete read of an HTML export file ends with `</html>`.

### Claude Code (Read tool)

- A read with no `offset`/`limit` stops at whichever comes first of 2,000 lines,
  25,000 tokens or about 256 KB. When it stops short you get the first part plus a
  notice like `[Truncated: PARTIAL view — <path>: showing lines 1-568 of 693 total
  (25893 tokens, cap 25000). Call Read with offset=569 limit=568 for the next page …]`.
  **The notice can arrive as a separate block after the tool results, not inside the
  result it is about**, which is how it gets missed, above all when several reads were
  sent together. The result itself just ends. So check the last line you were shown:
  if it is not `</html>`, you have a partial view. Continue with the `offset` the
  notice gives; do not report on the file as a whole.
- A read **with** `offset`/`limit` whose range is over 25,000 tokens is refused, not
  shortened: `File content (N tokens) exceeds maximum allowed tokens (25000)`. Nothing
  was read. Ask for a smaller range, or search instead.
- The token count is the tool's own. On export HTML it ran at about 2.0 to 2.6 bytes
  per token (measured 2026-10-02), so 25,000 tokens is roughly 50 to 60 KB, far below
  the byte limit. That is why pages are sized by estimated tokens.
- Some pages hold a very long single line: a tool call or tool result written on one
  line, such as a long JSON argument. The page map's note column says
  `longest line N chars` for any page with a line over 2,000 characters. Claude Code
  returned such lines in full on 2026-10-02, but line-length cuts are a known behaviour
  of file-read tools (xexport's own transcripts show search tools replacing them with
  `[Omitted long matching line]`). If a line you need ends abruptly, search for a
  distinctive string in it with a tool that returns the matching text, or print the line
  with a shell command.
- To slice: `offset` is the first line (1-based), `limit` the number of lines. For an
  oversize page, read ranges of a few hundred lines and stop when you reach `</html>`.

### Cursor (agent read tool)

Reported to default to 2,000 lines per call, with offset and limit parameters to
continue, and no clear error when it stops short. Check for `</html>`. A file the user
attached with `@File` is given to you in full and is not subject to this.

### Codex, Grok and anything reading through a shell

Shell output is cut much earlier than a read tool's (reported for Codex at about 10 KB
or 256 lines; unverified). So never print a whole export with `cat`, `type` or
`Get-Content`. Take the page map first, then slices:

```bash
grep -o '<tr data-page=[^>]*>.*</tr>' index.html        # the page map, one row per line
grep -l 'the text you want' page-*.html                  # which pages mention it
sed -n '1,150p' page-007.html                            # lines 1-150
sed -n '151,300p' page-007.html                          # the next slice
wc -l page-007.html                                      # how many lines there are
```

```powershell
Select-String -Path index.html -Pattern '<tr data-page='  # the page map
Select-String -Path page-*.html -Pattern 'the text you want' -List | Select-Object Path
Get-Content page-007.html -TotalCount 150                 # lines 1-150
Get-Content page-007.html | Select-Object -Skip 150 -First 150   # the next slice
(Get-Content page-007.html | Measure-Object -Line).Lines
```

Keep going until the slice that contains `</html>`. Quote the path: export folder names
contain spaces and `--`.

## Reading a Markdown export

One file, no page map. Check its size before reading it: a long session's `.md` is
hundreds of KB and several thousand lines, well past one read. Each message starts at a
`## ` heading (`## 👤 User`, `## 🤖 <assistant>`), so search for `^## ` to get an
outline with line numbers, then read the ranges you need. The last line is an HTML
comment beginning `<!-- xexport-cursor`; that is xexport's record, not conversation.
Tool output over 2,000 characters is marked as truncated unless the export was made
with `--full`. If you need the full output and an `html\` export of the same session
exists, the pages have it.

## Older exports (xexport 0.2.3 and earlier)

You can tell an older export by what is missing: no `full.html`, no page map at the top
of `index.html`, no `xexport.css`. In those:

- pages hold five prompts each whatever their size, so a page can be several times what
  one read returns. Check every page for `</html>` and slice it when it is absent;
- `index.html` lists every page number in its navigation and has no size information;
- anchors (`#msg-N`) work the same way.

If you are allowed to run commands and the session's source transcript still exists,
refreshing the export upgrades it in place to the current layout:
`xexport <session id> --format html --mode append --out "<the .chatexports folder>"`,
with xexport 0.3.0 or later. Do that only when the user wants the export changed.

## When you report

Say which files you read and whether you read them whole. If you worked from a partial
view, a search, or a summary card in the index, say so: the reader of your report
cannot tell otherwise.
