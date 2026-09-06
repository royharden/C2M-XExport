"""Shared render helpers.

One content predicate for both renderers. `--brief`, `--no-tools` and
`--no-thinking` are privacy controls, not Markdown styling: a user who passes
`--brief` to keep credentials and tool I/O out of an export must get that in
every format the run produces. Filtering here, on the neutral model, keeps the
two renderers from drifting apart on what "excluded" means.
"""

from __future__ import annotations

import copy

from ..model import (
    ASSISTANT_TEXT, RAW, THINKING, TOOL_CALL, TOOL_RESULT, USER_TEXT,
    Message, Session,
)

_ALWAYS = {USER_TEXT, ASSISTANT_TEXT}
_THINKING_KINDS = {THINKING}
_TOOL_KINDS = {TOOL_CALL, TOOL_RESULT, RAW}


def keeps_everything(*, brief: bool = False, include_tools: bool = True,
                     include_thinking: bool = True) -> bool:
    """True when filtering would be a no-op, so callers can skip the copy."""
    return not brief and include_tools and include_thinking


def filtered(session: Session, *, brief: bool = False, include_tools: bool = True,
             include_thinking: bool = True) -> Session:
    """A copy of `session` carrying only the block kinds the caller allows.

    The message list keeps its length and order. A message can end up with no
    blocks at all (a tool message under --no-tools); it is left in place rather
    than dropped so that anything counting messages still agrees with the
    unfiltered transcript. Renderers skip empty messages at render time.
    """
    if brief:
        include_tools = False
        include_thinking = False
    if keeps_everything(include_tools=include_tools,
                        include_thinking=include_thinking):
        return session

    allowed = set(_ALWAYS)
    if include_thinking:
        allowed |= _THINKING_KINDS
    if include_tools:
        allowed |= _TOOL_KINDS

    clone = copy.copy(session)
    clone.messages = [
        Message(role=m.role,
                blocks=[b for b in m.blocks if b.kind in allowed],
                timestamp=m.timestamp)
        for m in session.messages
    ]
    return clone
