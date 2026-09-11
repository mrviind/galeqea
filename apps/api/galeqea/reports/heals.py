"""The self-healing report: every locator repair the system proposed.

A heal is always a *proposal* with evidence, never a silent mutation, so the
audit trail of what changed and why is the point. This report lists the recent
heal events with their before/after locators, scores, and review status, plus a
breakdown by status and strategy so the shape of the fragility is visible.
"""

from __future__ import annotations

from sqlalchemy import select

from ..models.appmodel import HealEvent
from .common import api_href, cap_markdown, envelope, md_table, ui_href

#: A window recent enough to be actionable without unbounded growth.
_LIMIT = 200


def build_heals_report(db, project) -> dict:
    """The canonical JSON heals report. Deterministic; newest first."""
    events = list(db.execute(
        select(HealEvent)
        .where(HealEvent.project_id == project.id)
        .order_by(HealEvent.created_at.desc(), HealEvent.id.desc())
        .limit(_LIMIT)
    ).scalars())

    items = []
    by_status: dict[str, int] = {}
    by_strategy: dict[str, int] = {}
    for he in events:
        items.append({
            "id": he.id,
            "test_case_id": he.test_case_id,
            "element_id": he.element_id,
            "strategy": he.strategy,
            "old_locator": he.old_locator,
            "new_locator": he.new_locator,
            "score": he.score,
            "status": he.status,
            "created_at": he.created_at.isoformat() if he.created_at else None,
            "href": api_href(project.id, "heals", he.id),
            "ui_href": ui_href("intelligence"),
        })
        by_status[he.status] = by_status.get(he.status, 0) + 1
        by_strategy[he.strategy] = by_strategy.get(he.strategy, 0) + 1

    return envelope(
        project.id, "heals",
        ("heals", "report.json"),
        {
            "summary": {
                "total": len(items),
                "by_status": dict(sorted(by_status.items())),
                "by_strategy": dict(sorted(by_strategy.items())),
            },
            "heals": items,
        },
    )


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
def heals_report_markdown(report: dict) -> str:
    s = report["summary"]
    by_status = ", ".join(f"{k}: {v}" for k, v in s["by_status"].items()) or "none"
    by_strategy = ", ".join(f"{k}: {v}" for k, v in s["by_strategy"].items()) or "none"
    lines = [
        "# Self-healing report",
        "",
        f"**{s['total']} heal event(s).** By status: {by_status}. "
        f"By strategy: {by_strategy}.",
        "",
        "## Heal events",
        "",
        md_table(
            ["Element", "Strategy", "Status", "Score"],
            [
                [it["element_id"], it["strategy"], it["status"], f"{it['score']:.2f}"]
                for it in report["heals"]
            ],
        ),
    ]
    return cap_markdown("\n".join(lines) + "\n", report["href"])
