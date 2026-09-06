"""xexport command-line interface."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
import webbrowser
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import click

from . import __version__, cursors, detect, naming
from .model import Session
from .render.html import render_html
from .render.markdown import addendum_header, describe_delta, render_markdown
from .sources import claude, codex, cursor
from .titles import unique_path

_SOURCE_CHOICES = ["auto", "claude", "codex", "cursor"]
_MODE_CHOICES = ["new", "append", "replace"]


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass


# ============================================================== options container
@dataclass
class Options:
    """Everything `_export` needs, so it does not take twenty positional arguments."""

    fmt: str = "html"
    out: str | None = None
    name: str | None = None
    brief: bool = False
    no_tools: bool = False
    no_thinking: bool = False
    full: bool = False
    copy_json: bool = False
    open_after: bool = False
    mode: str = "new"
    seamless: bool = False
    callsign: str | None = None
    name_template: str | None = None
    html_subdir: str = "html"
    # Roy's choice, 2026-09-04: subagent exports sit beside the main ones, not in
    # their own folder — one place to look. They are self-marking because a Claude
    # subagent's id is `agent-<hex>`, so the name ends `-- claude-agent-a109fa34…`.
    subagent_subdir: str = ""
    max_name: int = naming.MAX_NAME
    quiet: bool = False
    seen_notes: set[str] = field(default_factory=set)

    @property
    def md_kwargs(self) -> dict:
        return dict(
            brief=self.brief,
            include_tools=not self.no_tools,
            include_thinking=not self.no_thinking,
            truncate=0 if self.full else 2000,
        )

    @property
    def html_kwargs(self) -> dict:
        """The same content filters, minus truncation.

        --brief/--no-tools/--no-thinking are privacy controls and must reach every
        format. --full only sets a Markdown truncation width; HTML truncates in the
        browser via the expand control, so it is deliberately absent here.
        """
        return dict(
            brief=self.brief,
            include_tools=not self.no_tools,
            include_thinking=not self.no_thinking,
        )

    @property
    def html_opts_fingerprint(self) -> str:
        """Fingerprint of the options that change HTML content.

        Held apart from the Markdown one so that --full, which HTML ignores, does
        not make an HTML export look incompatible with itself.
        """
        return cursors.options_fingerprint(**self.html_kwargs, truncate=0)


def _options(kw: dict) -> Options:
    """Build Options from click kwargs, resolving the --append alias.

    `--mode new --append` must be an error, not a silent win for --append. Comparing
    values cannot tell "new because the user typed it" from "new by default", so ask
    click where the value came from.
    """
    data = {f: kw[f] for f in Options.__dataclass_fields__ if f in kw}
    if kw.get("append"):
        ctx = click.get_current_context(silent=True)
        source = ctx.get_parameter_source("mode") if ctx is not None else None
        typed = source is not None and getattr(source, "name", "") == "COMMANDLINE"
        if typed and data.get("mode") != "append":
            raise click.UsageError(
                f"--append conflicts with --mode {data['mode']}; pass only one."
            )
        data["mode"] = "append"
    return Options(**data)


def _say(opt: Options, message: str) -> None:
    if not opt.quiet:
        click.echo(message)


def _say_once(opt: Options, key: str, message: str) -> None:
    """`--format both` runs two exports; identical notes must not print twice."""
    if key in opt.seen_notes:
        return
    opt.seen_notes.add(key)
    _say(opt, message)


def _warn(message: str) -> None:
    click.echo(message, err=True)


@contextmanager
def _export_lock(path: Path):
    """Serialise writes to one export across concurrent xexport processes.

    Reachable in normal use: the recommended autosave setup runs a Stop hook and a
    SubagentStop hook, which can overlap. Two appends racing on the same file both read
    the same cursor and both write the same delta. The lock lives in the temp dir, not
    beside the export, so `.chatexports` stays free of paperwork.
    """
    token = hashlib.sha1(str(path.resolve()).encode("utf-8", "replace")).hexdigest()[:16]
    lock = Path(tempfile.gettempdir()) / f"xexport-{token}.lock"
    fd = None
    for _ in range(60):                      # ~6 s
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:                             # break a lock left by a killed process
                if time.time() - lock.stat().st_mtime > 60:
                    lock.unlink(missing_ok=True)
                    continue
            except OSError:
                pass
            time.sleep(0.1)
        except OSError:
            break                            # no temp dir: proceed unlocked
    try:
        yield
    finally:
        if fd is not None:
            try:
                os.close(fd)
                lock.unlink(missing_ok=True)
            except OSError:
                pass


# ================================================================ source plumbing
def _sniff_source(path: Path) -> str:
    """Guess store from path layout or first-line shape."""
    parts = {p.lower() for p in path.parts}
    if "agent-transcripts" in parts:
        return "cursor"
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            first = f.readline()
        obj = json.loads(first)
        if isinstance(obj, dict) and obj.get("type") == "session_meta":
            return "codex"
        if isinstance(obj, dict) and obj.get("role") in ("user", "assistant"):
            # Cursor agent transcripts use role at the top level.
            msg = obj.get("message")
            if isinstance(msg, dict) and "content" in msg and "type" not in obj:
                return "cursor"
    except (OSError, json.JSONDecodeError, ValueError):
        pass
    return "claude"


def _parse(path: Path, source: str) -> Session:
    if source == "auto":
        source = _sniff_source(path)
    if source == "codex":
        return codex.parse_file(path)
    if source == "cursor":
        return cursor.parse_file(path)
    return claude.parse_file(path)


def _resolve_ref(ref: str, source: str) -> tuple[Path, str]:
    as_path = Path(ref)
    if as_path.is_file():
        return as_path, _sniff_source(as_path)
    found: list[tuple[Path, str]] = []
    if source in ("auto", "claude"):
        p = claude.find_session(ref)
        if p:
            found.append((p, "claude"))
    if source in ("auto", "codex"):
        p = codex.find_session(ref)
        if p:
            found.append((p, "codex"))
    if source in ("auto", "cursor"):
        p = cursor.find_session(ref)
        if p:
            found.append((p, "cursor"))
    if not found:
        raise click.ClickException(
            f"No session matching {ref!r} found"
            + ("" if source == "auto" else f" in source {source!r}")
            + ". Try `xexport list`."
        )
    if len(found) > 1:
        click.echo(f"Note: {ref!r} matches sessions in multiple stores; using "
                   f"{found[0][1]}. Pass --source to override.", err=True)
    return found[0]


def _hook_payload() -> dict:
    """Claude Code hook JSON on stdin. Anything unexpected degrades to {}."""
    if sys.stdin is None:
        return {}
    try:
        if sys.stdin.isatty():
            return {}
        raw = sys.stdin.read()
    except (OSError, ValueError):
        return {}
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return obj if isinstance(obj, dict) else {}


# ========================================================================= export
def _md_document(session: Session, opt: Options, *, run: int) -> str:
    body = render_markdown(session, **opt.md_kwargs)
    marker = cursors.marker_line(cursors.make(
        session, messages=len(session.messages), run=run,
        opts=cursors.options_fingerprint(**opt.md_kwargs),
    ))
    return f"{body}\n{marker}\n"


def _export_md(session: Session, opt: Options, name: str, md_dir: Path) -> tuple[Path, str, str]:
    """Write / append / replace the Markdown export.

    Returns (path, verb, detail) with verb in {"wrote", "appended", "uptodate"}.
    """
    total = len(session.messages)
    opts = cursors.options_fingerprint(**opt.md_kwargs)
    existing = None

    if opt.mode in ("append", "replace"):
        existing = cursors.find_md_export(md_dir, session, opts)
        if existing is None:
            _say_once(opt, "no-previous",
                      f"Note: no previous export found for session "
                      f"{session.session_id}; created a new one.")

    if existing is not None:
        path, data = existing
        verdict, detail = cursors.validate(data, session, opts=opts)
        if verdict in ("mismatch", "unsupported"):
            _warn(f"Warning: the previous export {path.name} cannot be extended — "
                  f"{detail}. Writing a fresh export instead.")
            existing = None
        elif opt.mode == "replace":
            path.write_text(_md_document(session, opt, run=int(data.get("run") or 1) + 1),
                            encoding="utf-8")
            return path, "wrote", ""
        elif verdict == "uptodate":
            when = str(data.get("updated", ""))[:16].replace("T", " ")
            return path, "uptodate", f"no new turns since {when} ({total} messages)"
        else:
            start = int(data["messages"])
            run = int(data.get("run") or 1) + 1
            body = render_markdown(
                session, start_index=start, header=False, **opt.md_kwargs
            )
            if not body.strip():
                # Filters can render a delta to nothing (--brief over a run of pure
                # tool traffic). Advancing the cursor over it would skip those turns
                # permanently, so leave the cursor where it is and write nothing.
                return path, "uptodate", (
                    f"{describe_delta(session, start, total)}, but nothing to write "
                    f"under the current filters"
                )
            chunk = ""
            if not opt.seamless:
                chunk += addendum_header(
                    session, run=run, start_index=start, end_index=total,
                    previous_title=str(data.get("title") or ""),
                )
            chunk += body
            chunk += "\n" + cursors.marker_line(cursors.make(
                session, messages=total, run=run, opts=opts)) + "\n"
            # No `newline=` here or in write_text: both use the platform default, so an
            # appended chunk keeps the same line endings as the rest of the file.
            with open(path, "a", encoding="utf-8") as f:
                f.write(chunk)
            return path, "appended", (
                f"{describe_delta(session, start, total)}, addendum {run}"
            )

    md_dir.mkdir(parents=True, exist_ok=True)
    path = unique_path(md_dir, name, ".md")
    if opt.mode != "new" and path.name != f"{name}.md":
        # A collision here means an export of this session already existed but could
        # not be read or extended. That is a fork, and a fork must be visible even
        # under --quiet, which the hook recipes use.
        _warn(f"Warning: {name}.md already exists but could not be extended; "
              f"wrote {path.name} instead.")
    path.write_text(_md_document(session, opt, run=1), encoding="utf-8")
    return path, "wrote", ""


def _export_html(session: Session, opt: Options, name: str, html_dir: Path) -> tuple[Path, str, str]:
    """Write / regenerate the paginated HTML export.

    HTML output is derived wholly from the session and is index-linked across pages, so
    "append" means "re-render this folder in place" — the same bytes an incremental
    append would have produced, without the partial-write failure mode.
    """
    total = len(session.messages)
    existing = None
    run = 1

    if opt.mode in ("append", "replace"):
        existing = cursors.find_html_export(html_dir, session,
                                            opt.html_opts_fingerprint)
        if existing is None:
            _say_once(opt, "no-previous",
                      f"Note: no previous export found for session "
                      f"{session.session_id}; created a new one.")

    if existing is not None:
        folder, data = existing
        verdict, detail = cursors.validate(data, session,
                                           opts=opt.html_opts_fingerprint)
        if verdict in ("mismatch", "unsupported"):
            # Same guard as the Markdown path. Without it a mismatch re-rendered the
            # folder in place, overwriting index.html and deleting the now-surplus
            # page-NNN.html files — destroying an export of a longer transcript with
            # no warning and exit 0.
            _warn(f"Warning: the previous export {folder.name} cannot be extended — "
                  f"{detail}. Writing a fresh export instead.")
            existing = None
        elif opt.mode == "append" and verdict == "uptodate":
            when = str(data.get("updated", ""))[:16].replace("T", " ")
            return folder / "index.html", "uptodate", (
                f"no new turns since {when} ({total} messages)"
            )
        else:
            run = int(data.get("run") or 1) + 1
            index_path = render_html(session, folder, **opt.html_kwargs)
            cursors.write_html_marker(folder, cursors.make(
                session, messages=total, run=run,
                opts=opt.html_opts_fingerprint))
            return index_path, "wrote", ""

    html_dir.mkdir(parents=True, exist_ok=True)
    folder = unique_path(html_dir, name)
    if opt.mode != "new" and folder.name != name:
        _warn(f"Warning: {name}\\ already exists but could not be extended; "
              f"wrote {folder.name}\\ instead.")
    index_path = render_html(session, folder, **opt.html_kwargs)
    cursors.write_html_marker(folder, cursors.make(
        session, messages=total, run=run, opts=opt.html_opts_fingerprint))
    return index_path, "wrote", ""


def _path_budget(html_dir: Path, fallback: int) -> int:
    """Longest export name that keeps the deepest generated file inside MAX_PATH.

    The deepest thing an export writes is `<html_dir>\\<name>\\page-NNN.html`, so the
    budget depends on how deep the project already sits — a fixed cap is either too
    tight for a shallow root or too loose for a deep one. Full session ids make this
    worth computing rather than guessing.
    """
    try:
        base = len(str(html_dir.resolve())) + 1          # + the separator before <name>
    except OSError:
        return fallback
    budget = 260 - base - len("\\page-001.html") - 1
    return max(40, min(fallback, budget))


def _export(session: Session, opt: Options) -> None:
    callsign = naming.resolve_callsign(opt.callsign, session=session)

    base = Path(opt.out) if opt.out else Path.cwd() / ".chatexports"
    if session.is_subagent and opt.subagent_subdir:
        base = base / opt.subagent_subdir
    md_dir = base
    html_dir = base / opt.html_subdir if opt.html_subdir else base
    md_dir.mkdir(parents=True, exist_ok=True)

    budget = _path_budget(html_dir, opt.max_name)
    try:
        name = naming.build_name(
            session, title=opt.name, callsign=callsign,
            template=opt.name_template, max_name=budget,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    if len(name) > budget:
        # The identity suffix is never trimmed — it is what makes an export traceable
        # and findable — so a budget smaller than it is simply unsatisfiable. Say so
        # rather than quietly emitting a path Windows may refuse to create.
        _warn(
            f"Warning: this export's name is {len(name)} characters but only {budget} "
            f"fit under {html_dir}. The session id is never shortened; use a shallower "
            f'--out, or set XEXPORT_NAME_TEMPLATE="{{agent}} -- {{title}} -- {{id8}}" '
            f"for compact names."
        )

    results: list[tuple[Path, str, str]] = []
    index_path: Path | None = None

    # One lock for the whole export directory: read-cursor-then-append has to be
    # atomic against a concurrent xexport, and --format both writes twice.
    with _export_lock(base):
        if opt.fmt in ("html", "both"):
            result = _export_html(session, opt, name, html_dir)
            results.append(result)
            index_path = result[0]
            if opt.copy_json and session.path and result[1] != "uptodate":
                shutil.copy2(session.path, result[0].parent / session.path.name)

        if opt.fmt in ("md", "both"):
            result = _export_md(session, opt, name, md_dir)
            results.append(result)
            if opt.copy_json and session.path and result[1] != "uptodate":
                shutil.copy2(session.path, result[0].with_suffix(".jsonl"))

    _report(session, opt, results)

    if opt.open_after and index_path:
        webbrowser.open(index_path.resolve().as_uri())


def _report(session: Session, opt: Options, results: list[tuple[Path, str, str]]) -> None:
    if opt.quiet:
        return
    verbs = {verb for _, verb, _ in results}
    if verbs == {"uptodate"}:
        path, _, detail = results[0]
        click.echo(f"Up to date: {detail}.")
        click.echo(f"  file:    {path}")
        return

    headline = "Appended" if "appended" in verbs else "Exported"
    click.echo(f"{headline}: {session.title}")
    click.echo(f"  source:  {session.app or session.source}"
               f" · session {session.session_id}")
    for path, verb, detail in results:
        if verb == "uptodate":
            click.echo(f"  current: {path}")
            continue
        if verb == "appended":
            click.echo(f"  added:   {detail}")
        click.echo(f"  wrote:   {path}")


# ======================================================================== options
def common_options(f):
    f = click.option("--source", type=click.Choice(_SOURCE_CHOICES),
                     default="auto", show_default=True,
                     help="Which session store to use.")(f)
    f = click.option("--format", "fmt",
                     type=click.Choice(["html", "md", "both"]),
                     default="html", show_default=True,
                     help="Output format.")(f)
    f = click.option("--out", type=click.Path(file_okay=False),
                     default=None, help=r"Output directory "
                     r"[default: .\.chatexports]")(f)
    f = click.option("--name", default=None,
                     help="Override the {title} part of the export name.")(f)
    f = click.option("--mode", type=click.Choice(_MODE_CHOICES),
                     default="append", show_default=True,
                     help="append (default): add only the new turns to this session's "
                          "existing export, creating it if there is none. "
                          "new: always a fresh export. replace: overwrite it.")(f)
    f = click.option("--append", is_flag=True,
                     help="Shorthand for --mode append.")(f)
    f = click.option("--seamless", is_flag=True,
                     help="In --mode append, omit the '➕ Addendum N' header.")(f)
    f = click.option("--callsign", default=None,
                     help="AgentNamer callsign for the {callsign} name field, or "
                          "'auto' to discover it [env: XEXPORT_CALLSIGN].")(f)
    f = click.option("--name-template", default=None,
                     help="Name template, e.g. '{agent} -- {title} -- {identity}' "
                          "[env: XEXPORT_NAME_TEMPLATE].")(f)
    f = click.option("--html-subdir", default="html", show_default=True,
                     help='Subfolder for HTML exports; "" writes them flat.')(f)
    f = click.option("--subagent-subdir", default="", show_default=True,
                     help='Subfolder for subagent exports; "" (default) writes '
                          'them beside the main exports.')(f)
    f = click.option("--max-name", default=naming.MAX_NAME, show_default=True,
                     help="Upper bound on the assembled name; the real cap is also "
                          "derived from the output path so page-NNN.html stays "
                          "inside MAX_PATH. Only {title} shrinks.")(f)
    f = click.option("--brief", is_flag=True,
                     help="User + assistant text only (no tools/thinking).")(f)
    f = click.option("--no-tools", is_flag=True, help="Skip tool calls/results.")(f)
    f = click.option("--no-thinking", is_flag=True, help="Skip thinking blocks.")(f)
    f = click.option("--full", is_flag=True,
                     help="Disable truncation of long tool output (Markdown).")(f)
    f = click.option("--json", "copy_json", is_flag=True,
                     help="Copy the raw .jsonl next to the output.")(f)
    f = click.option("--open", "open_after", is_flag=True,
                     help="Open index.html when done.")(f)
    f = click.option("--quiet", is_flag=True,
                     help="Suppress normal output (warnings and errors still show).")(f)
    return f


class ExportFallbackGroup(click.Group):
    """`xexport <session-id-or-path>` falls through to the export command."""

    def resolve_command(self, ctx, args):
        try:
            return super().resolve_command(ctx, args)
        except click.UsageError:
            cmd = self.get_command(ctx, "export")
            return "export", cmd, args


@click.group(cls=ExportFallbackGroup, invoke_without_command=True)
@click.version_option(__version__)
@click.option("--limit", default=15, show_default=True,
              help="How many sessions the picker shows.")
@common_options
@click.pass_context
def main(ctx, limit, source, **kw):
    """Export Claude Code / Codex / Cursor chat sessions to HTML or Markdown.

    Run with no arguments for an interactive picker, or see:
    xexport current / xexport list / xexport subagents / xexport <session-id>
    """
    _utf8_stdout()
    if ctx.invoked_subcommand is not None:
        return
    infos = _gather(source, limit)
    if not infos:
        raise click.ClickException("No sessions found.")
    _print_table(infos)
    choice = click.prompt("Export which session?", type=click.IntRange(1, len(infos)))
    info = infos[choice - 1]
    _export(_parse(info.path, info.source), _options(kw))


def _gather(source: str, limit: int, *, subagents: bool = False):
    infos = []
    if source in ("auto", "claude"):
        infos.extend(claude.list_sessions(limit, subagents=subagents))
    if source in ("auto", "codex"):
        infos.extend(codex.list_sessions(limit))
    if source in ("auto", "cursor"):
        infos.extend(cursor.list_sessions(limit, subagents=subagents))
    infos.sort(key=lambda i: i.mtime, reverse=True)
    return infos[:limit]


def _print_table(infos) -> None:
    for i, info in enumerate(infos, start=1):
        when = datetime.fromtimestamp(info.mtime).strftime("%m-%d %H:%M")
        title = info.title.replace("\n", " ")
        if len(title) > 60:
            title = title[:60] + "…"
        click.echo(f"{i:>3}  {info.source:<7} {when}  {title}")
        click.echo(f"     id: {info.session_id}")


@main.command("list")
@click.option("--source", type=click.Choice(_SOURCE_CHOICES),
              default="auto", show_default=True)
@click.option("--limit", default=15, show_default=True)
@click.option("--subagents", is_flag=True,
              help="Include subagent transcripts in the listing.")
def list_cmd(source, limit, subagents):
    """List recent sessions across supported stores."""
    _utf8_stdout()
    infos = _gather(source, limit, subagents=subagents)
    if not infos:
        raise click.ClickException("No sessions found.")
    _print_table(infos)


@main.command()
@click.option("--session-id", default=None,
              help="Exact session id. Use this to select a specific session.")
@click.option("--from-hook", is_flag=True,
              help="Read Claude Code hook JSON on stdin and use its transcript_path "
                   "or session_id.")
@common_options
def current(session_id, from_hook, source, **kw):
    """Export the session you are currently inside."""
    _utf8_stdout()
    opt = _options(kw)
    cwd = Path.cwd()

    if from_hook:
        payload = _hook_payload()
        hook_path = str(payload.get("transcript_path") or "").strip()
        if hook_path and Path(hook_path).is_file():
            path = Path(hook_path)
            _export(_parse(path, _sniff_source(path)), opt)
            return
        session_id = session_id or str(payload.get("session_id") or "").strip() or None

    if session_id:
        path, found_source = _resolve_ref(session_id, source)
    elif source in ("auto", "cursor") and (conv_id := os.environ.get(
            "CURSOR_CONVERSATION_ID", "").strip()):
        # Cursor agent shells expose the active conversation id.
        path, found_source = _resolve_ref(conv_id, "cursor")
    elif source in ("auto", "codex") and (thread_id := os.environ.get(
            "CODEX_THREAD_ID", "").strip()):
        # Codex Desktop exposes the active task id. Prefer it over the shared
        # workspace heuristic so concurrent tasks cannot export each other.
        path, found_source = _resolve_ref(thread_id, "codex")
    else:
        path, found_source = _detect_current(cwd, source)
    _export(_parse(path, found_source), opt)


def _detect_current(cwd: Path, source: str) -> tuple[Path, str]:
    candidates: list[tuple[Path, str, bool]] = []
    if source in ("auto", "claude"):
        p, confirmed = detect.detect_claude(cwd)
        if p:
            candidates.append((p, "claude", confirmed))
    if source in ("auto", "codex"):
        p, matched = detect.detect_codex(cwd)
        if p:
            candidates.append((p, "codex", matched))
    if source in ("auto", "cursor"):
        p, matched = detect.detect_cursor(cwd)
        if p:
            candidates.append((p, "cursor", matched))
    if not candidates:
        raise click.ClickException(
            "Could not find a session for this directory. "
            "Try `xexport list` and export by id."
        )
    confirmed = [c for c in candidates if c[2]]
    pool = confirmed or candidates
    pool.sort(key=lambda c: c[0].stat().st_mtime, reverse=True)
    path, found_source, was_confirmed = pool[0]
    if not was_confirmed:
        click.echo(f"Note: could not identify the active session exactly; "
                   f"using the newest {found_source} session: {path.name}. "
                   "Pass --session-id to choose explicitly.",
                   err=True)
    return path, found_source


@main.command("subagents")
@click.option("--session-id", default=None,
              help="Parent session id [default: the current session].")
@click.option("--from-hook", is_flag=True,
              help="Read Claude Code hook JSON on stdin (SubagentStop).")
@common_options
def subagents_cmd(session_id, from_hook, source, **kw):
    """Export every subagent transcript belonging to one parent session.

    A Claude Code subagent shares its parent's session id, so it cannot export itself
    through `current` — the parent, or a SubagentStop hook, drives it from outside.
    With --mode append this is idempotent and near-free to re-run.
    """
    _utf8_stdout()
    opt = _options(kw)

    if from_hook:
        payload = _hook_payload()
        hook_path = str(payload.get("transcript_path") or "").strip()
        # Only honour a hook path that really is a subagent transcript. A SubagentStop
        # payload may carry the MAIN transcript, and exporting that here would write
        # the parent into the same file the Stop hook maintains while reporting that
        # no subagents were found — the failure mode is completely silent.
        if hook_path and Path(hook_path).is_file() and claude.is_subagent_path(Path(hook_path)):
            path = Path(hook_path)
            _export(_parse(path, _sniff_source(path)), opt)
            return
        session_id = session_id or str(payload.get("session_id") or "").strip() or None
        if hook_path and not session_id:
            # …/<project>/<parent-session-id>/subagents/… or …/<parent>.jsonl
            session_id = Path(hook_path).stem

    if not session_id:
        session_id = (os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
                      or os.environ.get("CURSOR_CONVERSATION_ID", "").strip())
    if not session_id:
        path, found_source = _detect_current(Path.cwd(), source)
        session_id = path.stem

    infos = []
    if source in ("auto", "claude"):
        infos.extend(claude.list_subagents(session_id))
    if source in ("auto", "cursor"):
        infos.extend(cursor.list_subagents(session_id))
    if not infos:
        _say(opt, f"No subagent transcripts found for session {session_id}.")
        return

    for info in infos:
        _export(_parse(info.path, info.source), opt)


@main.command()
@click.argument("ref")
@common_options
def export(ref, source, **kw):
    """Export a session by id or by path to a .jsonl file."""
    _utf8_stdout()
    path, found_source = _resolve_ref(ref, source)
    _export(_parse(path, found_source), _options(kw))


if __name__ == "__main__":
    main()
