"""WO#8-B: the step cache short-circuits the model on a re-run.

Proves the zero-token re-run at the healing-engine level: once a step's ladder is in
the cache, a heal with the same intent on the same page shape resolves from Tier 0 and
NEVER reaches the provider. A fake provider that raises if called stands in for "a
regression would spend tokens here".
"""

from __future__ import annotations

import asyncio

import pytest

from galeqea.engine import step_cache as sc
from galeqea.engine.healing import HealingEngine

_ARIA = """- main:
  - heading "Checkout" [h1]
  - button "Pay" [ref=e1]"""
_URL = "https://shop.example/checkout"


class _ExplodingProvider:
    """If the cache fails to short-circuit, healing falls to the model, and this
    blows up, so the regression is loud rather than silent (tokens > 0)."""

    model = "should-never-be-called"

    async def complete(self, *a, **k):  # noqa: D401
        raise AssertionError("the model was called on a cache hit; zero-token re-run broke")


def test_cache_hit_resolves_without_the_model(db, project):
    # Prime the cache as if the model had resolved this step on a first run.
    sc.write_back(db, project.id, intent="Click Pay", action="click", url=_URL,
                  aria_state=_ARIA, ladder=[{"kind": "role", "value": "button[name=Pay]"}],
                  model="claude-x", strategy="model")
    db.commit()

    engine = HealingEngine(db, provider=_ExplodingProvider(), project_id=project.id)
    outcome = asyncio.run(engine.heal({
        "requestId": "r1", "intent": "click pay.", "action": "click", "url": _URL,
        "ariaSnapshot": _ARIA, "elementId": None,
        "candidates": [{"suggested": "role=button[name=Pay]", "score": 0.4}],
    }))

    assert outcome.ok is True
    assert outcome.strategy == "cache"            # resolved from Tier 0
    assert outcome.locator == {"kind": "role", "value": "button[name=Pay]"}
    assert outcome.evidence.get("cache") is True


def test_a_different_page_shape_misses_and_would_call_the_model(db, project):
    sc.write_back(db, project.id, intent="Click Pay", action="click", url=_URL,
                  aria_state=_ARIA, ladder=[{"kind": "role", "value": "x"}])
    db.commit()
    engine = HealingEngine(db, provider=_ExplodingProvider(), project_id=project.id)
    # Same intent, different route → cache miss → falls through toward the model.
    with pytest.raises(AssertionError, match="model was called"):
        asyncio.run(engine.heal({
            "requestId": "r2", "intent": "Click Pay", "action": "click",
            "url": "https://shop.example/account", "ariaSnapshot": _ARIA, "elementId": None,
            "candidates": [{"suggested": "role=button", "score": 0.4}],
        }))
