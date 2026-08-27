"""Flaky-test detection.

Re-running a test 10,000 times is the gold standard and is useless in practice.
QE Agent instead scores flakiness from evidence CI already produces, and is
explicit about how much it trusts that evidence.

Signals, strongest first:

1. **Same-commit disagreement** - the test passed and failed at the *same* git
   SHA. Barring an environment change, that is flakiness by definition.
2. **Retry rescue** - failed, then passed on retry with nothing else changed.
3. **Outcome flips** - transitions in the recent outcome window, normalised by
   window length so a long-lived test is not punished for age.
4. **Healing pressure** - a test whose locators keep needing rescue is fragile
   even when it is green.
5. **Duration variance** - a wildly variable runtime usually means a race.

Each signal contributes to a 0-1 score, and a **separate confidence** value says
how much data is behind it. Reporting a score without its confidence is how
teams end up quarantining a test that has run twice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import RunStatus, TestCase, TestStat
from ..models.base import utcnow

WINDOW = 40
QUARANTINE_SCORE = 0.65
QUARANTINE_CONFIDENCE = 0.6


@dataclass(slots=True)
class FlakeAssessment:
    score: float
    confidence: float
    reasons: list[str] = field(default_factory=list)
    recommendation: str = "monitor"

    def as_dict(self) -> dict:
        return {
            "score": round(self.score, 3),
            "confidence": round(self.confidence, 3),
            "reasons": self.reasons,
            "recommendation": self.recommendation,
        }


def wilson_lower_bound(successes: int, total: int, z: float = 1.96) -> float:
    """Lower bound of the pass-rate confidence interval.

    Preferred over a raw ratio because 1/1 passes and 40/40 passes are very
    different claims, and the raw ratio calls them both 1.0.
    """
    if total == 0:
        return 0.0
    phat = successes / total
    denom = 1 + z**2 / total
    centre = phat + z**2 / (2 * total)
    margin = z * math.sqrt((phat * (1 - phat) + z**2 / (4 * total)) / total)
    return max(0.0, (centre - margin) / denom)


def _entropy(window: list[str]) -> float:
    """Shannon entropy of the outcome window, normalised to 0-1."""
    if len(window) < 2:
        return 0.0
    counts: dict[str, int] = {}
    for outcome in window:
        counts[outcome] = counts.get(outcome, 0) + 1
    total = len(window)
    h = -sum((c / total) * math.log2(c / total) for c in counts.values() if c)
    return min(1.0, h / math.log2(max(2, len(counts))))


def _num(value, default: float = 0.0) -> float:
    """Column defaults only apply on INSERT, so an in-memory stat can hold None.

    Assessment runs on freshly constructed stats as well as persisted ones, and
    crashing on a not-yet-flushed row would fail a whole run finalisation for a
    reason that has nothing to do with the tests.
    """
    return default if value is None else value


def assess(stat: TestStat) -> FlakeAssessment:
    window: list[str] = [w.get("status", "") for w in (stat.outcome_window or [])][:WINDOW]
    reasons: list[str] = []
    score = 0.0

    runs = int(_num(stat.runs))
    same_sha = int(_num(stat.same_sha_disagreements))
    rescues = int(_num(stat.retry_rescues))
    heals = int(_num(stat.heal_count))
    mean_duration = _num(stat.mean_duration_ms)
    m2 = _num(stat.m2_duration)

    # 1. Same-commit disagreement - the strongest available evidence.
    if same_sha:
        contribution = min(0.55, 0.28 * same_sha)
        score += contribution
        reasons.append(
            f"passed and failed at the same commit {same_sha}x "
            "(non-deterministic by definition)"
        )

    # 2. Retry rescues.
    if rescues:
        rate = rescues / max(1, runs)
        contribution = min(0.30, rate * 1.5)
        score += contribution
        reasons.append(f"rescued by retry {rescues}x ({rate:.0%} of runs)")

    # 3. Outcome flips in the window.
    flips = sum(1 for a, b in zip(window, window[1:], strict=False) if a != b)
    if flips and len(window) > 3:
        flip_rate = flips / (len(window) - 1)
        contribution = min(0.30, flip_rate * 0.6)
        score += contribution
        reasons.append(f"outcome flipped {flips}x across the last {len(window)} runs")

    # 4. Healing pressure.
    if heals and runs:
        heal_rate = heals / runs
        if heal_rate > 0.15:
            score += min(0.18, heal_rate * 0.4)
            reasons.append(f"locators needed healing in {heal_rate:.0%} of runs")

    # 5. Duration instability - a proxy for races and waits.
    if runs > 4 and mean_duration > 0:
        variance = m2 / max(1, runs - 1)
        cv = math.sqrt(max(0.0, variance)) / mean_duration
        if cv > 0.5:
            score += min(0.15, (cv - 0.5) * 0.3)
            reasons.append(f"runtime varies wildly (coefficient of variation {cv:.2f})")

    entropy = _entropy(window)
    if entropy > 0.8 and len(window) >= 6:
        score += 0.10
        reasons.append("outcome sequence is close to random")

    score = max(0.0, min(1.0, score))

    # Confidence is about *evidence volume*, deliberately separate from score.
    confidence = min(1.0, math.log1p(runs) / math.log1p(30))
    if same_sha:
        confidence = min(1.0, confidence + 0.25)

    if score >= QUARANTINE_SCORE and confidence >= QUARANTINE_CONFIDENCE:
        recommendation = "quarantine"
    elif score >= 0.4:
        recommendation = "investigate"
    elif score >= 0.2:
        recommendation = "monitor"
    else:
        recommendation = "healthy"

    if not reasons:
        reasons.append("no instability signals in the observed history")

    return FlakeAssessment(
        score=score, confidence=confidence, reasons=reasons, recommendation=recommendation
    )


def record_outcome(
    db: Session,
    *,
    project_id: str,
    test_case_id: str,
    status: str,
    duration_ms: int,
    git_sha: str = "",
    healed: bool = False,
    attempt: int = 1,
) -> TestStat:
    """Fold one result into the rolling stats. Online, so history never reloads."""
    stat = db.execute(
        select(TestStat).where(TestStat.test_case_id == test_case_id)
    ).scalar_one_or_none()
    if stat is None:
        stat = TestStat(project_id=project_id, test_case_id=test_case_id)
        db.add(stat)
        db.flush()

    window = list(stat.outcome_window or [])
    previous = window[0] if window else None

    stat.runs += 1
    if status == RunStatus.PASSED:
        stat.passes += 1
    elif status == RunStatus.ERROR:
        stat.errors += 1
    else:
        stat.failures += 1

    if previous and previous.get("status") != status:
        stat.flips += 1
    # Same commit, different verdict: the clearest possible flakiness evidence.
    if git_sha and previous and previous.get("git_sha") == git_sha \
            and previous.get("status") != status \
            and {previous.get("status"), status} <= {RunStatus.PASSED, RunStatus.FAILED}:
        stat.same_sha_disagreements += 1
    if attempt > 1 and status == RunStatus.PASSED:
        stat.retry_rescues += 1
    if healed:
        stat.heal_count += 1

    # Welford's online mean/variance - no need to keep every sample.
    if duration_ms > 0:
        delta = duration_ms - stat.mean_duration_ms
        stat.mean_duration_ms += delta / stat.runs
        stat.m2_duration += delta * (duration_ms - stat.mean_duration_ms)
        samples = ([duration_ms] + list(stat.duration_samples or []))[:60]
        stat.duration_samples = samples
        ordered = sorted(samples)
        stat.p95_duration_ms = ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]

    window.insert(0, {
        "status": status, "git_sha": git_sha, "at": utcnow().isoformat(),
        "duration_ms": duration_ms, "attempt": attempt,
    })
    stat.outcome_window = window[:WINDOW]
    stat.last_status = status
    stat.last_run_at = utcnow()

    verdict = assess(stat)
    stat.flake_score = verdict.score
    stat.flake_confidence = verdict.confidence
    stat.flake_reasons = verdict.reasons

    case = db.get(TestCase, test_case_id)
    if case:
        case.flake_score = verdict.score
        case.last_status = status
        case.avg_duration_ms = int(stat.mean_duration_ms)
        # Quarantine is *proposed*, never auto-applied: silently removing a test
        # from the suite is exactly how coverage quietly disappears.
    db.flush()
    return stat


def quarantine_candidates(db: Session, project_id: str) -> list[dict]:
    stats = db.execute(
        select(TestStat).where(TestStat.project_id == project_id)
    ).scalars()
    out: list[dict] = []
    for stat in stats:
        verdict = assess(stat)
        if verdict.recommendation != "quarantine":
            continue
        case = db.get(TestCase, stat.test_case_id)
        out.append({
            "test_case_id": stat.test_case_id,
            "key": case.key if case else "",
            "title": case.title if case else "",
            "already_quarantined": bool(case and case.quarantined),
            "pass_rate": round(stat.passes / max(1, stat.runs), 3),
            "wilson_lower_bound": round(wilson_lower_bound(stat.passes, stat.runs), 3),
            **verdict.as_dict(),
        })
    out.sort(key=lambda r: (r["score"] * r["confidence"]), reverse=True)
    return out
