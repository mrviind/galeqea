"""WO#8-C: a per-role ceiling stops and asks instead of spending.

Engine-level assertion (the manager signed off on this over an e2e version): with a
grounder ceiling configured and a call whose estimated tokens run past it, the healing
engine degrades to the deterministic tier and NEVER reaches the model. A fake provider
that raises if called stands in for "a regression would spend tokens here". A cache hit
must stay free even under a ceiling, so that case is covered too.
"""

from __future__ import annotations

import asyncio

from galeqea.ai import keys
from galeqea.engine import step_cache as sc
from galeqea.engine.healing import HealingEngine

_ARIA = """- main:
  - heading "Checkout" [h1]
  - button "Pay" [ref=e1]"""
_URL = "https://shop.example/checkout"


class _ExplodingProvider:
    model = "should-never-be-called"

    async def complete(self, *a, **k):
        raise AssertionError("the model was called despite the per-call ceiling")


def _req(**over):
    base = {
        "requestId": "r1", "intent": "click pay.", "action": "click", "url": _URL,
        "ariaSnapshot": _ARIA, "elementId": None, "state_tokens": 5000,
        "candidates": [{"suggested": "role=button[name=Pay]", "score": 0.4}],
    }
    base.update(over)
    return base


def test_ceiling_stops_before_the_model(db, project, monkeypatch):
    from galeqea.config import settings
    monkeypatch.setattr(settings, "provider", "anthropic")
    keys.set_role_ceilings(db, provider="anthropic", project_id=project.id,
                           role_ceilings={"grounder": 500})
    db.commit()

    engine = HealingEngine(db, provider=_ExplodingProvider(), project_id=project.id)
    outcome = asyncio.run(engine.heal(_req()))  # 5000 state tokens ≫ 500 cap

    assert outcome.ok is False
    assert outcome.strategy == "ceiling"          # deliberate stop-and-ask, not a low-confidence decline
    assert outcome.evidence.get("ceiling") == 500
    assert outcome.evidence.get("est_tokens") >= 5000


def test_under_the_ceiling_still_calls_the_model(db, project, monkeypatch):
    from galeqea.config import settings
    monkeypatch.setattr(settings, "provider", "anthropic")
    keys.set_role_ceilings(db, provider="anthropic", project_id=project.id,
                           role_ceilings={"grounder": 100_000})
    db.commit()
    engine = HealingEngine(db, provider=_ExplodingProvider(), project_id=project.id)
    # Well under the cap → the ceiling does not intervene → the model is reached (and explodes).
    import pytest
    with pytest.raises(AssertionError, match="model was called"):
        asyncio.run(engine.heal(_req(state_tokens=50)))


def test_cache_hit_is_free_even_under_a_ceiling(db, project, monkeypatch):
    from galeqea.config import settings
    monkeypatch.setattr(settings, "provider", "anthropic")
    keys.set_role_ceilings(db, provider="anthropic", project_id=project.id,
                           role_ceilings={"grounder": 1})   # absurdly low
    sc.write_back(db, project.id, intent="click pay.", action="click", url=_URL,
                  aria_state=_ARIA, ladder=[{"kind": "role", "value": "button[name=Pay]"}],
                  model="claude-x", strategy="model")
    db.commit()

    engine = HealingEngine(db, provider=_ExplodingProvider(), project_id=project.id)
    outcome = asyncio.run(engine.heal(_req(state_tokens=99999)))
    assert outcome.ok is True and outcome.strategy == "cache"   # Tier 0 short-circuits before the ceiling
