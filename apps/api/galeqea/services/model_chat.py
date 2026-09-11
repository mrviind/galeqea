"""Deterministic chat verb for per-role model routing (WO#8-C).

"use gpt-4o-mini for locating" routes the grounder (locating/healing) to a small model
while the planner keeps a frontier one; "use claude-opus-5 for judging" sets the judge.
"""

from __future__ import annotations

import re

from sqlalchemy.orm import Session

from ..config import settings
from ..models import Project, User

_ROLE_WORDS = {
    "locating": "grounder", "locate": "grounder", "grounding": "grounder",
    "healing": "grounder", "finding": "grounder", "locator": "grounder",
    "judging": "judge", "judge": "judge", "verdicts": "judge",
    "planning": "planner", "reasoning": "planner", "planner": "planner",
}


_CEILING_VERBS = ("cap", "limit", "ceiling", "restrict", "budget")


def _handle_ceiling(db: Session, project: Project, low: str) -> tuple[str, list[dict]] | None:
    """"cap locating at 2000 tokens" / "limit judging to 3000 tokens per call"."""
    if not any(v in low for v in _CEILING_VERBS):
        return None
    role_m = re.search(r"\b(locating|locate|grounding|healing|finding|locator|"
                       r"judging|judge|verdicts|planning|reasoning|planner)\b", low)
    num_m = re.search(r"(\d[\d,]*)\s*(?:tokens?|tok\b)", low) or re.search(r"\b(\d[\d,]{2,})\b", low)
    if not role_m or not num_m:
        return None
    bucket = _ROLE_WORDS[role_m.group(1)]
    cap = int(num_m.group(1).replace(",", ""))
    if settings.provider in (None, "", "none"):
        return ("No model provider is configured. Connect one in Settings → Model first.", [])
    from ..ai import keys
    merged = keys.set_role_ceilings(db, provider=settings.provider, project_id=project.id,
                                    role_ceilings={bucket: cap})
    db.commit()
    shown = ", ".join(f"{k}≤{v}" for k, v in merged.items()) or "none"
    return (f"Capping **{bucket}** at **{cap} tokens** per call. When a call would run "
            f"past that, GaleQEA stops and asks instead of spending. Per-role ceilings: "
            f"{shown}. Cache hits and deterministic tiers are unaffected, so they cost nothing.",
            [{"type": "role_ceiling", "role": bucket, "ceiling": cap, "role_ceilings": merged}])


def try_handle(db: Session, project: Project, text: str, user: User) -> tuple[str, list[dict]] | None:
    low = text.strip().lower()
    ceiling = _handle_ceiling(db, project, low)
    if ceiling is not None:
        return ceiling
    m = re.search(r"use\s+(.+?)\s+for\s+(locating|locate|grounding|healing|finding|locator|"
                  r"judging|judge|verdicts|planning|reasoning|planner)\b", low)
    if not m:
        return None
    model_phrase, role_word = m.group(1).strip(), m.group(2)
    bucket = _ROLE_WORDS[role_word]

    # A named model id (has a digit or a hyphen) vs a vague "a small model".
    named = re.search(r"([a-z0-9][\w./:-]*\d[\w./:-]*|[\w-]+-[\w.-]+)", model_phrase)
    if not named or model_phrase in ("a small model", "a smaller model", "a cheaper model",
                                     "a local model"):
        return (f"Which model should handle {role_word}? Name it, e.g. "
                f"\"use gpt-4o-mini for {role_word}\" (or set it in Settings → Model, "
                "per-role).", [])
    model = named.group(1)

    from ..ai import keys
    if settings.provider in (None, "", "none"):
        return ("No model provider is configured. Connect one in Settings → Model first, "
                "then route a role to a specific model.", [])
    merged = keys.set_role_models(db, provider=settings.provider, project_id=project.id,
                                  role_models={bucket: model})
    db.commit()
    return (f"Routing **{bucket}** ({role_word}) to `{model}`. Planner/grounder/judge now: "
            f"{', '.join(f'{k}={v}' for k, v in merged.items())}. "
            "Re-runs still cost nothing. This only changes what the model *build* steps use.",
            [{"type": "model_routing", "role": bucket, "model": model, "role_models": merged}])
