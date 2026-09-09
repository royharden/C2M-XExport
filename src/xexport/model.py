"""Neutral intermediate representation shared by all sources and renderers.

Parsers (sources/) turn app-specific jsonl into this model; renderers (render/)
only ever see this model. A format drift in one app touches one parser, and both
sources get identical output styling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

# Block kinds
USER_TEXT = "user_text"
ASSISTANT_TEXT = "assistant_text"
THINKING = "thinking"
TOOL_CALL = "tool_call"
TOOL_RESULT = "tool_result"
RAW = "raw"  # unknown entry, preserved as pretty-printed JSON, rendered collapsed


@dataclass
class Block:
    kind: str
    text: str = ""      # user/assistant/thinking text, or raw JSON for RAW blocks
    name: str = ""      # tool name (TOOL_CALL)
    detail: str = ""    # short human hint, e.g. Bash description or file path
    args: str = ""      # pretty-printed tool arguments (TOOL_CALL)
    output: str = ""    # tool output (TOOL_RESULT)
    is_error: bool = False


@dataclass
class Message:
    role: str  # "user" | "assistant" | "tool"
    blocks: list[Block] = field(default_factory=list)
    timestamp: str = ""  # ISO-8601 when the source records one


@dataclass
class Session:
    source: str  # "claude" | "codex" | "cursor"
    session_id: str
    path: Path | None = None
    title: str = ""
    cwd: str = ""
    started: str = ""
    model: str = ""
    app: str = ""  # e.g. "Claude Code", "Codex Desktop", "Codex CLI", "Cursor"
    is_subagent: bool = False
    parent_session_id: str = ""   # the session that spawned this subagent
    messages: list[Message] = field(default_factory=list)

    @property
    def assistant_label(self) -> str:
        return {
            "claude": "Claude",
            "codex": "Codex",
            "cursor": "Cursor",
        }.get(self.source, self.source.title() or "Assistant")

    def is_prompt(self, m: Message) -> bool:
        return m.role == "user" and any(b.kind == USER_TEXT for b in m.blocks)

    def prompt_groups(self) -> list[list[Message]]:
        """Split messages into groups, each starting at a real user prompt.

        Tool-result messages carry role "tool", so they never open a group.
        Anything before the first prompt (rare) is folded into the first group.
        """
        groups: list[list[Message]] = []
        current: list[Message] = []
        for m in self.messages:
            if self.is_prompt(m) and current and any(self.is_prompt(x) for x in current):
                groups.append(current)
                current = []
            current.append(m)
        if current:
            groups.append(current)
        return groups

    def stats(self) -> dict[str, int]:
        prompts = sum(1 for m in self.messages if self.is_prompt(m))
        tool_calls = sum(
            1 for m in self.messages for b in m.blocks if b.kind == TOOL_CALL
        )
        return {
            "prompts": prompts,
            "messages": len(self.messages),
            "tool_calls": tool_calls,
        }
