"""Autonomous exploratory testing: the Plan-Act-Verify loop.

Exploration answers a different question from a test. A test asks *"does this
still do what we agreed?"*; exploration asks *"what does this do that we never
agreed about?"* — so its output is findings a human triages, not a pass or fail.

Two strategies, both driving the same loop:

* **Deterministic** (the default, no model). A systematic traversal: prefer
  controls never touched, prefer actions that reach unseen screens, fill forms
  with boundary values, and back out of dead ends. It cannot judge whether a
  message is *confusing*, but it finds broken links, console errors, 5xx
  responses, unlabelled controls, dead ends and lost form data — and it finds
  them the same way every time, which a model cannot promise.

* **Model** (when configured). The same loop, but the next action is chosen by
  a model reading the accessibility snapshot, and it can additionally judge
  whether what happened would surprise or mislead a user.

Both are bounded by a step budget and both refuse destructive actions outright:
the explorer is pointed at a real application, and "explore freely" must never
mean "delete things".
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

from ...core.safety import wrap_untrusted
from ..prompts import system_prompt
from ..providers.base import LLMProvider, Message, NoAIModeError, ProviderError, Role

#: Two tiers, because "destructive" covers two different risks.
#:
#: DESTRUCTIVE is data loss - never clicked, in any environment. A false
#: positive costs one unexplored button; a false negative costs someone's data.
DESTRUCTIVE = re.compile(
    r"\b(delete|remove|destroy|erase|wipe|purge|drop|revoke|deactivate|"
    r"close account|cancel subscription|unsubscribe|sign out|log ?out)\b",
    re.IGNORECASE,
)

#: TRANSACTIONAL commits something real - a payment, an order, a transfer.
#: Blocked by default, but allowed per session, because on a staging
#: environment the submit button is precisely where the interesting behaviour
#: lives. Refusing it there means exploration can only ever see the form.
TRANSACTIONAL = re.compile(
    r"\b(pay|purchase|buy|place order|confirm payment|submit payment|"
    r"charge|withdraw|transfer|checkout|complete order|book now)\b",
    re.IGNORECASE,
)

#: Not part of the application under test.
NON_APP_URL = re.compile(r"^(about:|chrome-error://|data:|$)", re.IGNORECASE)

#: Values chosen to probe boundaries rather than to succeed.
PROBE_VALUES = {
    "email": ["not-an-email", "a@b.co", "  spaced@example.com  "],
    "password": ["x", "correct horse battery staple"],
    "number": ["-1", "0", "999999999"],
    "tel": ["abc", "+441234567890"],
    "url": ["notaurl", "https://example.com"],
    "search": ["", "'; DROP TABLE--", "ünïcödé ✓"],
    "text": ["", "x", "A" * 300],
}


@dataclass(slots=True)
class Decision:
    action: str                       # click | fill | goto | back | finish
    target_index: int | None = None
    value: str = ""
    url: str = ""
    rationale: str = ""

    def as_response(self, request_id: str) -> dict:
        return {
            "requestId": request_id,
            "ok": True,
            "action": self.action,
            "targetIndex": self.target_index,
            "value": self.value,
            "url": self.url,
            "rationale": self.rationale,
        }


@dataclass(slots=True)
class ExplorerState:
    """What the explorer has already done. Kept server-side, not in the browser."""

    charter: str = ""
    base_url: str = ""
    #: Whether this session may commit a transaction (see TRANSACTIONAL).
    allow_transactional: bool = False
    visited_routes: set[str] = field(default_factory=set)
    touched: set[str] = field(default_factory=set)
    trail: list[dict] = field(default_factory=list)
    consecutive_no_progress: int = 0
    consecutive_backs: int = 0
    #: Controls deliberately not clicked, so the report can say what was skipped
    #: rather than silently leaving a hole in the coverage.
    skipped: list[dict] = field(default_factory=list)
    #: Findings filed for the first time vs. ones already on the list. Without
    #: the distinction, a session that re-confirms six known defects reports
    #: "nothing worth reporting", which is the opposite of what happened.
    new_findings: int = 0
    recurring_findings: int = 0

    def remember(self, decision: Decision, observation: dict, label: str) -> None:
        self.trail.append({
            "step": len(self.trail),
            "action": decision.action,
            "target": label,
            "value": decision.value,
            "url": observation.get("url", ""),
            "rationale": decision.rationale,
        })


# --------------------------------------------------------------------------- #
def element_key(candidate: dict) -> str:
    """Identity of a control across page loads, for 'have I tried this?'."""
    basis = "|".join([
        candidate.get("role", ""),
        (candidate.get("name") or "")[:60],
        candidate.get("testId") or "",
    ])
    return hashlib.blake2b(basis.encode(), digest_size=8).hexdigest()


def forbidden_reason(candidate: dict, *, allow_transactional: bool) -> str | None:
    """Why this control must not be clicked, or None if it is safe."""
    text = " ".join(filter(None, [candidate.get("name"), candidate.get("text")]))
    if DESTRUCTIVE.search(text):
        return "destructive"
    if not allow_transactional and TRANSACTIONAL.search(text):
        return "transactional"
    return None


def is_destructive(candidate: dict, *, allow_transactional: bool = False) -> bool:
    return forbidden_reason(candidate, allow_transactional=allow_transactional) is not None


def decide_deterministic(observation: dict, state: ExplorerState) -> Decision:
    """Systematic traversal. Same input, same choice, every time."""
    candidates: list[dict] = observation.get("candidates") or []
    url = observation.get("url", "")
    route = observation.get("route") or url

    # Going back past the entry page lands on about:blank, which has nothing to
    # click, which looks like a dead end, which suggests going back. Left alone
    # that loop consumes the entire step budget - it burned 15 of 18 steps the
    # first time this ran. Recovery is to return to the application.
    if NON_APP_URL.match(url or ""):
        state.consecutive_backs = 0
        return Decision(
            "goto", url=state.base_url,
            rationale="left the application — returning to the start",
        )

    state.visited_routes.add(route)

    if not candidates:
        return _back_or_restart(state, "nothing interactive here")

    usable = [c for c in candidates if not c.get("disabled")]
    safe: list[dict] = []
    for candidate in usable:
        reason = forbidden_reason(candidate, allow_transactional=state.allow_transactional)
        if reason is None:
            safe.append(candidate)
        else:
            entry = {"label": _label(candidate), "reason": reason}
            if entry not in state.skipped:
                state.skipped.append(entry)

    if not safe:
        return _back_or_restart(state, "every remaining control is guarded")

    untouched = [c for c in safe if element_key(c) not in state.touched]
    pool = untouched or safe

    state.consecutive_backs = 0

    # 1. Empty inputs first: a form nobody filled tells you nothing.
    inputs = [c for c in pool if c.get("role") in {"textbox", "combobox"} and not c.get("value")]
    if inputs:
        target = inputs[0]
        return Decision(
            "fill",
            target_index=candidates.index(target),
            value=_probe_value(target, state),
            rationale=f"probing '{_label(target)}' with a boundary value",
        )

    # 2. Then links, which are the cheapest way to reach an unseen screen.
    links = [c for c in pool if c.get("role") == "link" and _leads_somewhere_new(c, state)]
    if links:
        target = links[0]
        return Decision("click", target_index=candidates.index(target),
                        rationale=f"following '{_label(target)}' toward an unvisited screen")

    # 3. Then any untouched control.
    if untouched:
        target = untouched[0]
        return Decision("click", target_index=candidates.index(target),
                        rationale=f"exercising '{_label(target)}', not yet tried")

    # 4. Everything here has been tried.
    state.consecutive_no_progress += 1
    if state.consecutive_no_progress >= 2:
        return Decision("finish", rationale="this area is fully explored")
    return _back_or_restart(state, "nothing new on this screen")


def _back_or_restart(state: ExplorerState, why: str) -> Decision:
    """Back out, but never more than twice in a row without regrouping."""
    state.consecutive_backs += 1
    if state.consecutive_backs >= 3:
        state.consecutive_backs = 0
        if state.base_url:
            return Decision(
                "goto", url=state.base_url,
                rationale=f"{why} — backing out repeatedly got nowhere, restarting from the top",
            )
        return Decision("finish", rationale=f"{why} — and nowhere left to go")
    return Decision("back", rationale=f"{why} — going back")


async def decide_with_model(
    provider: LLMProvider, observation: dict, state: ExplorerState
) -> Decision:
    """Let a model choose, constrained to an index in a server-supplied list.

    The model cannot invent a selector or a URL - it picks from candidates the
    runner actually found. That bounds the blast radius of both a hallucination
    and any instruction hidden in the page.
    """
    candidates: list[dict] = observation.get("candidates") or []
    listing = "\n".join(
        f"[{i}] {c.get('role')} \"{(c.get('name') or '')[:60]}\""
        + (f" value={c.get('value')!r}" if c.get("value") else "")
        + (f" ({forbidden_reason(c, allow_transactional=state.allow_transactional).upper()} - forbidden)"
           if is_destructive(c, allow_transactional=state.allow_transactional) else "")
        + (" (already tried)" if element_key(c) in state.touched else "")
        for i, c in enumerate(candidates)
    )
    history = "\n".join(
        f"  {t['step']}. {t['action']} {t['target']} -> {t['url']}" for t in state.trail[-8:]
    ) or "  (nothing yet)"

    prompt = (
        f"Charter: {state.charter}\n"
        f"Current URL: {observation.get('url')}\n"
        f"Screens visited: {len(state.visited_routes)}\n\n"
        f"What you have done so far:\n{history}\n\n"
        f"Controls on this screen:\n{listing}\n\n"
        + wrap_untrusted(
            (observation.get("ariaSnapshot") or "")[:6000],
            source=observation.get("url", "page"), kind="accessibility_snapshot",
        )
        + "\n\nChoose the single next action. Prefer what advances the charter and "
        "what you have not tried. Never choose a control marked DESTRUCTIVE."
    )

    schema = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["click", "fill", "back", "finish"]},
            "index": {"type": ["integer", "null"]},
            "value": {"type": "string"},
            "rationale": {"type": "string"},
            "finding": {
                "type": ["object", "null"],
                "properties": {
                    "title": {"type": "string"},
                    "detail": {"type": "string"},
                    "severity": {"type": "string", "enum": ["low", "medium", "high"]},
                    "confidence": {"type": "number"},
                },
                "required": ["title", "detail", "severity", "confidence"],
                "additionalProperties": False,
            },
        },
        "required": ["action", "index", "value", "rationale", "finding"],
        "additionalProperties": False,
    }

    result = await provider.complete(
        [Message(role=Role.USER, content=prompt)],
        system=system_prompt("explorer"),
        max_tokens=1200,
        response_format=schema,
    )
    payload = _parse(result.text)
    if payload is None:
        return decide_deterministic(observation, state)

    index = payload.get("index")
    action = payload.get("action", "back")
    if action in {"click", "fill"}:
        if index is None or not (0 <= index < len(candidates)):
            return decide_deterministic(observation, state)
        # The refusal is enforced here, not merely requested in the prompt.
        reason = forbidden_reason(
            candidates[index], allow_transactional=state.allow_transactional
        )
        if reason:
            return Decision(
                "back",
                rationale=f"model chose a {reason} control; refused and backed out",
            )

    return Decision(
        action=action,
        target_index=index,
        value=str(payload.get("value") or ""),
        rationale=str(payload.get("rationale") or "")[:400],
    )


def model_finding(text: str) -> dict | None:
    payload = _parse(text)
    return (payload or {}).get("finding")


# --------------------------------------------------------------------------- #
def _label(candidate: dict) -> str:
    return (candidate.get("name") or candidate.get("text") or candidate.get("role") or "?")[:60]


def _probe_value(candidate: dict, state: ExplorerState) -> str:
    kind = (candidate.get("type") or "").lower()
    name = f"{candidate.get('name', '')} {candidate.get('testId', '')}".lower()
    for key in ("email", "password", "tel", "url", "search", "number"):
        if key in kind or key in name:
            options = PROBE_VALUES[key]
            return options[len(state.trail) % len(options)]
    options = PROBE_VALUES["text"]
    return options[len(state.trail) % len(options)]


def _leads_somewhere_new(candidate: dict, state: ExplorerState) -> bool:
    href = candidate.get("href") or ""
    if not href or href.startswith(("mailto:", "tel:", "#", "javascript:")):
        return False
    return href not in state.visited_routes


def _parse(text: str) -> dict[str, Any] | None:
    body = (text or "").strip()
    if body.startswith("```"):
        body = body.split("```", 2)[1].removeprefix("json").strip().rsplit("```", 1)[0]
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return None


async def decide(
    observation: dict, state: ExplorerState, *, provider: LLMProvider | None
) -> Decision:
    """Choose the next action, degrading to the deterministic policy on failure."""
    if provider is None:
        return decide_deterministic(observation, state)
    try:
        return await decide_with_model(provider, observation, state)
    except (NoAIModeError, ProviderError):
        return decide_deterministic(observation, state)
