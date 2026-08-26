"""Anomaly detection over run metrics.

Uses a robust z-score (median and MAD) rather than mean and standard deviation:
test timings are heavily skewed by occasional slow runs, and a single 30-second
outlier drags a mean-based detector far enough that it stops noticing anything.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AnomalyRecord, Run, RunTest, TestStat

MAD_SCALE = 1.4826  # makes MAD a consistent estimator of sigma for normal data
WARN_SIGMA = 3.0
CRITICAL_SIGMA = 5.0
MIN_SAMPLES = 6


@dataclass(slots=True)
class Anomaly:
    subject_id: str
    label: str
    metric: str
    observed: float
    expected: float
    sigma: float
    severity: str
    detail: dict

    def as_dict(self) -> dict:
        return {
            "subject_id": self.subject_id, "label": self.label, "metric": self.metric,
            "observed": round(self.observed, 2), "expected": round(self.expected, 2),
            "sigma": round(self.sigma, 2), "severity": self.severity, "detail": self.detail,
        }


def robust_z(value: float, samples: list[float]) -> tuple[float, float]:
    """Returns (sigma-equivalent deviation, expected value)."""
    if len(samples) < MIN_SAMPLES:
        return 0.0, value
    median = statistics.median(samples)
    mad = statistics.median([abs(s - median) for s in samples])
    if mad == 0:
        # Degenerate but common: a perfectly stable test. Fall back to spread.
        spread = statistics.pstdev(samples) or 1.0
        return abs(value - median) / spread, median
    return abs(value - median) / (mad * MAD_SCALE), median


def detect_for_run(db: Session, run: Run) -> list[Anomaly]:
    results = list(db.execute(select(RunTest).where(RunTest.run_id == run.id)).scalars())
    found: list[Anomaly] = []

    for result in results:
        stat = db.execute(
            select(TestStat).where(TestStat.test_case_id == result.test_case_id)
        ).scalar_one_or_none()
        if not stat or not stat.duration_samples:
            continue
        # Exclude this run's own sample so a spike cannot mask itself.
        history = [float(d) for d in stat.duration_samples[1:]]
        sigma, expected = robust_z(float(result.duration_ms), history)
        if sigma >= WARN_SIGMA and result.duration_ms > expected:
            found.append(Anomaly(
                subject_id=result.test_case_id,
                label=f"{result.test_key} {result.title}".strip(),
                metric="duration_ms",
                observed=float(result.duration_ms),
                expected=expected,
                sigma=sigma,
                severity="critical" if sigma >= CRITICAL_SIGMA else "warning",
                detail={
                    "samples": len(history),
                    "note": f"took {result.duration_ms}ms against a typical {expected:.0f}ms "
                            f"({result.duration_ms / max(1.0, expected):.1f}x)",
                },
            ))

    # Suite-level pass-rate shift: catches a broad regression that no single
    # test flags loudly enough on its own.
    history_runs = list(
        db.execute(
            select(Run)
            .where(Run.project_id == run.project_id, Run.id != run.id)
            .order_by(Run.created_at.desc())
            .limit(25)
        ).scalars()
    )
    rates = [
        (r.totals or {}).get("passed", 0) / max(1, (r.totals or {}).get("total", 1))
        for r in history_runs
        if (r.totals or {}).get("total")
    ]
    current_total = (run.totals or {}).get("total", 0)
    if rates and current_total:
        current = (run.totals or {}).get("passed", 0) / current_total
        sigma, expected = robust_z(current, rates)
        if sigma >= WARN_SIGMA and current < expected:
            found.append(Anomaly(
                subject_id=run.id,
                label="suite pass rate",
                metric="pass_rate",
                observed=current,
                expected=expected,
                sigma=sigma,
                severity="critical" if sigma >= CRITICAL_SIGMA else "warning",
                detail={"note": f"pass rate fell to {current:.0%} against a typical {expected:.0%}"},
            ))

    for anomaly in found:
        db.add(AnomalyRecord(
            project_id=run.project_id, run_id=run.id, scope="run",
            metric=anomaly.metric, subject_id=anomaly.subject_id,
            observed=anomaly.observed, expected=anomaly.expected,
            deviation_sigma=anomaly.sigma, severity=anomaly.severity,
            detail={"label": anomaly.label, **anomaly.detail},
        ))
    db.flush()
    return found
