"""Render a Session to HTML: index.html + page-NNN.html + full.html.

Layout and styling adapted from claude-code-transcripts by Simon Willison
(Apache-2.0) — see NOTICE. Index page: a page map, then one card per user prompt
with tool-use stats and the closing long answer. Detail pages: full content with
client-side expand/collapse, packed by *size* so that one page is one whole read
for an agent's file-read tool. full.html: the same messages in one continuous file.

Why size and not a prompt count: an agent that opens a page with a file-read tool
is shown only part of it, or none of it, once the file passes that tool's caps
(observed 2026-10 for Claude Code: a partial view past 25,000 tokens by its own
count, a refusal past about 256 KB, and a documented default of 2,000 lines).
Five prompts can be 400 KB. See LESSONS.md.

The index and the pages link one shared xexport.css and xexport.js rather than
carrying them inline: inline, they were about a fifth of every page's budget, read
again by every agent that opened a page. full.html keeps them inline, so the one
file meant to be passed around on its own still stands on its own.
"""

from __future__ import annotations

import copy
import math
import os
import re
import string
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import mistune
from jinja2 import Environment, PackageLoader, select_autoescape
from markupsafe import Markup

from .. import __version__
from ..publish import atomic_write_text
from ..model import ASSISTANT_TEXT, TOOL_CALL, USER_TEXT, Message, Session
from . import filtered

LONG_TEXT_MIN_CHARS = 400  # closing answers longer than this get an index preview

# Some readers cut each line at a fixed width whatever the size of the file.
# Lines this long are inside <pre> blocks -- a tool argument or output on one
# line -- where wrapping would change the content, so instead the page map says
# which pages have one.
LONG_LINE_CHARS = 2_000

# ------------------------------------------------------------------ page budget
# A page closes at whichever of these is reached first, measured on the file as it
# is written (boilerplate and navigation included). Each sits about 20% under the
# reader cap it protects: 25,000 tokens, 2,000 lines, 256 KB.
PAGE_MAX_TOKENS = 20_000
PAGE_MAX_LINES = 1_500
PAGE_MAX_BYTES = 200_000

# Tokens are estimated, because a real tokenizer is a heavy dependency that
# fetches vocabulary over the network, and the tokenizer that matters (the one
# behind Claude Code's Read tool) cannot be run locally at all. The estimate is a
# weight per character class and per run, calibrated on 2026-10-02 against the
# token counts that Read tool itself reports -- see
# scripts/validate_token_estimate.py, which holds the calibration data, and
# LESSONS.md. A flat bytes-per-token ratio cannot do this job: the same reader
# counted 3.9 bytes per token on Russian prose and 1.05 on base64, and a page of
# tool output is much closer to the second. Nor can a weight per character alone:
# what makes hexdumps, ids and random-case strings expensive is that they are
# many short runs, so the runs are counted too.
# The weights are the solution of a linear program: the smallest over-count of 34
# real pages such that no real page is under-counted, no measured class is more
# than 8% under, and three harder ones (Indonesian and Swahili prose, lowercase
# ids) no more than 15% under, which the 20% between the budget and the reader's
# cap still covers. What it cannot do is tell words from random lowercase
# letters: those cost the reader about three times as much as prose and are
# estimated at around 60% of their real count. The classes, the counts and the
# exceptions are in scripts/validate_token_estimate.py. Changing a weight changes
# where pages are cut: bump HTML_LAYOUT with it, or existing exports keep their
# old cuts until they next grow.
TOKENS_PER_LETTER = 0.26        # A-Z a-z
TOKENS_PER_LETTER_RUN = 0.35    # a word, or one hump of a camelCase name
TOKENS_PER_SHORT_RUN = 1.28     # added per letter run of 1 or 2: "3f a2", "xQ"
TOKENS_PER_LONG_LETTER = 2.44   # added per letter past the 8th of one run
TOKENS_PER_DIGIT = 0.59
TOKENS_PER_DIGIT_RUN = 0.33
TOKENS_PER_SPACE = 0.40
TOKENS_PER_NEWLINE = 1.0
TOKENS_PER_OTHER_ASCII = 0.60   # punctuation, and the entities autoescape emits
TOKENS_PER_NON_ASCII_BYTE = 0.375
TOKENS_PER_ASTRAL_CHAR = 1.10   # added per 4-byte character: emoji
TOKENS_PER_OPAQUE_CHAR = 0.20   # added per character of a long unbroken run
_OPAQUE_RUN = re.compile(rb"[A-Za-z0-9]{20,}")   # hashes, base64, minified blobs
_LETTER_RUN = re.compile(rb"[A-Z]?[a-z]+|[A-Z]+")
_DIGIT_RUN = re.compile(rb"[0-9]+")
_LETTERS = string.ascii_letters.encode("ascii")
_DIGITS = string.digits.encode("ascii")
_ASCII = bytes(range(0x80))
_BELOW_ASTRAL_LEAD = bytes(range(0xF0))

# Bumped when the files an export folder holds, or how they are cut, change in a
# way an existing export should be re-rendered to pick up. 1 = five prompts per
# page (0.2.3 and earlier, recorded as no value at all). 2 = size-packed pages,
# full.html, the page map and the shared stylesheet and script.
HTML_LAYOUT = 2

# A chat is retitled while it runs, and the title is on every page. So that a new
# title can never move a page cut (and with it every link into the pages after
# it), a page is budgeted as if its title cost this much, whatever it really is,
# and the title shown on a page is capped to what that covers: one line of at
# most 80 characters, shown twice. The worst such title under the weights above
# is 80 characters that each escape to an entity, about 540 tokens and 800 bytes.
PAGE_TITLE_CHARS = 80
PAGE_TITLE_BYTES = 1_000
PAGE_TITLE_TOKENS = 650

_markdown = mistune.create_markdown(
    escape=True, plugins=["table", "strikethrough", "url"]
)


@dataclass(frozen=True)
class Limits:
    tokens: int
    lines: int
    bytes: int


def _env_limit(name: str, default: int) -> int:
    """A positive integer from the environment, or the default.

    Never raises: this runs inside a Stop hook, where an exception is a blocked
    turn, so a mistyped override falls back rather than failing the export.
    """
    try:
        value = int(os.environ.get(name, "").strip())
    except ValueError:
        return default
    return value if value > 0 else default


def page_limits() -> Limits:
    return Limits(
        tokens=_env_limit("XEXPORT_PAGE_MAX_TOKENS", PAGE_MAX_TOKENS),
        lines=_env_limit("XEXPORT_PAGE_MAX_LINES", PAGE_MAX_LINES),
        bytes=_env_limit("XEXPORT_PAGE_MAX_BYTES", PAGE_MAX_BYTES),
    )


def layout_key() -> str:
    """What an export folder was laid out as: the layout version and the budget.

    Stored in the folder's cursor record. A folder whose key differs is re-rendered
    even when the transcript has not changed, which is how an export made by an
    older version picks up full.html and size-packed pages.
    """
    limits = page_limits()
    return f"{HTML_LAYOUT}:{limits.tokens}/{limits.lines}/{limits.bytes}"


def token_weight(data: bytes) -> float:
    """Estimated tokens in UTF-8 `data`, as a float so that pieces add up.

    A sum over characters and runs, so the weight of a page is the weight of its
    shell plus the weights of its messages, and packing never has to re-measure a
    page. That holds as long as no run of letters or digits spans a join, which
    is true of what is joined here: every piece starts and ends with markup.
    """
    size = len(data)
    letters = size - len(data.translate(None, _LETTERS))
    digits = size - len(data.translate(None, _DIGITS))
    spaces = data.count(b" ")
    newlines = data.count(b"\n")
    non_ascii = len(data.translate(None, _ASCII))
    astral = len(data.translate(None, _BELOW_ASTRAL_LEAD))
    other = size - letters - digits - spaces - newlines - non_ascii
    opaque = sum(len(run) for run in _OPAQUE_RUN.findall(data))
    run_lengths = [len(run) for run in _LETTER_RUN.findall(data)]
    short_runs = sum(1 for n in run_lengths if n <= 2)
    long_letters = sum(n - 8 for n in run_lengths if n > 8)
    digit_runs = len(_DIGIT_RUN.findall(data))
    return (letters * TOKENS_PER_LETTER + len(run_lengths) * TOKENS_PER_LETTER_RUN
            + short_runs * TOKENS_PER_SHORT_RUN
            + long_letters * TOKENS_PER_LONG_LETTER
            + digits * TOKENS_PER_DIGIT + digit_runs * TOKENS_PER_DIGIT_RUN
            + spaces * TOKENS_PER_SPACE + newlines * TOKENS_PER_NEWLINE
            + other * TOKENS_PER_OTHER_ASCII
            + non_ascii * TOKENS_PER_NON_ASCII_BYTE
            + astral * TOKENS_PER_ASTRAL_CHAR + opaque * TOKENS_PER_OPAQUE_CHAR)


def estimate_tokens(text: str) -> int:
    return math.ceil(token_weight(text.encode("utf-8")))


def page_name(number: int) -> str:
    """page-001.html ... page-999.html, then page-1000.html: the width grows.

    Padding to the final page count instead would rename every page the moment a
    session crossed 999, breaking every link into it. Past 999 a plain name sort
    is no longer page order; the page map in index.html is.
    """
    return f"page-{number:03d}.html"


def _md(text: str) -> Markup:
    return Markup(_markdown(text or ""))


def _tool_icon(name: str) -> str:
    n = (name or "").lower()
    if "bash" in n or "shell" in n or "powershell" in n:
        return "$"
    if "read" in n:
        return "📖"
    if "write" in n or "edit" in n:
        return "✏️"
    if "grep" in n or "glob" in n or "search" in n or "find" in n:
        return "🔍"
    if "web" in n or "fetch" in n or "browser" in n or "navigate" in n:
        return "🌐"
    if "task" in n or "agent" in n:
        return "🤖"
    if "todo" in n:
        return "☑️"
    return "🔧"


def _env() -> Environment:
    env = Environment(
        loader=PackageLoader("xexport.render", "templates"),
        autoescape=select_autoescape(("html",)),
        # Without these every template tag leaves a blank line behind, and lines
        # are one of the three things a page is budgeted on.
        trim_blocks=True,
        lstrip_blocks=True,
        # Files end with a newline, so `wc -l`, a read tool's line numbers and the
        # page map all give the same count.
        keep_trailing_newline=True,
    )
    env.filters["md"] = _md
    env.globals["tool_icon"] = _tool_icon
    env.globals["page_name"] = page_name
    return env


def _role_view(m: Message, session: Session, anchor: int) -> dict:
    css = {"user": "user", "assistant": "assistant", "tool": "tool-reply"}[m.role]
    label = {"user": "User", "assistant": session.assistant_label,
             "tool": "Tool reply"}[m.role]
    return {"css": css, "label": label, "anchor": anchor,
            "ts": m.timestamp, "blocks": m.blocks}


def _group_stats_line(group: list[Message]) -> str:
    counts = Counter(
        (b.name or "tool").lower()
        for m in group for b in m.blocks if b.kind == TOOL_CALL
    )
    return " · ".join(f"{n} {name}" for name, n in counts.most_common())


def _group_prompt(group: list[Message]) -> str:
    for m in group:
        if m.role == "user":
            for b in m.blocks:
                if b.kind == USER_TEXT and b.text.strip():
                    return b.text
    return "(no prompt)"


def _group_long_text(group: list[Message]) -> str:
    for m in reversed(group):
        if m.role == "assistant":
            for b in reversed(m.blocks):
                if b.kind == ASSISTANT_TEXT and len(b.text) >= LONG_TEXT_MIN_CHARS:
                    return b.text
    return ""


# ------------------------------------------------------------------------ packing
def _size(text: str) -> tuple[int, int, float]:
    """(UTF-8 bytes, newlines, estimated tokens) of a piece of rendered HTML."""
    data = text.encode("utf-8")
    return len(data), data.count(b"\n"), token_weight(data)


def _publish(path: Path, text: str) -> None:
    """Write `text` unless the file already holds exactly that.

    A page that already has a successor renders byte-identically on every later
    refresh, unless the chat was retitled. Rewriting it anyway would move its
    mtime each turn, and under OneDrive that is a re-upload of the whole export
    after every answer.
    """
    data = text.encode("utf-8")
    try:
        # Size first: it settles most changed files without opening them, and
        # opening a cloud-only file under OneDrive downloads it.
        if path.stat().st_size == len(data):
            with open(path, "rb") as f:
                if f.read() == data:
                    return
    except OSError:
        pass
    atomic_write_text(path, text)


@dataclass
class _Fragment:
    """One message as it will appear on a page, rendered once and measured once."""

    anchor: int
    prompt: int             # the prompt group it belongs to, 1-based
    timestamp: str
    html: str = ""          # "" when a content filter left the message bare
    nbytes: int = 0
    newlines: int = 0
    tokens: float = 0.0

    @property
    def drawn(self) -> bool:
        return bool(self.html)


@dataclass
class _Page:
    number: int
    shell_bytes: int = 0
    shell_newlines: int = 0
    shell_tokens: float = 0.0
    continues: int = 0      # the prompt number this page continues, 0 if none
    oversize: bool = False
    fragments: list[_Fragment] = field(default_factory=list)
    anchors: list[int] = field(default_factory=list)   # drawn or not
    prompts: list[int] = field(default_factory=list)
    body_bytes: int = 0
    body_newlines: int = 0
    body_tokens: float = 0.0

    @property
    def empty(self) -> bool:
        return not self.fragments

    def fits(self, fragments: list[_Fragment], limits: Limits) -> bool:
        # Each fragment is joined to the page by one newline. Charging one per
        # fragment over-counts the first by a byte and keeps the sums additive.
        count = len(fragments)
        total_bytes = (self.shell_bytes + self.body_bytes + count
                       + sum(f.nbytes for f in fragments))
        total_lines = (self.shell_newlines + self.body_newlines + count
                       + sum(f.newlines for f in fragments))
        total_tokens = (self.shell_tokens + self.body_tokens
                        + count * TOKENS_PER_NEWLINE
                        + sum(f.tokens for f in fragments))
        return (total_bytes <= limits.bytes
                and total_lines <= limits.lines
                and total_tokens <= limits.tokens)

    def add(self, fragment: _Fragment) -> None:
        self.anchors.append(fragment.anchor)
        if not self.prompts or self.prompts[-1] != fragment.prompt:
            self.prompts.append(fragment.prompt)
        if fragment.drawn:
            self.fragments.append(fragment)
            self.body_bytes += fragment.nbytes + 1
            self.body_newlines += fragment.newlines + 1
            self.body_tokens += fragment.tokens + TOKENS_PER_NEWLINE


def _paginate(groups: list[list[_Fragment]], limits: Limits, shell_size) -> list[_Page]:
    """Pack prompt groups into pages, greedily and in order.

    Cut rules, in order of preference:

    1. between prompt groups, when the whole next group fits on the current page;
    2. a group that does not fit starts a fresh page; if it does not fit there
       either it is split between its messages, and each continuation page says
       which prompt it continues;
    3. a single message larger than a whole page gets a page to itself, marked
       oversize. Nothing is ever truncated and no message is ever split.

    Prefix-stable: every decision depends only on what has already been placed and
    on the unit being placed, and only the last group of a live transcript can still
    grow. So appending turns can change the last page and add pages after it, and
    never changes a page that already has a successor.

    `shell_size(number, continues)` returns (bytes, newlines, tokens) of a page
    with no messages on it, so the budget covers the file as written.
    """
    def open_page(continues: int = 0) -> _Page:
        number = len(pages) + 1
        nbytes, newlines, tokens = shell_size(number, continues)
        page = _Page(number=number, shell_bytes=nbytes, shell_newlines=newlines,
                     shell_tokens=tokens, continues=continues)
        pages.append(page)
        return page

    pages: list[_Page] = []
    page = open_page()
    for group in groups:
        drawn = [f for f in group if f.drawn]

        if not page.empty and not page.fits(drawn, limits):
            page = open_page()
        if page.fits(drawn, limits):
            for fragment in group:
                page.add(fragment)
            continue

        # The group is larger than a page on its own: split between messages.
        for fragment in group:
            if fragment.drawn and not page.empty and not page.fits([fragment], limits):
                page = open_page(continues=fragment.prompt)
            if fragment.drawn and page.empty and not page.fits([fragment], limits):
                page.oversize = True
            page.add(fragment)
    return pages


def _span(first: int, last: int, prefix: str = "") -> str:
    if first == last:
        return f"{prefix}{first}"
    return f"{prefix}{first}–{prefix}{last}"


def _thousands(value: int) -> str:
    return f"{value:,}"


def render_html(session: Session, out_dir: Path, *, brief: bool = False,
                include_tools: bool = True, include_thinking: bool = True) -> Path:
    """Write the pages, full.html and index.html into out_dir; return the index.

    The content filters are applied here rather than by the caller so that
    prompt grouping, the index counters and `session.stats()` all describe the
    document that was actually written. Everything below works from the filtered
    session, so the pages, full.html and the index cannot disagree about what was
    excluded.
    """
    session = filtered(session, brief=brief, include_tools=include_tools,
                       include_thinking=include_thinking)
    out_dir.mkdir(parents=True, exist_ok=True)
    env = _env()
    limits = page_limits()
    exported = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %Z")
    common = {"session": session, "version": __version__}

    # ---- render every message once; the pages and full.html share the result
    message_macro = env.get_template("macros.html").module.message
    groups = session.prompt_groups()
    fragment_groups: list[list[_Fragment]] = []
    anchor = 0
    for prompt_number, group in enumerate(groups, start=1):
        fragments = []
        for m in group:
            fragment = _Fragment(anchor=anchor, prompt=prompt_number,
                                 timestamp=m.timestamp)
            # A message stripped bare by a filter would render as an empty card;
            # skip drawing it, but still spend its anchor so numbering matches
            # the unfiltered transcript.
            if m.blocks:
                fragment.html = str(message_macro(_role_view(m, session, anchor)))
                (fragment.nbytes, fragment.newlines,
                 fragment.tokens) = _size(fragment.html)
            fragments.append(fragment)
            anchor += 1
        fragment_groups.append(fragments)

    # ---- pack
    page_tpl = env.get_template("page.html")

    title = " ".join((session.title or "").split())    # one line, always
    if len(title) > PAGE_TITLE_CHARS:
        title = title[:PAGE_TITLE_CHARS - 1] + "…"

    def render_page(page: _Page, *, has_next: bool, body: str = "",
                    page_title: str = title) -> str:
        # No export time, no page total and no xexport version on a page: each
        # changes without the conversation changing, and a page that already
        # has a successor must stay byte-identical so links and refreshes are
        # stable.
        return page_tpl.render(number=page.number, has_next=has_next,
                               continues=page.continues, oversize=page.oversize,
                               body=Markup(body), exported="", page_title=page_title,
                               session=session, version="")

    def shell_size(number: int, continues: int) -> tuple[int, int, float]:
        # The larger of the two navigation states, so the last page (whose
        # "Next" is a disabled span, a few bytes longer than the link) is
        # covered too; and a fixed allowance in place of the real title.
        # A shell differs from page to page only in the digits of the numbers
        # it prints, and every digit weighs the same, so shells whose numbers
        # have the same widths have the same size.
        # A continuation page has a note the others do not, so it is its own
        # case, not just a different width of the number in the note.
        key = (bool(continues),) + tuple(
            len(str(n)) for n in (number - 1, number, number + 1, continues))
        if key not in shell_sizes:
            blank = _Page(number, continues=continues)
            sizes = [_size(render_page(blank, has_next=state, page_title=""))
                     for state in (True, False)]
            shell_sizes[key] = (max(s[0] for s in sizes) + PAGE_TITLE_BYTES,
                                max(s[1] for s in sizes),
                                max(s[2] for s in sizes) + PAGE_TITLE_TOKENS)
        return shell_sizes[key]

    shell_sizes: dict[tuple[int, ...], tuple[int, int, float]] = {}

    pages = _paginate(fragment_groups, limits, shell_size)
    total_pages = len(pages)

    # ---- shared assets first: every page and the index link them
    # Read as source, not rendered as templates: neither is one. A checkout can
    # hand them back with CRLF endings, and the output should not depend on that.
    css = env.loader.get_source(env, "xexport.css")[0].replace("\r\n", "\n")
    js = env.loader.get_source(env, "xexport.js")[0].replace("\r\n", "\n")
    _publish(out_dir / "xexport.css", css)
    _publish(out_dir / "xexport.js", js)

    # ---- pages
    page_of_anchor: dict[int, int] = {}
    page_opens_with: dict[int, int] = {}    # page number -> its first drawn anchor
    page_map = []
    for page in pages:
        for a in page.anchors:
            page_of_anchor[a] = page.number
        if page.fragments:
            page_opens_with[page.number] = page.fragments[0].anchor
        html = render_page(page, has_next=page.number < total_pages,
                           body="\n".join(f.html for f in page.fragments))
        _publish(out_dir / page_name(page.number), html)
        nbytes, newlines, tokens = _size(html)
        longest = max(len(line) for line in html.split("\n"))
        drawn = [f.anchor for f in page.fragments]
        page_map.append({
            "number": page.number,
            "href": page_name(page.number),
            "prompts": _span(page.prompts[0], page.prompts[-1], "#")
            if page.prompts else "",
            "messages": _span(drawn[0], drawn[-1]) if drawn else "",
            # Seconds are enough to place a page in time, and this row is paid
            # for once per page by every reader of the index.
            "ts": next((f.timestamp for f in page.fragments if f.timestamp), "")[:19],
            "lines": _thousands(newlines),
            "kb": _thousands(math.ceil(nbytes / 1000)),
            "tokens": _thousands(math.ceil(tokens)),
            "notes": "; ".join(note for note in (
                "OVERSIZE: read in slices" if page.oversize else "",
                f"continues #{page.continues}" if page.continues else "",
                f"longest line {_thousands(longest)} chars"
                if longest > LONG_LINE_CHARS else "",
            ) if note),
        })

    # Re-rendering into an existing folder: drop pages left over from a previous,
    # longer render so nothing stale stays linkable. Done BEFORE the index is
    # written, so the index is the last file published and never points at a page
    # that is about to be removed.
    for stale in out_dir.glob("page-*.html"):
        try:
            if int(stale.stem.split("-")[1]) > total_pages:
                stale.unlink()
        except (ValueError, IndexError, OSError):
            continue

    # ---- full.html: the same fragments, in one file, with the page starts marked
    parts = []
    for page in pages:
        parts.append(
            f'<div class="page-break" id="page-{page.number}" '
            f'data-page="{page.number}"><a href="{page_name(page.number)}">'
            f'Page {page.number}</a></div>')
        parts.extend(f.html for f in page.fragments)
    body = "\n".join(parts)
    full_tpl = env.get_template("full.html")
    drawn_count = sum(len(page.fragments) for page in pages)

    def render_full(content: str, nbytes: int, tokens: float) -> str:
        return full_tpl.render(
            body=Markup(content), total_pages=total_pages,
            message_count=drawn_count,
            size_kb=_thousands(math.ceil(nbytes / 1000)),
            size_tokens=_thousands(math.ceil(tokens)),
            inline_assets={"css": Markup(css), "js": Markup(js)},
            exported=exported, **common)

    # The banner states the file's own size. That is the empty document plus
    # the pieces, which were all measured when they were rendered; measuring
    # the finished file again would be a second pass over megabytes. The
    # figure is off by the page markers and its own digits, which "about"
    # covers.
    shell_bytes, _, shell_tokens = _size(render_full("", 0, 0.0))
    drawn = [f for page in pages for f in page.fragments]
    full_bytes = shell_bytes + sum(f.nbytes + 1 for f in drawn)
    full_tokens = shell_tokens + sum(f.tokens + TOKENS_PER_NEWLINE for f in drawn)
    atomic_write_text(out_dir / "full.html",
                      render_full(body, full_bytes, full_tokens))

    # ---- index, last: it must never point at a file that is not there yet
    index_items = []
    for prompt_number, fragments in enumerate(fragment_groups, start=1):
        group = groups[prompt_number - 1]
        drawn = [f for f in fragments if f.drawn]
        # The index must link to a message that was actually drawn. Anchoring on
        # the group's first message linked to nothing whenever a filter emptied
        # it -- reachable for group 1, which absorbs any metadata before the
        # first prompt, and --no-tools empties exactly that.
        first = drawn[0].anchor if drawn else fragments[0].anchor
        last = drawn[-1].anchor if drawn else first
        first_page, last_page = page_of_anchor[first], page_of_anchor[last]
        long_text = _group_long_text(group)
        index_items.append({
            "number": prompt_number,
            # A group a filter emptied entirely has no message to land on, so
            # its card links to the page and names no anchor.
            "href": page_name(first_page) + (f"#msg-{first}" if drawn else ""),
            "page": first_page,
            # Only where the page really opens with this prompt. A page that
            # opens mid-prompt is shown by the badge of the prompt it continues.
            "starts_page": page_opens_with.get(first_page) == first,
            "pages_label": ("page " if first_page == last_page else "pages ")
            + _span(first_page, last_page),
            "ts": next((m.timestamp for m in group if m.timestamp), ""),
            "prompt_html": _md(_group_prompt(group)),
            "stats_line": _group_stats_line(group),
            "long_html": _md(long_text) if long_text else "",
        })

    index_tpl = env.get_template("index.html")
    # Count what was rendered, not what was parsed: a filter can empty a message,
    # and an index reading "4 messages" above two cards is simply wrong.
    visible = copy.copy(session)
    visible.messages = [m for m in session.messages if m.blocks]
    html = index_tpl.render(total_pages=total_pages, index_items=index_items,
                            page_map=page_map, limits=limits,
                            stats=visible.stats(), exported=exported, **common)
    index_path = out_dir / "index.html"
    atomic_write_text(index_path, html)
    return index_path
