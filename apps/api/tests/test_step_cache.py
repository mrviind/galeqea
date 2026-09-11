"""WO#8-B step cache: key/fingerprint determinism, never-cache-assertions,
and write-back → hit (zero-token re-run)."""

from __future__ import annotations

from sqlalchemy import select

from galeqea.engine import step_cache as sc
from galeqea.models import StepCache

_ARIA = """- banner:
  - heading "Shop" [h1]
  - link "Home" [ref=e1]
- main:
  - heading "Products" [h2]
  - button "Add to cart" [ref=e2]"""


# --------------------------------------------------------------------------- #
# Pure functions
# --------------------------------------------------------------------------- #
def test_is_cacheable_only_navigation_and_locating():
    assert sc.is_cacheable("click") and sc.is_cacheable("fill") and sc.is_cacheable("goto")
    # assertions & queries are never cached; their point is the live page
    for a in ("expect_text", "expect_visible", "expect_semantic", "assert_a11y",
              "snapshot", "api_request"):
        assert not sc.is_cacheable(a), a


def test_normalize_intent():
    assert sc.normalize_intent("  Click   the Pay button. ") == "click the pay button"
    assert sc.normalize_intent("Click Pay") == sc.normalize_intent("click  pay")


def test_page_fingerprint_stable_and_route_scoped():
    fp1 = sc.page_fingerprint("https://x.io/checkout?step=2", _ARIA)
    fp2 = sc.page_fingerprint("https://x.io/checkout?step=9#frag", _ARIA)  # query/frag ignored
    assert fp1 == fp2
    assert fp1.startswith("/checkout#")
    # a different route → different fingerprint
    assert sc.page_fingerprint("https://x.io/account", _ARIA) != fp1
    # a structural change (heading skeleton) → different fingerprint
    assert sc.page_fingerprint("https://x.io/checkout", _ARIA.replace("Products", "Cart")) != fp1


def test_cache_key_deterministic():
    fp = sc.page_fingerprint("https://x.io/checkout", _ARIA)
    assert sc.cache_key("Click Pay", fp) == sc.cache_key("click pay.", fp)
    assert sc.cache_key("Click Pay", fp) != sc.cache_key("Click Cancel", fp)


# --------------------------------------------------------------------------- #
# Store: write-back then hit
# --------------------------------------------------------------------------- #
def test_write_back_then_hit_returns_ladder_and_counts(db, project):
    ladder = [{"kind": "role", "value": "button[name=Pay]"}]
    row = sc.write_back(db, project.id, intent="Click Pay", action="click",
                        url="https://x.io/checkout", aria_state=_ARIA, ladder=ladder,
                        model="claude-x", strategy="model")
    db.commit()
    assert row is not None and row.provenance["model"] == "claude-x"

    hit = sc.lookup(db, project.id, intent="click pay.", action="click",
                    url="https://x.io/checkout?a=1", aria_state=_ARIA)
    assert hit is not None and hit.ladder == ladder and hit.hits == 1


def test_assertion_step_is_never_cached(db, project):
    assert sc.write_back(db, project.id, intent="see the total", action="expect_text",
                         url="https://x.io/checkout", aria_state=_ARIA,
                         ladder=[{"x": 1}]) is None
    assert sc.lookup(db, project.id, intent="see the total", action="expect_text",
                     url="https://x.io/checkout", aria_state=_ARIA) is None
    # nothing was written
    assert db.execute(select(StepCache).where(StepCache.project_id == project.id)
                      ).scalars().first() is None


def test_miss_on_a_different_page_shape(db, project):
    sc.write_back(db, project.id, intent="Click Pay", action="click",
                  url="https://x.io/checkout", aria_state=_ARIA, ladder=[{"k": 1}])
    db.commit()
    # same intent, different route → miss
    assert sc.lookup(db, project.id, intent="Click Pay", action="click",
                     url="https://x.io/account", aria_state=_ARIA) is None


def test_export_for_test_carries_the_entries(db, project):
    row = sc.write_back(db, project.id, intent="Click Pay", action="click",
                        url="https://x.io/checkout", aria_state=_ARIA, ladder=[{"k": 1}])
    db.commit()
    out = sc.export_for_test(db, project.id, [row.cache_key])
    assert out[row.cache_key]["ladder"] == [{"k": 1}]
    assert out[row.cache_key]["intent"] == "click pay"


def test_yaml_for_intents_matches_test_steps(db, project):
    sc.write_back(db, project.id, intent="Click Pay", action="click",
                  url="https://x.io/checkout", aria_state=_ARIA, ladder=[{"k": 1}])
    db.commit()
    # a step whose intent matches (case-insensitively / with trailing punctuation)
    yaml_text = sc.yaml_for_intents(db, project.id, ["Click Pay.", "unrelated step"])
    assert "Click Pay" not in yaml_text and "click pay" in yaml_text
    assert "ladder" in yaml_text
    # a test with no cached steps → no file
    assert sc.yaml_for_intents(db, project.id, ["something never cached"]) == ""


def test_export_always_returns_step_cache_key(db, project):
    """WO#8 nit: the export must always carry step_cache_yaml + filename, even for a
    test with no cached ladders, so a consumer never has to branch on its presence."""
    import yaml as _yaml
    from fastapi.testclient import TestClient

    from galeqea.main import app
    from galeqea.models.testing import StepAction, TestCase, TestStep

    case = TestCase(project_id=project.id, key="PROJ-T-9001", title="Uncached export")
    db.add(case)
    db.flush()
    db.add(TestStep(test_case_id=case.id, index=0, action=StepAction.GOTO,
                    intent="open the home page", value={"url": "/"}))
    db.commit()

    body = TestClient(app).get(
        f"/api/projects/{project.id}/tests/{case.id}/export?target=playwright").json()
    assert "step_cache_yaml" in body and "step_cache_filename" in body
    assert body["step_cache_filename"] == "proj-t-9001.step_cache.yaml"
    # a valid, empty-but-parseable doc, not an absent key, not a broken file
    doc = _yaml.safe_load(body["step_cache_yaml"])
    assert doc == {"version": 1, "steps": []}
