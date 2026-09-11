"""Step-level cache (WO#8-B): zero-token re-runs, even for model-authored steps.

The first time the model resolves a locating step on a given page shape, the resolved
ladder is written to the ``StepCache`` keyed by (normalised intent, page fingerprint).
Every later run with the same intent on the same shape hits the cache and executes
deterministically, with no model call. Only navigation/locating steps are cached; an
assertion or a query is never cached, because its whole point is to look at the live
page.
"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.metrics import record_cache
from ..models import StepCache
from ..models.base import utcnow

#: Actions that locate or navigate, safe to cache their resolved ladder.
_CACHEABLE = {
    "click", "double_click", "fill", "type", "press", "select", "check", "uncheck",
    "hover", "upload", "scroll", "goto", "wait_for", "set_storage",
}
#: Actions that read the live page. NEVER cache these (Midscene rule).
_NEVER = {
    "expect_visible", "expect_text", "expect_value", "expect_url", "expect_count",
    "expect_attribute", "expect_semantic", "assert_a11y", "assert_perf",
    "snapshot", "api_request", "note", "script",
}

_LANDMARKS = ("banner", "navigation", "main", "complementary", "contentinfo",
              "region", "form", "search", "heading", "dialog", "tablist")


def is_cacheable(action: str) -> bool:
    a = (action or "").lower()
    if a in _NEVER:
        return False
    return a in _CACHEABLE


def normalize_intent(intent: str) -> str:
    """Lowercase, collapse whitespace, drop trailing punctuation, so "Click Pay"
    and "click  pay." map to the same key."""
    text = re.sub(r"\s+", " ", (intent or "").strip().lower())
    return re.sub(r"[.…!?,;:]+$", "", text).strip()


def page_fingerprint(url: str, aria_state: str) -> str:
    """URL path (no query/host) + a hash of the landmark/heading skeleton of the
    trimmed page state. Two pages with the same route and the same structural
    skeleton fingerprint identically, which is exactly when a cached ladder applies."""
    path = ""
    if url:
        m = re.search(r"https?://[^/]+(/[^?#]*)?", url)
        path = (m.group(1) if m and m.group(1) else "/").rstrip("/") or "/"
    skeleton = []
    for line in (aria_state or "").splitlines():
        role = re.search(r"-\s+([a-z]+)", line.strip())
        if role and role.group(1) in _LANDMARKS:
            name = re.search(r'"([^"]*)"', line)
            skeleton.append(f"{role.group(1)}:{(name.group(1) if name else '')[:40]}")
    digest = hashlib.sha256(("|".join(skeleton)).encode()).hexdigest()[:16]
    return f"{path}#{digest}"


def cache_key(intent: str, fingerprint: str) -> str:
    return hashlib.sha256(f"{normalize_intent(intent)}|{fingerprint}".encode()).hexdigest()[:32]


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
def lookup(db: Session, project_id: str, *, intent: str, action: str, url: str,
           aria_state: str) -> StepCache | None:
    """Return a cached ladder for this step, or None. Records the hit/miss metric.
    A non-cacheable action (assertion/query) always misses by design."""
    if not is_cacheable(action):
        return None
    key = cache_key(intent, page_fingerprint(url, aria_state))
    row = db.execute(select(StepCache).where(
        StepCache.project_id == project_id, StepCache.cache_key == key)).scalar_one_or_none()
    if row is None:
        record_cache("miss")
        return None
    row.hits += 1
    db.flush()
    record_cache("hit")
    return row


def write_back(db: Session, project_id: str, *, intent: str, action: str, url: str,
               aria_state: str, ladder: list, params: dict | None = None,
               model: str = "", strategy: str = "model") -> StepCache | None:
    """After the model resolves a locating step, persist the ladder so the next run
    hits the cache. No-ops for non-cacheable actions."""
    if not is_cacheable(action) or not ladder:
        return None
    fp = page_fingerprint(url, aria_state)
    key = cache_key(intent, fp)
    row = db.execute(select(StepCache).where(
        StepCache.project_id == project_id, StepCache.cache_key == key)).scalar_one_or_none()
    if row is None:
        row = StepCache(project_id=project_id, cache_key=key, intent=normalize_intent(intent),
                        page_fingerprint=fp)
        db.add(row)
    row.ladder = ladder
    row.params = params or {}
    row.provenance = {"model": model, "strategy": strategy, "ts": utcnow().isoformat()}
    db.flush()
    return row


def export_for_test(db: Session, project_id: str, refs: list[str]) -> dict:
    """The subset of the cache that a test carries with it (step_cache.yaml), keyed
    by cache_key → {intent, page_fingerprint, ladder, provenance}."""
    if not refs:
        return {}
    rows = db.execute(select(StepCache).where(
        StepCache.project_id == project_id, StepCache.cache_key.in_(refs))).scalars()
    return {r.cache_key: {"intent": r.intent, "page_fingerprint": r.page_fingerprint,
                          "ladder": r.ladder, "provenance": r.provenance} for r in rows}


def yaml_for_intents(db: Session, project_id: str, intents: list[str]) -> str:
    """Render the ``step_cache.yaml`` that travels with an exported test: every cached
    entry whose normalised intent matches one of the test's steps. Empty string when
    the test has no cached resolutions yet (so no stray file is written)."""
    import yaml

    wanted = {normalize_intent(i) for i in intents if i}
    if not wanted:
        return ""
    rows = [r for r in db.execute(select(StepCache).where(
        StepCache.project_id == project_id)).scalars() if r.intent in wanted]
    if not rows:
        return ""
    doc = {"version": 1, "steps": [
        {"cache_key": r.cache_key, "intent": r.intent, "page_fingerprint": r.page_fingerprint,
         "ladder": r.ladder, "provenance": r.provenance} for r in rows]}
    return yaml.safe_dump(doc, sort_keys=False)
