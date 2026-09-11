"""WO#8-C per-role model routing: role→bucket mapping, config merge, chat verb."""

from __future__ import annotations

from galeqea.ai import keys
from galeqea.ai.providers.registry import _ROLE_BUCKET


def test_role_bucket_mapping():
    assert _ROLE_BUCKET["healer"] == "grounder"      # locating/healing → the cheap model
    assert _ROLE_BUCKET["locator"] == "grounder"
    assert _ROLE_BUCKET["judge"] == "judge"
    assert _ROLE_BUCKET["rca_analyst"] == "judge"
    assert _ROLE_BUCKET["explorer"] == "planner"
    assert _ROLE_BUCKET["test_designer"] == "planner"


def test_set_role_models_merges_without_touching_the_key(db, project):
    keys.set_role_models(db, provider="anthropic", project_id=project.id,
                         role_models={"grounder": "haiku-small"})
    db.commit()
    cfg = keys.config_for(db, provider="anthropic", project_id=project.id)
    assert cfg["role_models"] == {"grounder": "haiku-small"}

    # a second call merges rather than replaces, and drops empty values
    keys.set_role_models(db, provider="anthropic", project_id=project.id,
                         role_models={"judge": "sonnet-mid", "planner": ""})
    db.commit()
    cfg = keys.config_for(db, provider="anthropic", project_id=project.id)
    assert cfg["role_models"] == {"grounder": "haiku-small", "judge": "sonnet-mid"}


def test_chat_routes_a_role_to_a_named_model(db, project, humans, monkeypatch):
    from galeqea.config import settings
    from galeqea.services import model_chat
    monkeypatch.setattr(settings, "provider", "anthropic")

    text, blocks = model_chat.try_handle(db, project, "use gpt-4o-mini for locating",
                                         humans["author"])
    assert "grounder" in text and "gpt-4o-mini" in text
    assert blocks[0]["type"] == "model_routing" and blocks[0]["role"] == "grounder"
    cfg = keys.config_for(db, provider="anthropic", project_id=project.id)
    assert cfg["role_models"]["grounder"] == "gpt-4o-mini"


def test_chat_asks_when_no_model_named(db, project, humans, monkeypatch):
    from galeqea.config import settings
    from galeqea.services import model_chat
    monkeypatch.setattr(settings, "provider", "anthropic")
    text, blocks = model_chat.try_handle(db, project, "use a small model for locating",
                                         humans["author"])
    assert "which model" in text.lower() and blocks == []


def test_chat_ignores_unrelated_text(db, project, humans):
    from galeqea.services import model_chat
    assert model_chat.try_handle(db, project, "run the smoke tests", humans["author"]) is None


# --- WO#8-C: per-role token ceilings + stop-and-ask ------------------------ #

def test_set_role_ceilings_merges_and_clears(db, project):
    keys.set_role_ceilings(db, provider="anthropic", project_id=project.id,
                           role_ceilings={"grounder": 2000, "judge": 3000})
    db.commit()
    assert keys.role_ceiling(db, provider="anthropic", project_id=project.id,
                             bucket="grounder") == 2000
    # a 0 clears just that role, leaving the others
    keys.set_role_ceilings(db, provider="anthropic", project_id=project.id,
                           role_ceilings={"grounder": 0})
    db.commit()
    assert keys.role_ceiling(db, provider="anthropic", project_id=project.id,
                             bucket="grounder") is None
    assert keys.role_ceiling(db, provider="anthropic", project_id=project.id,
                             bucket="judge") == 3000


def test_check_role_ceiling_raises_over_cap(db, project):
    keys.set_role_ceilings(db, provider="anthropic", project_id=project.id,
                           role_ceilings={"grounder": 500})
    db.commit()
    # under the cap: silent
    keys.check_role_ceiling(db, provider="anthropic", project_id=project.id,
                            bucket="grounder", est_tokens=400)
    # over the cap: stop-and-ask
    import pytest
    with pytest.raises(keys.RoleCeilingExceeded) as ei:
        keys.check_role_ceiling(db, provider="anthropic", project_id=project.id,
                                bucket="grounder", est_tokens=900)
    assert ei.value.ceiling == 500 and ei.value.est_tokens == 900
    assert "stopping" in str(ei.value).lower() or "over its" in str(ei.value).lower()


def test_check_role_ceiling_noop_without_config(db, project):
    # no ceiling configured -> never raises
    keys.check_role_ceiling(db, provider="anthropic", project_id=project.id,
                            bucket="grounder", est_tokens=10_000)


def test_chat_caps_a_role(db, project, humans, monkeypatch):
    from galeqea.config import settings
    from galeqea.services import model_chat
    monkeypatch.setattr(settings, "provider", "anthropic")
    text, blocks = model_chat.try_handle(db, project, "cap locating at 2000 tokens",
                                         humans["author"])
    assert "grounder" in text and "2000" in text
    assert blocks[0]["type"] == "role_ceiling" and blocks[0]["ceiling"] == 2000
    assert keys.role_ceiling(db, provider="anthropic", project_id=project.id,
                             bucket="grounder") == 2000
