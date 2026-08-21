"""xexport command-line interface."""

from __future__ import annotations

import json
import os
import shutil
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

import click

from . import __version__, detect
from .model import Session
from .render.html import render_html
from .render.markdown import render_markdown
from .sources import claude, codex, cursor
from .titles import sanitize_title, unique_path

_SOURCE_CHOICES = ["auto", "claude", "codex", "cursor"]


def _utf8_stdout() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError, OSError):
            pass


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


def _export(session: Session, *, fmt: str, out: str | None, name: str | None,
            brief: bool, no_tools: bool, no_thinking: bool, full: bool,
            copy_json: bool, open_after: bool) -> None:
    title = sanitize_title(name or session.title)
    out_dir = Path(out) if out else Path.cwd() / ".chatexports"
    out_dir.mkdir(parents=True, exist_ok=True)

    md_kwargs = dict(
        brief=brief,
        include_tools=not no_tools,
        include_thinking=not no_thinking,
        truncate=0 if full else 2000,
    )

    written: list[Path] = []
    index_path: Path | None = None

    if fmt in ("html", "both"):
        folder = unique_path(out_dir, title)
        index_path = render_html(session, folder)
        written.append(index_path)
        if fmt == "both":
            md_path = folder / f"{title}.md"
            md_path.write_text(render_markdown(session, **md_kwargs),
                               encoding="utf-8")
            written.append(md_path)
        if copy_json and session.path:
            shutil.copy2(session.path, folder / session.path.name)
    elif fmt == "md":
        md_path = unique_path(out_dir, title, ".md")
        md_path.write_text(render_markdown(session, **md_kwargs),
                           encoding="utf-8")
        written.append(md_path)
        if copy_json and session.path:
            shutil.copy2(session.path, md_path.with_suffix(".jsonl"))

    click.echo(f"Exported: {session.title}")
    click.echo(f"  source:  {session.app or session.source}"
               f" · session {session.session_id}")
    for p in written:
        click.echo(f"  wrote:   {p}")
    if open_after and index_path:
        webbrowser.open(index_path.resolve().as_uri())


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
                     help="Override the export name (default: chat title).")(f)
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
def main(ctx, limit, source, fmt, out, name, brief, no_tools, no_thinking,
         full, copy_json, open_after):
    """Export Claude Code / Codex / Cursor chat sessions to HTML or Markdown.

    Run with no arguments for an interactive picker, or see:
    xexport current / xexport list / xexport <session-id>
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
    session = _parse(info.path, info.source)
    _export(session, fmt=fmt, out=out, name=name, brief=brief,
            no_tools=no_tools, no_thinking=no_thinking, full=full,
            copy_json=copy_json, open_after=open_after)


def _gather(source: str, limit: int):
    infos = []
    if source in ("auto", "claude"):
        infos.extend(claude.list_sessions(limit))
    if source in ("auto", "codex"):
        infos.extend(codex.list_sessions(limit))
    if source in ("auto", "cursor"):
        infos.extend(cursor.list_sessions(limit))
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
def list_cmd(source, limit):
    """List recent sessions across supported stores."""
    _utf8_stdout()
    infos = _gather(source, limit)
    if not infos:
        raise click.ClickException("No sessions found.")
    _print_table(infos)


@main.command()
@click.option("--session-id", default=None,
              help="Exact session id. Use this to select a specific session.")
@common_options
def current(session_id, source, fmt, out, name, brief, no_tools, no_thinking,
            full, copy_json, open_after):
    """Export the session you are currently inside."""
    _utf8_stdout()
    cwd = Path.cwd()
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
    session = _parse(path, found_source)
    _export(session, fmt=fmt, out=out, name=name, brief=brief,
            no_tools=no_tools, no_thinking=no_thinking, full=full,
            copy_json=copy_json, open_after=open_after)


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


@main.command()
@click.argument("ref")
@common_options
def export(ref, source, fmt, out, name, brief, no_tools, no_thinking, full,
           copy_json, open_after):
    """Export a session by id or by path to a .jsonl file."""
    _utf8_stdout()
    path, found_source = _resolve_ref(ref, source)
    session = _parse(path, found_source)
    _export(session, fmt=fmt, out=out, name=name, brief=brief,
            no_tools=no_tools, no_thinking=no_thinking, full=full,
            copy_json=copy_json, open_after=open_after)


if __name__ == "__main__":
    main()
