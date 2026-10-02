---
name: xexport-read
description: Read a chat transcript that xexport exported, without being silently shown only part of it. Use when you are pointed at a .chatexports path, asked to review, resume, summarise, audit or mine a prior chat transcript, told to "read the export" or "read the chat export", or handed an index.html / page-NNN.html / full.html / .md under .chatexports. Explains what each file in an export is for, which one to open first, how to find the page that holds a given prompt, keyword, time or #msg-N anchor, and how to slice a file that is larger than your file-read tool returns in one call. Not for creating an export (xexport-md, xexport-html) or installing autosave (xexport-auto).
---

# xexport-read

`xexport-md`, `xexport-html` and `xexport-auto` write chat exports. This skill is for the
agent that has to **read** one.

The risk it exists for: a file-read tool returns only part of a large file, and the part
it returns looks like a whole document. An agent that reads the opening of a transcript
and reports on "the conversation" has reported on the opening of it.

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
| `index.html` | A page map, then one card per user prompt (the prompt, tool counts, the closing answer). | Take the page map from its top. Do not try to read it whole on a long session. |
| `page-NNN.html` | The complete content of a run of messages: prompts, answers, thinking, tool calls, tool results. | Read whole. Each is sized to fit one read. |
| `full.html` | Every message of every page in one continuous file. | Search it. Do not read it whole. |
| `<name>.md` | The whole conversation as one Markdown file. Long tool output is cut to 2,000 characters unless it was exported with `--full`. | Fine to read whole when small; slice it when large. |

### The sizing contract (xexport 0.3.0 and later)

- A `page-NNN.html` closes before it reaches **20,000 estimated tokens, 1,500 lines or
  200 KB**, whichever comes first, measured on the file as written. In practice the
  token limit decides, and a full page is 25 to 40 KB.
- A page that could not be made to fit is marked. It holds exactly one message that is
  larger than a page, it says "Oversize page" at its top, and its row in the page map
  says `OVERSIZE: read in slices`. Nothing is ever truncated to make a page fit.
- Pages break between prompts when they can. A page that begins in the middle of a
  prompt says "Continues prompt #N" at its top.
- While a chat is still running, every page except the last is final: later turns never
  change which messages it holds. The last page, `full.html` and the index are rewritten
  as the chat grows, and the prompt in progress can move from the last page to a new one.
- Sizes are estimates, calibrated on English, code and tool output. A page of text in
  some other languages, or one dominated by machine-generated lowercase names, can be
  larger than its estimate and come back partial. The check does not change: the last
  line of a complete read is `</html>`.
- `index.html` and `full.html` are **not** sized to a read. On a long session the index
  alone is hundreds of KB. That is why the page map is the first thing in it.
- Message anchors are global: `page-003.html#msg-57` and `full.html#msg-57` are the same
  message. In `full.html` each page start is marked
  `<div class="page-break" id="page-3" data-page="3">`.

**Check that the folder really is in this layout before relying on any of that:** the
top of `index.html` must hold the page map (a `<table class="page-map">` in its first
twenty lines). If it does not, see "Older exports" below, and ignore any `full.html` in
the folder.

## Reading an HTML export

1. **Take the page map.** It is a table near the top of `index.html`, starting around
   line 14, one line per page: the file name, the prompts it covers (`#4–#6`), the
   message anchors it holds (`57–80`), its first timestamp, its lines, KB and estimated
   tokens, and a note. Get it with a **ranged** read (the first 150 lines; continue
   until you pass `</table>`), or by searching `index.html` for `<tr data-page=`.
   A whole-file read of a long session's index is refused or cut short; the cards below
   the map are a summary you can do without.
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

A read with no `offset`/`limit` has three outcomes. Only the first is a complete read.

1. **Whole file.** The last line shown is `</html>`.
2. **Partial view.** A file over 25,000 tokens (by the tool's own count) comes back as
   its first part plus a notice: `[Truncated: PARTIAL view — <path>: showing lines
   1-568 of 693 total (25893 tokens, cap 25000). Call Read with offset=569 limit=568
   for the next page …]`. The notice can arrive as a separate block after the tool
   results rather than inside the result it is about, which is how it gets missed,
   above all when several reads were sent together. The result itself just ends. So
   check the last line you were shown; if it is not `</html>`, continue with the
   `offset` the notice gives, and do not report on the file as a whole.
3. **Refused.** A file over about 256 KB is not read at all:
   `File content (598.5KB) exceeds maximum allowed size (256KB)`. Use a ranged read or a
   search. This is what happens to `full.html`, and to `index.html` on a long session.

A read **with** `offset`/`limit` is not subject to the 256 KB limit, but is refused when
the range is over 25,000 tokens: `File content (N tokens) exceeds maximum allowed
tokens (25000)`. Nothing was read; ask for a smaller range. `offset` is the first line
(1-based), `limit` the number of lines.

Other things that were measured (2026-10-02):

- The tool's token count on export HTML ran at 2.0 to 2.6 bytes per token, so 25,000
  tokens is roughly 50 to 65 KB. That is why pages are sized by estimated tokens.
- The documented default of 2,000 lines per read was not what bound: a 3,000-line file
  under the token limit came back whole. Do not rely on either behaviour; check for
  `</html>`.
- Very long single lines (a tool call or result written on one line) were returned in
  full, up to the token limit. The page map's note column says `longest line N chars`
  for any page with a line over 2,000 characters, because other readers cut long lines.

**When one line is itself too large.** An oversize page can hold a single line of tens
of thousands of characters, and then no line range is small enough: even `limit=1` is
refused. Take that line in character slices through a shell instead (25,000 characters
at a time keeps under what a shell tool returns):

```bash
awk 'length > 2000 {print FNR, length}' page-020.html    # which lines are long, and how long
sed -n '18p' page-020.html | cut -c1-25000
sed -n '18p' page-020.html | cut -c25001-50000
```

```powershell
$line = (Get-Content -LiteralPath page-020.html)[17]      # line 18 is index 17
$line.Length
$line.Substring(0, [Math]::Min(25000, $line.Length))
$line.Substring(25000, [Math]::Min(25000, $line.Length - 25000))   # and so on, while the start is below the length
```

`cut -c` counts bytes in some builds, so a slice boundary can fall inside a non-ASCII
character; the character is damaged, nothing else is.

### Cursor (agent read tool)

Reported to default to 2,000 lines per call, with offset and limit parameters to
continue, and no clear error when it stops short. Check for `</html>`. A file the user
attached with `@File` is reported to be given to you in full.

### Codex, Grok and anything reading through a shell

Shell output is cut much earlier than a read tool's (reported for Codex at about 10 KB
or 256 lines; unverified). So never print a whole export with `cat`, `type` or
`Get-Content`. Take the page map first, then slices.

**Change into the export folder first**, with the path quoted: folder names contain
spaces and ` -- `, and can contain `[` and `]`, which break unquoted paths and
PowerShell's `-Path`.

```bash
cd "<project root>/.chatexports/html/<export folder>"
grep -o '<tr data-page=.*</tr>' index.html               # the page map, one row per line
grep -l 'the text you want' page-*.html                  # which pages mention it
wc -l page-007.html                                      # how many lines it has
sed -n '1,150p' page-007.html                            # lines 1-150
sed -n '151,300p' page-007.html                          # the next slice
```

```powershell
Set-Location -LiteralPath "<project root>\.chatexports\html\<export folder>"
Select-String -LiteralPath index.html -Pattern '<tr data-page=' | ForEach-Object Line
Get-ChildItem page-*.html | Select-String -Pattern 'the text you want' -CaseSensitive -List |
    Select-Object -ExpandProperty Filename               # which pages mention it
(Get-Content -LiteralPath page-007.html | Measure-Object -Line).Lines
Get-Content -LiteralPath page-007.html -TotalCount 150   # lines 1-150
Get-Content -LiteralPath page-007.html | Select-Object -Skip 150 -First 150   # the next slice
```

Keep going until the slice that contains `</html>`. A page-map row is about 230
characters, so on a session with hundreds of pages take the map in slices too
(`grep -o … | sed -n '1,40p'`). Line numbers in the page map and in `sed` count
newlines; `Get-Content` also breaks a line at a bare carriage return inside tool
output, so on a page that has one its line numbers run ahead of the map's.

## Reading a Markdown export

One file, no page map. Check its size before reading it: a long session's `.md` is
hundreds of KB and several thousand lines, well past one read. Each message starts at a
heading such as `## 👤 User` or `## 🤖 Claude`, so search for `^## (👤|🤖)` to get an
outline with line numbers (a bare `^## ` also matches headings inside answers), then
read the ranges you need. The last line is an HTML comment beginning
`<!-- xexport-cursor`; that is xexport's record, not conversation. Tool output over
2,000 characters is marked as truncated unless the export was made with `--full`. If you
need the full output and an `html\` export of the same session exists, the pages have
it.

## Older exports (xexport 0.2.3 and earlier)

**The sign is an `index.html` with no page map at its top.** Every version writes the
index last, so it tells you which version last wrote the folder. In such a folder:

- pages hold five prompts each whatever their size, so a page can be several times what
  one read returns, and a large one is refused outright. Check every page for `</html>`
  and slice it when it is absent;
- `index.html` lists every page number in its navigation and has no size information;
- anchors (`#msg-N`) work the same way;
- **a `full.html`, `xexport.css` or `xexport.js` in the folder is a leftover and must
  not be used.** It happens when a newer xexport wrote the folder and an older one
  refreshed it afterwards: the older one rewrites the index and pages and leaves
  `full.html` as it was, still calling itself the complete transcript and missing
  everything since.

If you are allowed to run commands and the session's source transcript still exists,
refreshing the export with xexport 0.3.0 or later upgrades it in place:

```
xexport <session id> --format html --mode append --callsign "<agent>" --out "<the .chatexports folder>"
```

where `<agent>` is the part of the export's folder name before the first ` -- `. Passing
it keeps the export under the name of the agent whose chat it is; without it the folder
can be renamed after you. Do this only when the user wants the export changed.

## When you report

Say which files you read and whether you read them whole. If you worked from a partial
view, a search, or a summary card in the index, say so: the reader of your report
cannot tell otherwise.
