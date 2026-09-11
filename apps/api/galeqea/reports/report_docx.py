"""Professional Word (.docx) Test Completion Report: branded, client-ready.

The artefact a QA team hands to a stakeholder: a cover with the company mark and
the headline pass rate, a table of contents, an executive summary, the scope and
test plan, a test-case register, and (the part that makes it credible) every
failure with its error and a framed screenshot as evidence. Built deterministically
with python-docx from a finished run; the visual system lives in ``docx_style``.
"""

from __future__ import annotations

import io
import os

from docx.shared import Pt

from ..models import Artifact
from . import docx_style as S
from .runs import build_run_report

_PASSING = {"passed", "flaky"}
_SKIPPED = {"skipped", "blocked"}


def build_run_docx(db, project, run, *, company: str = "", target: str = "",
                   pages: list[str] | None = None, plan: dict | None = None) -> bytes:
    report = build_run_report(db, project, run)
    summary = report["summary"]
    items = report["results"]
    settings_meta = project.settings or {}
    company = company or settings_meta.get("report_company") or "GaleQEA"
    logo_path = settings_meta.get("report_logo_path") or S.DEFAULT_LOGO
    target = target or run.base_url or (project.environments or {}).get(
        project.default_environment, "") or "-"

    overall = str(run.status).replace("_", " ").title()
    passed, failed = summary.get("passed", 0), summary.get("failed", 0)
    skipped = summary.get("skipped", 0)
    total = summary.get("total", len(items))
    pass_rate = round(100 * passed / total) if total else 0
    rate_color = S.PASS if (total and failed == 0) else S.FAIL if (total and pass_rate < 80) else S.WARN

    doc = S.base_document(title="Test Completion Report", subject=target, author=company)

    # --- cover --------------------------------------------------------------- #
    S.cover(
        doc, kicker="Test Completion Report", title="Test Completion Report",
        subtitle=target, logo_path=logo_path,
        hero=(f"{'pass rate' if total else 'no tests run'}", f"{pass_rate}%" if total else "-", rate_color),
        stat_cards=[
            ("Total", str(total), S.INK),
            ("Passed", str(passed), S.PASS),
            ("Failed", str(failed), S.FAIL if failed else S.MUTED),
            ("Skipped", str(skipped), S.MUTED),
        ] if total else None,
        meta_rows=[
            ("Project", project.name),
            ("Run", f"#{run.number}"),
            ("Overall result", overall),
            ("Environment", run.environment or "-"),
            ("Prepared by", company),
            ("Date", S.today()),
        ],
    )
    S.running_header_footer(doc, doc_kind="Test Completion Report", company=company)
    S.table_of_contents(doc, sections=[
        "1. Executive summary", "2. Scope & approach", "3. Results by test type",
        "4. Test cases executed", "5. Failures & evidence", "6. Conclusion & sign-off",
    ])

    # --- executive summary ---------------------------------------------------- #
    S.heading(doc, "1. Executive summary")
    verdict = ("All tests passed. The target met the acceptance criteria for this run."
               if failed == 0 and total
               else f"{failed} of {total} test(s) failed and require attention before release."
               if total else "No tests were executed in this run.")
    doc.add_paragraph(verdict)
    if total:
        S.donut_with_legend(
            doc, [("Passed", passed, S.PASS_HEX), ("Failed", failed, S.FAIL_HEX),
                 ("Skipped", skipped, "9AA3B2")],
            center_value=f"{pass_rate}%", center_label="pass rate",
            caption="Share of the run by result")

    # --- scope ----------------------------------------------------------------- #
    S.heading(doc, "2. Scope & approach")
    pages = pages or (plan or {}).get("pages") or []
    doc.add_paragraph(
        f"GaleQEA drove a real browser against {target}. "
        + (f"{len(pages)} page(s) were analysed and exercised:" if pages
           else "The suite below was executed against the target."))
    if pages:
        S.bullets(doc, pages[:12])
    if plan and plan.get("functional"):
        S.heading(doc, "Test plan: functional checks", level=2)
        S.bullets(doc, [str(c) for c in plan["functional"][:8]])
    if plan and plan.get("non_functional"):
        S.heading(doc, "Test plan: non-functional checks", level=2)
        S.bullets(doc, [str(c) for c in plan["non_functional"][:8]])

    # --- results by type --------------------------------------------------- #
    S.heading(doc, "3. Results by test type")
    by_type = report.get("by_test_type", {})
    if by_type:
        S.bar_chart_with_legend(
            doc,
            [(str(ty).replace("_", " ").title(),
              [(g.get("passed", 0), S.PASS_HEX), (g.get("failed", 0), S.FAIL_HEX),
               (g.get("skipped", 0), "9AA3B2")])
             for ty, g in sorted(by_type.items())],
            caption="Each bar is that type's own pass/fail/skip share; the number is its total.")
    t = S.styled_table(doc, ["Type", "Total", "Passed", "Failed", "Skipped"],
                       widths=[2.3, 1.0, 1.0, 1.0, 1.0])
    for ty, g in sorted(by_type.items()):
        S.data_row(t, [str(ty).replace("_", " ").title(), g.get("total", 0),
                       g.get("passed", 0), g.get("failed", 0), g.get("skipped", 0)])

    # --- test-case register ------------------------------------------------ #
    S.heading(doc, "4. Test cases executed")
    t = S.styled_table(doc, ["ID", "Test case", "Type", "Result", "Duration"],
                       widths=[1.35, 2.35, 0.9, 0.8, 0.9])
    for it in items:
        row = t.add_row()
        _cell_text(row.cells[0], it["key"])
        _cell_text(row.cells[1], it["title"][:70])
        _cell_text(row.cells[2], str(it["test_type"]).replace("_", " "))
        color = S.PASS_HEX if it["status"] in _PASSING else (
            "9AA3B2" if it["status"] in _SKIPPED else S.FAIL_HEX)
        S.pill(row.cells[3], str(it["status"]), color)
        _cell_text(row.cells[4], f"{(it['duration_ms'] or 0) / 1000:.1f}s")
        S.row_rule(row)

    # --- requirements coverage (when a requirement doc drove this) --------- #
    _requirements_section(db, doc, project)

    # --- failures with evidence ------------------------------------------- #
    failures = [it for it in items if it["status"] not in _PASSING and it["status"] not in _SKIPPED]
    S.heading(doc, "5. Failures & evidence")
    if not failures:
        p = doc.add_paragraph()
        r = p.add_run("No failures. Every executed test passed.")
        r.font.color.rgb = S.PASS
        r.bold = True
    else:
        for it in failures:
            S.heading(doc, f"{it['key']}: {it['title'][:80]}", level=2)
            err = it.get("error") or {}
            if err:
                S.callout(doc, f"{err.get('type', '')}: {err.get('message', '')}"[:400],
                          label="Error")
            shot = _screenshot_for(db, it["id"])
            if shot:
                try:
                    S.framed_picture(doc, shot, caption=f"Evidence: {it['key']}")
                except Exception:  # noqa: BLE001  (a corrupt image never breaks the report)
                    pass

    # --- sign-off ---------------------------------------------------------- #
    S.heading(doc, "6. Conclusion & sign-off")
    doc.add_paragraph(verdict)
    S.kv_table(doc, [
        ("Prepared by", f"{company} · GaleQEA"),
        ("Report generated", f"{S.today()}"),
        ("Reviewed by", ""),
        ("Approved for release", "☐ Yes    ☐ No"),
    ], blank_values={"Reviewed by"})

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _cell_text(cell, text: str, size: float = 9.5) -> None:
    p = cell.paragraphs[0]
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(2)
    r = p.add_run(text)
    r.font.size = Pt(size)
    r.font.name = S.FONT_BODY
    r.font.color.rgb = S.INK


def _requirements_section(db, doc, project) -> None:
    """A 'Requirements coverage' section from the RTM, shown when the project has an
    ingested requirement document, so an uploaded spec is reflected in the report."""
    try:
        from ..intelligence.coverage import traceability_matrix
        matrix = traceability_matrix(db, project.id)
    except Exception:  # noqa: BLE001
        return
    if not matrix:
        return
    S.heading(doc, "Requirements coverage", level=1)
    rules_total = sum(r.get("rules_total", 0) for r in matrix)
    rules_covered = sum(r.get("rules_covered", 0) for r in matrix)
    covered = sum(1 for r in matrix if r.get("covered"))
    gap = len(matrix) - covered
    cov_pct = round(100 * covered / len(matrix)) if matrix else 0
    doc.add_paragraph(
        f"{covered}/{len(matrix)} requirement(s) covered by an approved test; "
        f"{rules_covered}/{rules_total} atomic rule(s) covered. Traced from the uploaded "
        "requirement document.")
    S.donut_with_legend(
        doc, [("Covered", covered, S.PASS_HEX), ("Gap", gap, S.FAIL_HEX)],
        center_value=f"{cov_pct}%", center_label="covered",
        caption="Requirement coverage")
    t = S.styled_table(doc, ["Requirement", "Priority", "Rules covered", "Status"],
                       widths=[3.6, 0.9, 1.1, 0.9])
    for row in matrix[:40]:
        r = t.add_row()
        _cell_text(r.cells[0], f"{row['ref']} {row['title'][:56]}")
        _cell_text(r.cells[1], str(row.get("priority", "")))
        _cell_text(r.cells[2], f"{row.get('rules_covered', 0)}/{row.get('rules_total', 0)}")
        S.pill(r.cells[3], "covered" if row.get("covered") else "gap",
              S.PASS_HEX if row.get("covered") else S.FAIL_HEX)
        S.row_rule(r)


def _screenshot_for(db, run_test_id: str) -> str | None:
    """A local screenshot path for a result, if one exists on disk."""
    from sqlalchemy import select
    art = db.execute(
        select(Artifact).where(Artifact.run_test_id == run_test_id,
                               Artifact.kind == "screenshot")
        .order_by(Artifact.created_at.desc())
    ).scalars().first()
    if art and art.path and os.path.exists(art.path):
        return art.path
    return None
