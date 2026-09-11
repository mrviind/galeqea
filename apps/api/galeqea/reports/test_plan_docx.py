"""Corporate Test Plan document (.docx), IEEE-829-style.

The formal plan a QA lead circulates before execution: identifier, scope, the items
under test, the features and techniques, entry/exit criteria, deliverables, schedule
and risks. Rendered from a project's journey (crawled pages + typed plan) or its
ingested requirements. Branded, deterministic, python-docx. The visual system
lives in ``docx_style``.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime

from docx.shared import Pt

from . import docx_style as S


def build_test_plan_docx(db, project, journey=None, *, company: str = "") -> bytes:
    settings_meta = project.settings or {}
    company = company or settings_meta.get("report_company") or "GaleQEA"
    logo = settings_meta.get("report_logo_path") or S.DEFAULT_LOGO
    plan = (journey.plan if journey else {}) or {}
    typed = plan.get("typed", {})
    pages = plan.get("pages", [])
    target = (journey.target if journey else "") or (project.environments or {}).get(
        project.default_environment, "") or "-"
    types = [r for r in typed.get("types", []) if r.get("enabled")]
    total_cases = typed.get("totals", {}).get("test_count", sum(r.get("count", 0) for r in types))
    est_min = sum(float(r.get("est_minutes", 0) or 0) for r in types)

    doc = S.base_document(title="Test Plan", subject=target, author=company)

    S.cover(
        doc, kicker="Test Plan", title="Test Plan", subtitle=target, logo_path=logo,
        stat_cards=[
            ("Planned cases", str(total_cases), S.INK),
            ("Pages in scope", str(len(pages)), S.INK),
            ("Est. run time", f"~{round(est_min) or 2}m", S.INK),
        ] if (total_cases or pages) else None,
        meta_rows=[
            ("Test Plan Identifier", f"TP-{project.key}-{datetime.now(UTC).strftime('%Y%m%d')}"),
            ("Project", project.name),
            ("Target under test", target),
            ("Prepared by", company),
            ("Date", S.today()),
            ("Status", "Draft for review"),
        ],
    )
    S.running_header_footer(doc, doc_kind="Test Plan", company=company)
    S.table_of_contents(doc, sections=[
        "1. Introduction", "2. Scope", "3. Features to be tested", "4. Test approach",
        "5. Entry & exit criteria", "6. Test deliverables", "7. Schedule & effort",
        "8. Risks & assumptions", "9. Approvals",
    ])

    S.heading(doc, "1. Introduction")
    doc.add_paragraph(
        f"This test plan defines the scope, approach, and criteria for testing {target}. "
        "It is generated from an automated exploration of the application under test and "
        "follows a standard test-plan structure so it can be reviewed and signed off before "
        "execution begins.")

    S.heading(doc, "2. Scope")
    doc.add_paragraph("In scope, the following pages were discovered and are covered by this plan:")
    S.bullets(doc, pages[:20] or ["(the application under test)"])
    if plan.get("notes"):
        S.heading(doc, "Out of scope / notes", level=2)
        S.bullets(doc, [str(n) for n in plan["notes"]])

    S.heading(doc, "3. Features to be tested")
    if types:
        t = S.styled_table(doc, ["Test type", "Cases", "Risk"], widths=[3.0, 1.0, 1.6])
        for r in types:
            row = t.add_row()
            _text(row.cells[0], str(r.get("label", r.get("key", ""))))
            _text(row.cells[1], str(r.get("count", 0)))
            risk = str(r.get("risk", "") or "-")
            if risk.lower() in S.RISK_COLOR:
                S.pill(row.cells[2], risk, S.RISK_COLOR[risk.lower()])
            else:
                _text(row.cells[2], risk)
            S.row_rule(row)
    else:
        S.bullets(doc, [str(c) for c in plan.get("functional", ["Functional behaviour of each page"])])

    S.heading(doc, "4. Test approach")
    S.bullets(doc, [
        "Every page is exercised in a real browser (Playwright), no manual scripting required.",
        "Deterministic techniques where the domain is known: boundary-value analysis, "
        "equivalence partitioning, decision tables, and pairwise combinations.",
        "Accessibility is checked against WCAG 2.2 AA; performance against a page-load budget.",
        "Each failure is captured with a full-page screenshot as evidence.",
    ])
    if plan.get("non_functional"):
        S.heading(doc, "Non-functional checks", level=2)
        S.bullets(doc, [str(c) for c in plan["non_functional"]])

    S.heading(doc, "5. Entry & exit criteria")
    S.kv_table(doc, [
        ("Entry criteria", "The target is reachable; the plan is approved by a reviewer."),
        ("Exit criteria", "Every planned case has a recorded result; all failures are "
                          "triaged and either fixed or accepted with a documented reason."),
        ("Pass/fail", "A test passes when its assertion holds with no accessibility, "
                     "console, or 5xx error; otherwise it fails with evidence."),
    ])

    S.heading(doc, "6. Test deliverables")
    S.bullets(doc, [
        "This Test Plan (this document).",
        "The Test Case register (Word / Excel).",
        "The Test Completion Report with per-test results and screenshot evidence.",
        "The Requirements Traceability Matrix (HTML / Excel).",
    ])

    S.heading(doc, "7. Schedule & effort (estimated)")
    S.kv_table(doc, [
        ("Total planned cases", total_cases),
        ("Estimated automated run time", f"~{round(est_min) or 2} minute(s)"),
        ("Re-runs", "Zero-token on unchanged pages (cached locators)."),
    ])

    S.heading(doc, "8. Risks & assumptions")
    S.bullets(doc, [
        "Pages behind authentication may require a login step before they can be tested.",
        "A page that changes structure between runs may need its locators re-healed.",
        "Third-party/off-site content is out of scope.",
    ])

    S.heading(doc, "9. Approvals")
    S.kv_table(doc, [
        ("Prepared by", f"{company} · GaleQEA"),
        ("Reviewed by", ""),
        ("Approved by", ""),
        ("Date", ""),
    ], blank_values={"Reviewed by", "Approved by", "Date"})

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _text(cell, value: str) -> None:
    p = cell.paragraphs[0]
    r = p.add_run(value)
    r.font.size = Pt(10)
    r.font.name = S.FONT_BODY
    r.font.color.rgb = S.INK
