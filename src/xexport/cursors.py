"""What an export was made from, recorded inside the export itself.

An HTML comment on the last line of a Markdown transcript, a small JSON file inside
an HTML export folder. Keeping it in the artefact rather than in a sidecar beside it
is the whole point: `.chatexports` is meant to be a folder of transcripts you can
scan, not a folder of transcripts and their paperwork.

A refresh re-renders the whole document, so this record is not a resume point. It
exists to answer three questions *before* an existing export is overwritten:

- has anything actually changed (`digest`), so an unchanged turn costs no write;
- would this refresh drop content (`messages`), which is the one case re-rendering
  loses something;
- was the export made with different content filters (`opts`), so a --brief hook
  cannot quietly downgrade a full-fidelity transcript.

Every check fails **closed**: anything unexpected -- including a marker that is
missing, truncated or from a newer version -- refuses the existing export and writes
beside it. A duplicate file is recoverable; a transcript with a hole in it is not.
The companion carries `forked`, which is what lets the next run adopt it rather than
refusing the same original again.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path

from . import __version__, naming
from .model import Session

CURSOR_V = 1

# " (2)", " (3)" ... as unique_path appends them.
_NUMBERED = re.compile(r" \((\d+)\)$")
HTML_CURSOR_NAME = ".xexport-cursor.json"
TAIL_BYTES = 4096
TITLE_IN_MARKER = 120   # keeps the marker far inside TAIL_BYTES

MARKER_RE = re.compile(r"<!--\s*xexport-cursor\s+v(\d+)\s+(\{.*?\})\s*-->", re.DOTALL)


# ------------------------------------------------------------------- fingerprints
# Byte separators for content_digest, so that moving text between adjacent fields
# cannot produce the same hash.
_SEP_MESSAGE = b""
_SEP_BLOCK = b""
_SEP_FIELD = b""
def content_digest(session: Session) -> str:
    """Fingerprint of everything an export would render from this session.

    Change detection for a re-render has to answer "is the document that would
    be written the same as the one on disk", and a message *count* cannot: an
    edited, retried or reordered turn leaves the count untouched. Every store
    xexport reads is small and local, so hashing the whole conversation costs
    microseconds and removes that blind spot rather than documenting it.

    Deliberately covers the source content only. A rendered document carries an
    "Exported:" timestamp that changes every run, so comparing rendered bytes
    would never match; comparing what they are rendered from does.
    """
    h = hashlib.sha1()
    h.update(f"{session.title}|{session.model}|{session.app}".encode("utf-8", "replace"))
    for message in session.messages:
        h.update(_SEP_MESSAGE)
        h.update(f"{message.role}|{message.timestamp}".encode("utf-8", "replace"))
        for block in message.blocks:
            h.update(_SEP_BLOCK)
            h.update(f"{block.kind}|{block.name}|{block.detail}|{block.is_error}"
                     .encode("utf-8", "replace"))
            for part in (block.text, block.args, block.output):
                h.update(_SEP_FIELD)
                h.update((part or "").encode("utf-8", "replace"))
    return h.hexdigest()[:16]


def options_fingerprint(*, brief: bool, include_tools: bool,
                        include_thinking: bool, truncate: int) -> str:
    """Identifies the render options a run used.

    An append rendered with different options is not a continuation. The damaging case
    is real: `--brief` skips tool and thinking blocks, so a delta made entirely of tool
    traffic renders to nothing while the cursor still advances past it — those turns
    would then be skipped forever, including by a later full append.
    """
    payload = f"{bool(brief)}|{bool(include_tools)}|{bool(include_thinking)}|{int(truncate)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:8]


# ------------------------------------------------------------------------ records
def make(session: Session, *, messages: int, run: int, opts: str = "",
         forked: bool = False) -> dict:
    return {
        "v": CURSOR_V,
        # True when this export was written beside one that was refused. It is what
        # tells the next run that this companion, and not the untouchable original,
        # is the file to keep refreshing -- otherwise a refusal repeats every turn.
        "forked": bool(forked),
        "session_id": session.session_id,
        "source": session.source,
        "messages": messages,
        "prompts": sum(1 for m in session.messages[:messages] if session.is_prompt(m)),
        "digest": content_digest(session),
        "opts": opts,
        "run": run,
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "tool": __version__,
        "title": (session.title or "")[:TITLE_IN_MARKER],
    }


def marker_line(data: dict) -> str:
    blob = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # A title containing "-->" would otherwise close the comment early and make every
    # later read see a truncated marker. > is valid JSON and cannot do that.
    blob = blob.replace(">", "\\u003e")
    return f"<!-- xexport-cursor v{CURSOR_V} {blob} -->"


# ------------------------------------------------------------------------ reading
def read_md_marker(path: Path) -> dict | None:
    """Last marker in a Markdown export, read from the file's tail.

    Content after the final marker is simply part of the document: a refresh
    append did not finish (or someone edited the file afterwards). Either way the
    cursor no longer describes the file, so callers must not append onto it.
    """
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > TAIL_BYTES:
                f.seek(-TAIL_BYTES, os.SEEK_END)
            raw = f.read()
    except OSError:
        return None
    # The seek can land mid-character; errors="replace" is the house rule anyway.
    text = raw.decode("utf-8", "replace")
    matches = list(MARKER_RE.finditer(text))
    if not matches:
        return None
    last = matches[-1]
    try:
        data = json.loads(last.group(2))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    data.setdefault("v", int(last.group(1)))
    return data


def read_html_marker(folder: Path) -> dict | None:
    p = folder / HTML_CURSOR_NAME
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def write_html_marker(folder: Path, data: dict) -> None:
    """Published the same way as the pages it describes, so a crash mid-write
    cannot leave a folder whose marker is truncated JSON -- which now reads as
    "unverifiable" and would cost a companion folder."""
    (folder / HTML_CURSOR_NAME).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------------- validation
def validate(data: dict, session: Session, *, opts: str = "") -> tuple[str, str]:
    """Return (verdict, detail) for refreshing the export `data` came from.

    verdict: "ok" | "uptodate" | "shrink" | "options" | "unsupported"

    An export is refreshed by re-rendering the whole document, so a transcript that
    was edited, retried, compacted or reordered behind the last export needs no
    detection: the next render simply reflects it. Only three things still have to
    be caught before overwriting an export that is already on disk.
    """
    try:
        version = int(data.get("v", 0))
    except (TypeError, ValueError):
        version = 0
    if version > CURSOR_V:
        return "unsupported", (
            f"its marker is version {version}, newer than this build understands "
            f"(v{CURSOR_V})"
        )

    count = data.get("messages")
    if not isinstance(count, int) or count < 0:
        # Fail closed. Without a usable marker there is no way to tell whether this
        # refresh would drop content or change fidelity, and re-rendering over it
        # would do so silently. Refusing costs one companion file, which carries a
        # marker, so every later run refreshes that instead -- it self-heals.
        return "unverifiable", (
            "it carries no usable xexport marker, so there is no way to tell whether "
            "refreshing it would lose content"
        )

    total = len(session.messages)

    # A source shorter than what was exported is the one case a re-render loses
    # content. It is the single hole in the re-render design, and it is cheap to
    # close: refuse, keep the longer export, and write the shorter one beside it.
    if count > total:
        return "shrink", (
            f"it holds {count} messages but the transcript now has only {total}, "
            f"so refreshing it would drop {count - total}"
        )

    previous_opts = str(data.get("opts") or "")
    if opts and previous_opts and previous_opts != opts:
        # Silently rewriting a full-fidelity export as --brief (or the reverse) is
        # a fidelity change the user never asked for. The hook runs one way and a
        # hand-run export the other, so this is reachable in normal use.
        return "options", (
            "it was written with different render options "
            "(--brief/--full/--no-tools/--no-thinking)"
        )

    # Exact change detection: the digest covers every message and block, so an
    # edited or retried turn is caught even though the count did not move.
    previous_digest = str(data.get("digest") or "")
    if previous_digest and previous_digest == content_digest(session):
        return "uptodate", ""
    if not previous_digest and count == total:
        # Pre-digest marker: fall back to the count it does carry.
        return "uptodate", ""
    return "ok", ""


# ------------------------------------------------------------------------ lookup
def _ordered_candidates(directory: Path, pattern_hint: str, suffix: str) -> list[Path]:
    """Files that could hold this session's marker: the likely ones first."""
    seen: set[Path] = set()
    ordered: list[Path] = []
    patterns = []
    if pattern_hint:
        patterns.append(f"*{pattern_hint}*{suffix}")
    patterns.append(f"*{suffix}")
    for pattern in patterns:
        for p in sorted(directory.glob(pattern)):
            if p in seen:
                continue
            seen.add(p)
            ordered.append(p)
    return ordered


def _best(hits: list[tuple[Path, dict]], session: Session,
          opts: str) -> tuple[Path, dict] | None:
    """Pick which export to extend: a usable cursor always beats a stale one.

    Ranking by message count alone is what turns one shortened transcript into an
    endless fork: the stale pre-compaction export always has the highest count, so it
    is re-selected every run, fails validation every run, and forces another " (n)"
    file every run — once per turn under the autosave hook. Preferring a cursor that
    actually validates means the fallback export written on the first failure is the
    one every later run extends, so a break costs exactly one extra file.
    """
    # A " (2)" name is either a deliberate --mode new snapshot or the companion a
    # refusal wrote. Only the latter may be adopted: adopting a snapshot would make
    # the point-in-time copy the living document and silently freeze the original.
    hits = [(p, d) for p, d in hits
            if not _NUMBERED.search(p.stem if p.suffix else p.name) or d.get("forked")]
    if not hits:
        return None

    def rank(item: tuple[Path, dict]) -> tuple[int, int, float]:
        path, data = item
        verdict, _ = validate(data, session, opts=opts)
        usable = 1 if verdict in ("ok", "uptodate") else 0
        return (usable, int(data.get("messages") or 0), path.stat().st_mtime)

    return max(hits, key=rank)


def find_md_export(md_dir: Path, session: Session,
                   opts: str = "") -> tuple[Path, dict] | None:
    """Locate this session's Markdown export **by session id, never by title**.

    Chat titles are rewritten while a chat runs (Claude rewrites `ai-title`, and a user
    can set `custom-title` at any point), so a title match would fork the export into a
    second file the moment a chat was retitled — the exact clutter this is meant to
    prevent.
    """
    if not md_dir.is_dir():
        return None
    hits = []
    for p in _ordered_candidates(md_dir, naming.short_id(session.session_id), ".md"):
        data = read_md_marker(p)
        if data and data.get("session_id") == session.session_id:
            hits.append((p, data))
    return _best(hits, session, opts)


def find_html_export(html_dir: Path, session: Session,
                     opts: str = "") -> tuple[Path, dict] | None:
    if not html_dir.is_dir():
        return None
    hits = []
    for folder in sorted(p for p in html_dir.iterdir() if p.is_dir()):
        data = read_html_marker(folder)
        if data and data.get("session_id") == session.session_id:
            hits.append((folder, data))
    return _best(hits, session, opts)
