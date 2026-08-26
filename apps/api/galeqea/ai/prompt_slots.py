"""Ask-then-use: let the chat request one missing input and consume the reply.

The agent often needs a single fact before it can act — most commonly *which URL
to test*. Rather than fail with "I need a URL", it asks, remembers what it was
waiting for on the conversation, and treats the user's next message as the answer.

This is the conversational form of **slot filling**: name the one thing that is
missing, ask only for that, keep the rest of the request intact, and let a direct
answer fill the slot. It is the same shape as :mod:`plan_gate` (a small piece of
state on ``ChatSession.context`` that changes how the *next* message is read), and
like it, it needs no model — so the on-ramp works in No-AI mode.

A pending prompt is single-turn: the next message either fills it or replaces it.
A message that is clearly a change of subject (or an explicit cancel) drops the
slot rather than being force-fit into it, so a user is never trapped in a question
they've moved on from.
"""

from __future__ import annotations

import re

#: Where a pending prompt lives on ``ChatSession.context``.
PROMPT_KEY = "pending_prompt"

_CANCEL = re.compile(
    r"^\s*(stop|cancel|never ?mind|forget it|no(?:pe)?|don'?t|skip)\b", re.IGNORECASE
)


def set_prompt(session, *, slot: str, question: str, data: dict | None = None) -> None:
    """Remember that the chat is waiting on one input named ``slot``."""
    context = dict(session.context or {})
    context[PROMPT_KEY] = {"slot": slot, "question": question, "data": data or {}}
    session.context = context


def pending_prompt(session) -> dict | None:
    return (session.context or {}).get(PROMPT_KEY)


def clear_prompt(session) -> None:
    if session.context and PROMPT_KEY in session.context:
        context = dict(session.context)
        context.pop(PROMPT_KEY, None)
        session.context = context


def is_cancel(text: str) -> bool:
    return bool(_CANCEL.match(text or ""))
