"""The coverage report: what is tested, and (the valuable half) what is not.

A coverage number on its own is marketing; the report that matters names the
requirements nothing tests and the ones whose only test is weak. This renders
the coverage intelligence in three consistent shapes so a human, an LLM, and an
external agent all read the same honest picture of the gaps.
"""

from __future__ import annotations

from ..intelligence.coverage import RISK_ORDER
from .common import api_href, cap_markdown, envelope, md_table, ui_href


def build_coverage_report(db, project) -> dict:
    """The canonical JSON coverage report. Deterministic; gaps ordered by ref."""
    from ..intelligence.coverage import compute

    cov = compute(db, project.id, persist=False)

    def _gap(entry: dict) -> dict:
        ref = entry["ref"]
        return {
            **entry,
            "id": ref,
            "href": api_href(project.id, "requirements", ref),
            "ui_href": ui_href("requirements"),
        }

    uncovered = [_gap(e) for e in sorted(cov["uncovered"], key=lambda e: e["ref"])]
    weak = [_gap(e) for e in sorted(cov["weak"], key=lambda e: e["ref"])]

    return envelope(
        project.id, "coverage",
        ("coverage", "report.json"),
        {
            "summary": {
                "total_requirements": cov["total_requirements"],
                "covered_requirements": cov["covered_requirements"],
                "coverage_pct": cov["coverage_pct"],
                "automation_pct": cov["automation_pct"],
            },
            "by_risk": {r: cov["by_risk"][r] for r in RISK_ORDER if r in cov["by_risk"]},
            "uncovered": uncovered,
            "weak": weak,
            "headline": cov["headline"],
        },
    )


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
def coverage_report_markdown(report: dict) -> str:
    s = report["summary"]
    lines = [
        "# Coverage report",
        "",
        f"**{s['coverage_pct']:.0f}% covered, {s['automation_pct']:.0f}% automated** "
        f"across {s['total_requirements']} requirement(s) "
        f"({s['covered_requirements']} covered).",
        report["headline"],
        "",
        "## By risk",
        "",
        md_table(
            ["Risk", "Total", "Covered", "Automated"],
            [[risk, g["total"], g["covered"], g["automated"]]
             for risk, g in report["by_risk"].items()],
        ),
        "## Uncovered requirements",
        "",
        md_table(
            ["Ref", "Title", "Risk"],
            [[u["ref"], u["title"], u["risk"]] for u in report["uncovered"]],
        ),
    ]
    return cap_markdown("\n".join(lines) + "\n", report["href"])
