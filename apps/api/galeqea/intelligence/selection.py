"""Predictive test selection / test-impact analysis.

Running the whole suite on every change is honest but slow; running a hand-tagged
"smoke" set is fast but arbitrary. GaleQEA scores every test against the actual
change and runs the highest-value subset, then *reports what it skipped and why*
- a selector that silently drops coverage is worse than no selector at all.

Scoring blends five signals, in descending order of trustworthiness:

1. **Historical path correlation** - this test has failed before when these files
   changed. Learned from run history, no model required.
2. **Recent failure** - a test that failed recently is more likely to fail again.
3. **Requirement linkage** - the change touches an area traced to this test's
   requirements.
4. **Risk and priority** - author-declared importance.
5. **Staleness** - a test that has not run in a long time earns a turn, so the
   selector cannot starve part of the suite indefinitely.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RunStatus, TestCase, TestStat, TestStatus
from ..models.base import utcnow

RISK_WEIGHT = {"critical": 1.0, "high": 0.75, "medium": 0.45, "low": 0.2}
PRIORITY_WEIGHT = {"critical": 1.0, "high": 0.8, "medium": 0.5, "low": 0.25}


@dataclass(slots=True)
class Scored:
    test_case_id: str
    key: str
    title: str
    score: float
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "test_case_id": self.test_case_id, "key": self.key, "title": self.title,
            "score": round(self.score, 3), "reasons": self.reasons,
        }


def select_for_change(
    db: Session,
    project_id: str,
    *,
    changed_paths: list[str] | None = None,
    budget: int | None = None,
    min_score: float = 0.25,
    always_include_tags: tuple[str, ...] = ("smoke", "critical"),
) -> dict:
    """Rank the suite for a change and return both the selection and the omissions."""
    changed_paths = changed_paths or []
    cases = list(
        db.execute(
            select(TestCase).where(
                TestCase.project_id == project_id,
                TestCase.status == TestStatus.APPROVED,
            )
        ).scalars()
    )
    stats = {
        s.test_case_id: s
        for s in db.execute(select(TestStat).where(TestStat.project_id == project_id)).scalars()
    }

    scored: list[Scored] = []
    for case in cases:
        stat = stats.get(case.id)
        score = 0.0
        reasons: list[str] = []

        # 1. Path correlation.
        if changed_paths and stat and stat.correlated_paths:
            hits = [
                (path, weight)
                for path, weight in (stat.correlated_paths or {}).items()
                if any(_path_matches(path, changed) for changed in changed_paths)
            ]
            if hits:
                strength = min(1.0, sum(w for _, w in hits) / 3.0)
                score += 0.45 * strength
                reasons.append(
                    f"has failed before when {hits[0][0]} changed"
                    + (f" (+{len(hits) - 1} more)" if len(hits) > 1 else "")
                )

        # 2. Recent failure.
        if stat and stat.last_status in {RunStatus.FAILED, RunStatus.ERROR}:
            score += 0.25
            reasons.append("failed on its most recent run")
        elif stat and stat.outcome_window:
            recent_fails = sum(
                1 for w in stat.outcome_window[:10]
                if w.get("status") in {RunStatus.FAILED, RunStatus.ERROR}
            )
            if recent_fails:
                score += min(0.18, recent_fails * 0.05)
                reasons.append(f"failed {recent_fails}x in its last 10 runs")

        # 3. Requirement linkage to the change.
        if changed_paths and case.requirement_refs:
            if any(
                ref.lower() in changed.lower()
                for ref in case.requirement_refs for changed in changed_paths
            ):
                score += 0.2
                reasons.append("traces to a requirement referenced by the change")

        # 4. Declared importance.
        score += 0.18 * RISK_WEIGHT.get(case.risk, 0.45)
        score += 0.14 * PRIORITY_WEIGHT.get(case.priority, 0.5)

        # 5. Staleness - guarantees rotation so nothing is skipped forever.
        if stat and stat.last_run_at:
            days = (utcnow() - stat.last_run_at).days
            if days >= 7:
                bump = min(0.2, days / 60)
                score += bump
                reasons.append(f"has not run in {days} days")
        elif not stat:
            score += 0.3
            reasons.append("never executed")

        # Flaky tests are down-weighted, not excluded: they still carry signal.
        if case.flake_score > 0.5:
            score *= 0.75
            reasons.append(f"down-weighted (flake score {case.flake_score:.2f})")

        tags = {t.lower() for t in (case.tags or [])}
        if tags & set(always_include_tags):
            score = max(score, 0.95)
            reasons.append(f"tagged {', '.join(sorted(tags & set(always_include_tags)))} - always run")

        scored.append(Scored(case.id, case.key, case.title, min(1.0, score), reasons))

    scored.sort(key=lambda s: s.score, reverse=True)
    chosen = [s for s in scored if s.score >= min_score]
    dropped_by_threshold = [s for s in scored if s.score < min_score]

    dropped_by_budget: list[Scored] = []
    if budget and len(chosen) > budget:
        dropped_by_budget = chosen[budget:]
        chosen = chosen[:budget]

    total = len(scored)
    # A project with no approved tests is a legitimate state — a fresh workspace,
    # or one where everything is still proposed. It must return an empty selection,
    # not divide by zero. The percentage is only meaningful when there is a suite.
    if total == 0:
        note = ("No approved tests exist yet, so there is nothing to select. Approve some "
                "tests, or generate them from the requirements, before running impact analysis.")
    else:
        note = (
            f"Running {len(chosen)} of {total} approved tests ({len(chosen) / total:.0%})."
            + (f" {len(dropped_by_budget)} more scored above the threshold but were cut by the "
               f"budget of {budget}." if dropped_by_budget else "")
            + " Omitted tests are listed in full - this selection reduces time, not accountability."
        )
    return {
        "selected": [s.as_dict() for s in chosen],
        "selected_ids": [s.test_case_id for s in chosen],
        "omitted": [s.as_dict() for s in dropped_by_threshold + dropped_by_budget],
        "coverage_note": note,
        "changed_paths": changed_paths,
    }


def _path_matches(recorded: str, changed: str) -> bool:
    recorded, changed = recorded.lower(), changed.lower()
    if recorded == changed:
        return True
    # Directory-level correlation generalises better than exact file matches.
    return changed.startswith(recorded.rstrip("/") + "/") or recorded.startswith(changed)


def learn_from_run(db: Session, project_id: str, run_id: str, changed_paths: list[str]) -> int:
    """Reinforce path→test correlations from a completed run.

    Only failures teach. A passing test says nothing about whether it *covers*
    the change, which is why naive coverage-based selection over-selects.
    """
    from ..models import RunTest

    if not changed_paths:
        return 0

    failures = list(
        db.execute(
            select(RunTest).where(
                RunTest.run_id == run_id,
                RunTest.status.in_([RunStatus.FAILED, RunStatus.ERROR]),
            )
        ).scalars()
    )
    updated = 0
    for failure in failures:
        stat = db.execute(
            select(TestStat).where(TestStat.test_case_id == failure.test_case_id)
        ).scalar_one_or_none()
        if stat is None:
            continue
        correlations = dict(stat.correlated_paths or {})
        for path in changed_paths[:50]:
            correlations[path] = min(5.0, correlations.get(path, 0.0) + 1.0)
        # Decay keeps the map from calcifying around a long-fixed hotspot.
        stat.correlated_paths = {
            k: round(v * 0.97, 3) for k, v in correlations.items() if v * 0.97 > 0.1
        }
        updated += 1
    db.flush()
    return updated
