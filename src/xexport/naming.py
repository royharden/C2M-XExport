"""Export names: AgentNamer callsign + chat title + short session id.

The name is built from a template so the shape is a preference, not a decision baked
into the code:

    {callsign}_{title}_{id8}   ->  0007_Claude_Opus5_Update xexport skills_a68ce6ac

Empty fields collapse away, which is what makes the callsign prefix appear only for
projects that actually use AgentNamer — no conditional logic anywhere else.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from .model import Session
from .titles import sanitize_title

# Roy's choice, 2026-09-04: the full session id, for exact traceability back to the
# source transcript. Reverting to the compact form is one environment variable and no
# code change:
#
#     setx XEXPORT_NAME_TEMPLATE "{callsign} -- {title} -- {id8}"
#
# Measured before committing to it: even the deepest .chatexports root on this machine
# (…\NetScreen\NetScreen-JuniperConvert) leaves 90 characters for the title once
# "\html\<name>\page-001.html" is accounted for, so full ids do not risk MAX_PATH.
DEFAULT_TEMPLATE = "{agent} -- {title} -- {identity}"
MAX_NAME = 150
CALLSIGN_MAX = 60
_NO_TRIM = 10_000   # sanitize_title max_len that never truncates

FIELDS = (
    "agent", "callsign", "handle", "title", "identity", "id8", "idtail", "id",
    "source", "app", "model", "date", "time",
)

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]*)\}")

# AgentNamer grammar: <ID>_<Harness>_<Model>[_Sub][_<Team>_<Role>][_<Title>][_<Other>].
# Tokens never contain "_", "." or a space, so the name ends at the first token that
# does not look like one. AgentNamer's own CALLSIGN_RE stops after <Model>; this one
# keeps going so a full multi-token callsign survives.
_CALLSIGN_RE = re.compile(
    r"\b([0-9A-Z]{2}[0-9]{2}_(?:Claude|Codex|Cursor|Grok)_[A-Za-z0-9-]+"
    r"(?:_[A-Za-z0-9-]+)*)"
)

_AGENT_PREFIX = re.compile(r"^agent-", re.IGNORECASE)


# --------------------------------------------------------------------------- ids
def short_id(session_id: str, *, tail: bool = False) -> str:
    """8-char handle for a session id.

    Claude/Cursor ids are UUIDv4 (random) and Codex ids are UUIDv7 (the first 32 bits
    of a millisecond timestamp, which advance every ~65 s), so an 8-char *prefix* is
    effectively unique for all three — and it matches what ``xexport list`` prints, so
    a file name can be eyeballed back to a listing. ``tail=True`` takes the last 8
    instead for anyone who wants maximum entropy.

    A subagent transcript is named ``agent-<hex>``; the prefix is dropped so the
    handle is the agent's own hex rather than the word "agent".
    """
    raw = _AGENT_PREFIX.sub("", session_id or "")
    raw = re.sub(r"[^0-9A-Za-z]", "", raw)
    if not raw:
        return ""
    return raw[-8:] if tail else raw[:8]


def handle_of(callsign: str) -> str:
    """Lowercase/underscore form, matching AgentNamer's own `handle` field."""
    return re.sub(r"[^a-z0-9_]+", "_", (callsign or "").lower()).strip("_")


_HARNESS = {"claude": "Claude", "codex": "Codex", "cursor": "Cursor", "grok": "Grok"}
_VENDOR_PREFIX = re.compile(r"^(?:claude|anthropic|openai|google)[-./]", re.IGNORECASE)


def harness_token(source: str) -> str:
    """`claude` -> `Claude`, matching AgentNamer's harness vocabulary."""
    return _HARNESS.get((source or "").lower(), (source or "").title())


def normalize_model(raw: str) -> str:
    """Turn a raw model id into an AgentNamer-style model token.

    AgentNamer stores the model with dots and spaces removed — a real record on this
    machine reads `{"harness": "Claude", "model": "Fable51", ...}`. The fallback label
    has to produce the same token so a project that later adopts AgentNamer does not
    suddenly rename all its exports.

        claude-fable-5-1          -> Fable51
        claude-opus-4-8           -> Opus48
        claude-sonnet-5           -> Sonnet5
        claude-haiku-4-5-20251001 -> Haiku45      (release date dropped)
        gpt-5.5                   -> GPT55
        gpt-5.3-codex             -> GPT53-Codex
    """
    text = _VENDOR_PREFIX.sub("", (raw or "").strip())
    parts = [p for p in re.split(r"[-._\s]+", text) if p]
    if not parts:
        return ""
    family = parts[0]
    family = "GPT" if family.lower() == "gpt" else family[:1].upper() + family[1:].lower()
    digits: list[str] = []
    variant = ""
    for part in parts[1:]:
        if part.isdigit():
            # A trailing 6+ digit run is a release date (…-20251001), not a version.
            if len(part) < 6:
                digits.append(part)
            continue
        if not variant and part.isalnum():
            variant = part[:1].upper() + part[1:]
    token = family + "".join(digits)
    if variant:
        token = f"{token}-{variant}"
    return sanitize_title(token, 40)


def agent_label(session: Session, callsign: str = "") -> str:
    """The agent identity that prefixes an export name.

    Roy's choice, 2026-09-04: an AgentNamer callsign when the project has a registry,
    and harness+model otherwise — so every export says which agent produced it, without
    forcing AgentNamer into projects that have not adopted it. The two forms are
    deliberately compatible: `0000_Claude_Fable51` degrades to `Claude_Fable51`.
    """
    if callsign:
        return callsign
    harness = harness_token(session.source)
    if not harness:
        return ""
    model = normalize_model(session.model)
    label = f"{harness}_{model}" if model else harness
    if session.is_subagent:
        # Keeps subagents legible now that they sit beside the main exports.
        label += "_Sub"
    return label


def identity(source: str, session_id: str) -> str:
    """`<source>-<full session id>` — the stable marker that names an export.

    Never truncated: it is what makes an export traceable back to its transcript, and
    what an update finds when the chat title has changed underneath it.

    A Claude subagent's id is already `agent-<hex>`, so a subagent export reads
    `… -- claude-agent-a109fa34123bba393`, which marks it as a subagent in a flat
    folder without needing a separate directory.
    """
    # sanitize_title("") returns "session", which would silently turn an unknown
    # source into a literal "session-" prefix. Guard before sanitising, not after.
    safe_source = sanitize_title(source.lower(), 20) if (source or "").strip() else ""
    safe_session = sanitize_title(session_id, 80) if (session_id or "").strip() else ""
    if safe_source and safe_session:
        return f"{safe_source}-{safe_session}"
    return safe_session or safe_source


# --------------------------------------------------------------------- callsigns
def _agentnamer_script() -> Path | None:
    """Locate AgentNamer's claim.py without hard-coding anyone's home directory."""
    override = os.environ.get("XEXPORT_AGENTNAMER", "").strip()
    if override:
        p = Path(override)
        return p if p.is_file() else None
    home = Path.home()
    for mirror in (".claude", ".codex", ".grok"):
        p = home / mirror / "skills" / "AgentNamer" / "scripts" / "claim.py"
        if p.is_file():
            return p
    return None


def _callsign_from_agentnamer() -> str:
    """`claim.py whoami` -> callsign, or "" for unclaimed / no registry / no script.

    Runs under this interpreter rather than a bare `python`/`python3`, which sidesteps
    the Windows launcher entirely (claim.py is stdlib-only, 3.8+). Exit 3 (unclaimed)
    and exit 4 (project has no registry) both correctly mean "no prefix".
    """
    script = _agentnamer_script()
    if not script:
        return ""
    try:
        proc = subprocess.run(
            [sys.executable, str(script), "whoami"],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    m = re.match(r"CALLSIGN\s+(\S+)", (proc.stdout or "").strip())
    return m.group(1) if m else ""


def _callsign_from_transcript(session: Session | None) -> str:
    """A subagent's callsign, recovered from the prompt its parent wrote.

    AgentNamer requires a parent to open a subagent's prompt with
    "Your callsign is <name> (parent <ID>). ...", so a subagent transcript carries its
    own callsign in its first user message. Only consulted for subagents: a top-level
    prompt that merely *mentions* a callsign must not be mistaken for one.
    """
    if session is None or not session.is_subagent:
        return ""
    for message in session.messages[:4]:
        if message.role != "user":
            continue
        for block in message.blocks:
            m = _CALLSIGN_RE.search(block.text or "")
            if m:
                return m.group(1)
    return ""


def resolve_callsign(explicit: str | None, *, session: Session | None = None) -> str:
    """--callsign VALUE > $XEXPORT_CALLSIGN > (--callsign auto) > "" ."""
    value = explicit if explicit is not None else os.environ.get("XEXPORT_CALLSIGN", "")
    value = (value or "").strip()
    if not value:
        return ""
    if value.lower() != "auto":
        return sanitize_title(value, CALLSIGN_MAX)

    # A subagent shares its PARENT's session id, so asking AgentNamer "who is this
    # session?" answers with the parent's callsign — every subagent export would be
    # stamped with its parent's name. Read the callsign the parent wrote into the
    # subagent's own first prompt instead, and never fall through to whoami here.
    if session is not None and session.is_subagent:
        found = _callsign_from_transcript(session)
    else:
        found = _callsign_from_agentnamer()
    return sanitize_title(found, CALLSIGN_MAX) if found else ""


# ------------------------------------------------------------------------ names
def check_template(template: str) -> None:
    unknown = sorted(
        {m.group(1).lower() for m in _PLACEHOLDER.finditer(template)} - set(FIELDS)
    )
    if unknown:
        raise ValueError(
            "unknown name-template field(s): "
            + ", ".join("{%s}" % f for f in unknown)
            + ". Valid fields: "
            + ", ".join("{%s}" % f for f in FIELDS)
        )


def _render(template: str, values: dict[str, str]) -> str:
    return _PLACEHOLDER.sub(
        lambda m: str(values.get(m.group(1).lower(), "")), template
    )


def _collapse(name: str) -> str:
    """Squeeze the separators an empty field leaves behind.

    An absent callsign must leave `Title -- claude-<id>`, never ` --  -- Title …`.
    """
    name = re.sub(r"\s+", " ", name)
    name = re.sub(r"_{2,}", "_", name)
    name = re.sub(r"(?:_ | _)", "_", name)
    name = re.sub(r"(?:\s*--\s*){2,}", " -- ", name)   # doubled " -- " separators
    return name.strip(" _-·")


def build_name(
    session: Session,
    *,
    title: str | None = None,
    callsign: str = "",
    template: str | None = None,
    max_name: int = MAX_NAME,
) -> str:
    """Assemble the export name, shrinking only {title} to fit `max_name`.

    The callsign and the id are the identifying parts, so the title is the only thing
    that gives ground. The cap matters on Windows/OneDrive: an HTML export appends
    "\\page-001.html" under a root that is already ~45 characters deep.
    """
    if template is None:
        template = (os.environ.get("XEXPORT_NAME_TEMPLATE", "").strip()
                    or DEFAULT_TEMPLATE)
    if not template.strip():
        # An empty template used to fall through to the default, so `--name-template ""`
        # silently did the opposite of what it looked like. Unlike --html-subdir "",
        # there is no sensible "no name at all".
        raise ValueError(
            '--name-template cannot be empty; use "{title}" for just the chat title'
        )
    check_template(template)

    now = datetime.now().astimezone()
    values = {
        "agent": agent_label(session, callsign),
        "callsign": callsign or "",
        "handle": handle_of(callsign),
        "identity": identity(session.source, session.session_id),
        "id8": short_id(session.session_id),
        "idtail": short_id(session.session_id, tail=True),
        "id": session.session_id or "",
        "source": session.source or "",
        "app": session.app or session.source or "",
        "model": normalize_model(session.model),
        "date": now.strftime("%Y-%m-%d"),
        "time": now.strftime("%H%M"),
    }

    clean_title = sanitize_title(title if title is not None else session.title)

    def assemble(text: str, vals: dict) -> str:
        # _NO_TRIM: sanitize, but never length-truncate here. A blanket truncation at
        # max_name cuts from the right, which eats the session id — the one part that
        # makes an export traceable back to its transcript.
        return sanitize_title(
            _collapse(_render(template, {**vals, "title": text})), _NO_TRIM
        )

    name = assemble(clean_title, values)
    budget = len(clean_title)
    while len(name) > max_name and budget > 0:
        budget = max(0, budget - (len(name) - max_name))
        name = assemble(clean_title[:budget], values)

    if len(name) > max_name and (values.get("agent") or values.get("callsign")):
        # No title left and still too long: the agent prefix is oversized. Trim it,
        # never the identity suffix.
        over = len(name) - max_name
        values = dict(values)
        for key in ("agent", "callsign"):
            if values.get(key):
                values[key] = values[key][:-over].rstrip("_-· ")
        values["handle"] = handle_of(values["callsign"])
        name = assemble("", values)

    return name or "session"
