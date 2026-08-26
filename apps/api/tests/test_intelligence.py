"""Intelligence layer behaviour, including the cases that are easy to get wrong."""

from __future__ import annotations

import pytest

from galeqea.intelligence import flaky, signatures
from galeqea.intelligence.anomaly import robust_z
from galeqea.models import RunStatus, TestStat


# --------------------------------------------------------------------------- #
# Failure signatures
# --------------------------------------------------------------------------- #
def test_signatures_ignore_volatile_detail():
    a = "Timeout 30000ms exceeded waiting for element at (412, 883) req=8f3c1a2e trace 0xdeadbeef"
    b = "Timeout 30000ms exceeded waiting for element at (77, 210) req=1b9d4f77 trace 0xcafef00d"
    assert signatures.compute_signature("TimeoutError", a, "T-1") == \
           signatures.compute_signature("TimeoutError", b, "T-1")


def test_signatures_separate_different_tests():
    message = "Timeout 30000ms exceeded"
    assert signatures.compute_signature("TimeoutError", message, "T-1") != \
           signatures.compute_signature("TimeoutError", message, "T-2")


@pytest.mark.parametrize(("error", "expected"), [
    ("connect ECONNREFUSED 127.0.0.1:3000", "environment"),
    ("could not find the element for \"pay\"", "test_defect"),
    ("expected 'Order confirmed', got 'Error'", "assertion"),
    ("HTTP 503 from /api/orders", "server_error"),
])
def test_error_classification(error, expected):
    assert signatures.classify_error("", error) == expected


# --------------------------------------------------------------------------- #
# Flakiness
# --------------------------------------------------------------------------- #
def test_wilson_bound_penalises_thin_evidence():
    # One pass out of one is not the same claim as forty out of forty.
    assert flaky.wilson_lower_bound(1, 1) < flaky.wilson_lower_bound(40, 40)
    assert flaky.wilson_lower_bound(0, 0) == 0.0


def test_same_commit_disagreement_dominates_the_score():
    stat = TestStat(project_id="p", test_case_id="t", runs=10, passes=5, failures=5,
                    same_sha_disagreements=2, outcome_window=[])
    verdict = flaky.assess(stat)
    assert verdict.score >= 0.5
    assert any("same commit" in reason for reason in verdict.reasons)


def test_stable_test_scores_low_and_says_so():
    stat = TestStat(project_id="p", test_case_id="t", runs=30, passes=30,
                    outcome_window=[{"status": "passed"} for _ in range(30)])
    verdict = flaky.assess(stat)
    assert verdict.score < 0.2
    assert verdict.recommendation == "healthy"


def test_confidence_is_independent_of_score():
    """A high score from two runs must not be presented as certain."""
    thin = TestStat(project_id="p", test_case_id="t", runs=2, passes=1, failures=1,
                    same_sha_disagreements=1, outcome_window=[])
    thick = TestStat(project_id="p", test_case_id="t2", runs=40, passes=20, failures=20,
                     same_sha_disagreements=1, outcome_window=[])
    assert flaky.assess(thin).confidence < flaky.assess(thick).confidence


def test_record_outcome_tracks_flips_and_retry_rescues(db, project):
    from galeqea.models import TestCase, TestCategory, TestStatus

    case = TestCase(project_id=project.id, key="T-1", title="x",
                    category=TestCategory.AUTOMATED, status=TestStatus.APPROVED)
    db.add(case)
    db.commit()

    for status, attempt in [("passed", 1), ("failed", 1), ("passed", 2), ("passed", 1)]:
        flaky.record_outcome(db, project_id=project.id, test_case_id=case.id,
                             status=status, duration_ms=1000, git_sha="abc", attempt=attempt)
    db.commit()

    stat = db.query(TestStat).filter(TestStat.test_case_id == case.id).one()
    assert stat.runs == 4
    assert stat.flips == 2                      # passed→failed→passed
    assert stat.retry_rescues == 1              # the attempt-2 pass
    assert stat.same_sha_disagreements >= 1     # all at the same sha


# --------------------------------------------------------------------------- #
# Anomaly detection
# --------------------------------------------------------------------------- #
def test_robust_z_is_not_fooled_by_a_single_outlier():
    """A mean/stddev detector is blinded by one huge sample; MAD is not."""
    history = [100, 105, 98, 102, 99, 101, 30_000]  # one pathological run
    sigma, expected = robust_z(400, history)
    assert expected < 200            # median is unmoved by the outlier
    assert sigma > 3                 # 400ms is still flagged as anomalous


def test_robust_z_declines_on_thin_history():
    sigma, _ = robust_z(500, [100, 120])
    assert sigma == 0.0
