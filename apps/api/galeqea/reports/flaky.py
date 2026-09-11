"""The flaky-test report: which tests can't be trusted, and how sure we are.

Flakiness is scored from evidence CI already produces, and every score is paired
with a confidence so nobody quarantines a test that has run twice. This report
lists the tests scoring at or above a low threshold, worst first, with the
reasons behind each score.
"""

from __future__ import annotations

from sqlalchemy import select

from ..intelligence.flaky import assess
from ..models import TestCase, TestStat
from .common import api_href, cap_markdown, envelope, md_table, ui_href

#: Below this a test is effectively healthy; surfacing it would be noise.
_MIN_SCORE = 0.3


def build_flaky_report(db, project) -> dict:
    """The canonical JSON flaky report. Deterministic; ordered by score desc."""
    cases = {
        c.id: c
        for c in db.execute(
            select(TestCase).where(TestCase.project_id == project.id)
        ).scalars()
    }
    stats = {
        s.test_case_id: s
        for s in db.execute(
            select(TestStat).where(TestStat.project_id == project.id)
        ).scalars()
    }

    items = []
    for case_id, case in cases.items():
        stat = stats.get(case_id)
        if stat is None:
            continue
        a = assess(stat)
        if a.score < _MIN_SCORE:
            continue
        items.append({
            "id": case.id,
            "key": case.key,
            "title": case.title,
            "score": round(a.score, 3),
            "confidence": round(a.confidence, 3),
            "reasons": a.reasons,
            "recommendation": a.recommendation,
            "href": api_href(project.id, "tests", case.key),
            "ui_href": ui_href("tests"),
        })

    items.sort(key=lambda it: (-it["score"], it["key"]))

    return envelope(
        project.id, "flaky",
        ("flaky", "report.json"),
        {
            "summary": {
                "flaky_count": len(items),
                "evaluated": len(stats),
            },
            "tests": items,
        },
    )


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
def flaky_report_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# Flaky-test report",
        "",
        f"**{s['flaky_count']} flaky test(s)** out of {s['evaluated']} evaluated.",
        "",
        "## Flaky tests",
        "",
        md_table(
            ["Test", "Score", "Confidence", "Reasons"],
            [
                [it["key"] or it["title"], f"{it['score']:.2f}",
                 f"{it['confidence']:.2f}", "; ".join(it["reasons"])]
                for it in report["tests"]
            ],
        ),
    ]
    return cap_markdown("\n".join(lines) + "\n", report["href"])
