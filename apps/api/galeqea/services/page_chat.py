"""Page-aware chat: answer questions about whatever page the user is looking at.

"How many test cases are there?", "what failed?", "summarise this run". The client
tells the agent which page (route) is open, and this answers from the database, scoped
to that page, with no model required. Anything it can't answer deterministically it
declines (returns None) so the normal chat/model path handles it.
"""

from __future__ import annotations

import re

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import Project, Run, RunTest, TestCase, User
from ..reports.common import api_href

#: Questions this handler recognises: a count/summary/status ask about the page.
_ASKY = re.compile(
    r"\b(?:how many|how much|count|number of|summar|overview|what'?s (?:on|here|this)|"
    r"what (?:is|are) (?:on|here|this|there)|list|show me|status|what failed|"
    r"which (?:failed|passed)|pass rate|breakdown|tell me about (?:this|the))", re.I)


def _route(page: str) -> tuple[str, str | None]:
    """(section, id) from a route like '/runs/abc123' -> ('runs', 'abc123')."""
    parts = [p for p in (page or "").strip("/").split("/") if p]
    if not parts:
        return "workspace", None
    return parts[0], (parts[1] if len(parts) > 1 else None)


_EXPORT = re.compile(r"\b(export|download|save|generate)\b", re.I)
_FMT = {"excel": "xlsx", "xlsx": "xlsx", "spreadsheet": "xlsx",
        "word": "docx", "docx": "docx", "document": "docx"}
#: "is there a sample?" / "load the template": the requirements page's own
#: one-click demo, discoverable from chat as well as the button.
_SAMPLE = re.compile(
    r"\b(sample|template|example)\b.*\b(requirement|spec|doc)|"
    r"\b(try|load|use)\b.*\b(sample|template)\b", re.I)


def try_handle(db: Session, project: Project, text: str, user: User,
               page: str | None) -> tuple[str, list[dict]] | None:
    section, ident = _route(page or "")
    if section == "requirements" and _SAMPLE.search(text):
        return _sample_pointer(project)
    if _EXPORT.search(text):
        out = _export(project, text, page)
        if out is not None:
            return out
    if not page or not _ASKY.search(text):
        return None
    if section in ("tests",):
        return _tests(db, project, text)
    if section in ("runs",):
        return _run_detail(db, project, ident, text) if ident else _runs(db, project, text)
    if section in ("requirements",):
        return _requirements(db, project, text)
    if section in ("", "workspace", "dashboard"):
        return _overview(db, project)
    return None


def _tests(db, project, text):
    rows = list(db.execute(
        select(TestCase).where(TestCase.project_id == project.id)).scalars())
    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for c in rows:
        by_status[c.status] = by_status.get(c.status, 0) + 1
        ty = (c.provenance or {}).get("technique") or c.category
        by_type[ty] = by_type.get(ty, 0) + 1
    status_line = ", ".join(f"{n} {s}" for s, n in sorted(by_status.items()))
    top = ", ".join(f"{t} ({n})" for t, n in sorted(by_type.items(), key=lambda x: -x[1])[:5])
    return (f"There are **{len(rows)} test case(s)** on this page: {status_line or 'none yet'}. "
            f"By technique/category: {top or 'none'}. "
            "You can export them as **Word or Excel** (Export ▾), or ask me to filter, "
            "approve, or regenerate any of them.",
            [{"type": "page_answer", "page": "tests", "total": len(rows),
              "by_status": by_status, "by_type": by_type}])


def _runs(db, project, text):
    rows = list(db.execute(
        select(Run).where(Run.project_id == project.id)
        .order_by(Run.created_at.desc()).limit(10)).scalars())
    if not rows:
        return ("No runs yet on this page. Ask me to test a site or run a suite to create one.", [])
    latest = rows[0]
    counts: dict[str, int] = {}
    for r in rows:
        counts[r.status] = counts.get(r.status, 0) + 1
    line = ", ".join(f"{n} {s}" for s, n in counts.items())
    return (f"The **{len(rows)}** most recent run(s): {line}. "
            f"Latest is **run #{latest.number}** ({latest.status}) against "
            f"{latest.base_url or latest.environment or 'an unspecified target'}. Ask me to open it, re-run "
            "failures, or export its Test Completion Report (Word/Excel).",
            [{"type": "page_answer", "page": "runs", "count": len(rows), "latest": latest.number}])


def _run_detail(db, project, run_id, text):
    run = db.get(Run, run_id)
    if run is None or run.project_id != project.id:
        return None
    results = list(db.execute(select(RunTest).where(RunTest.run_id == run.id)).scalars())
    total = len(results)
    passed = sum(1 for r in results if r.status in ("passed", "flaky"))
    failed = sum(1 for r in results if r.status not in ("passed", "flaky", "skipped", "blocked"))
    fails = [r for r in results if r.status not in ("passed", "flaky", "skipped", "blocked")]
    fail_line = "; ".join(f"{r.test_key} ({(r.error_type or 'failed')})" for r in fails[:6])
    ask_fail = re.search(r"fail", text, re.I)
    body = (f"**Run #{run.number}** ({run.status}): {passed}/{total} passed"
            + (f", {failed} failed" if failed else "") + ". ")
    if ask_fail and fails:
        body += f"Failures: {fail_line}. "
    body += ("Export the **Test Completion Report** as Word (`report.docx`) or "
             "Excel (`report.xlsx`). Every failure carries a screenshot.")
    return (body, [{"type": "page_answer", "page": "run", "run": run.number,
                    "total": total, "passed": passed, "failed": failed}])


def _requirements(db, project, text):
    from ..intelligence.coverage import traceability_matrix
    matrix = traceability_matrix(db, project.id)
    covered = sum(1 for r in matrix if r.get("covered"))
    rules_total = sum(r.get("rules_total", 0) for r in matrix)
    rules_covered = sum(r.get("rules_covered", 0) for r in matrix)
    return (f"**{len(matrix)} requirement(s)** on this page: {covered} covered by an "
            f"approved test, {rules_covered}/{rules_total} atomic rules covered. "
            "Export the Requirements Traceability Matrix as **HTML or Excel**, or ask me "
            "to generate tests for any requirement.",
            [{"type": "page_answer", "page": "requirements", "requirements": len(matrix),
              "covered": covered, "rules_total": rules_total, "rules_covered": rules_covered}])


def _sample_pointer(project) -> tuple[str, list[dict]]:
    template_url = api_href(project.id, "requirements", "template")
    return (
        "There's a bundled **sample requirements doc** (a small task-tracker spec): "
        f"[download it]({template_url}), or use **Run the full floor with the sample** "
        "above to ingest it, generate its test cases, and run the whole pipeline "
        "(Test Plan, execution, Test Completion Report) in one go.",
        [{"type": "page_answer", "page": "requirements", "action": "sample_template",
          "template_url": template_url}],
    )


def _overview(db, project):
    tests = db.execute(select(func.count()).select_from(TestCase)
                       .where(TestCase.project_id == project.id)).scalar_one()
    runs = db.execute(select(func.count()).select_from(Run)
                      .where(Run.project_id == project.id)).scalar_one()
    return (f"Project **{project.name}**: {tests} test case(s), {runs} run(s). "
            "Tell me a website URL to test, or ask about your tests, runs, or requirements.",
            [{"type": "page_answer", "page": "overview", "tests": tests, "runs": runs}])


def _export(project, text: str, page: str | None) -> tuple[str, list[dict]] | None:
    """Turn "export <thing> as excel/word" into a ready download link, page-aware."""
    low = text.lower()
    fmt = next((v for k, v in _FMT.items() if k in low), None)
    section, ident = _route(page or "")

    # What to export: explicit words win; otherwise infer from the open page.
    if "test plan" in low or "testplan" in low:
        url = api_href(project.id, "test-plan.docx")
        return ("Here's the Test Plan, IEEE-829 style.",
                [{"type": "export", "artifact": "test_plan", "format": "docx", "url": url}])
    if "traceab" in low or "matrix" in low or "rtm" in low:
        ext = "xlsx" if fmt == "xlsx" else "html"
        url = api_href(project.id, "traceability", f"report.{ext}")
        return ("Here's the Requirements Traceability Matrix.",
                [{"type": "export", "artifact": "rtm", "format": ext, "url": url}])
    if "test case" in low or "cases" in low or section == "tests":
        ext = fmt or "xlsx"
        url = (api_href(project.id, "tests", "export.xlsx") if ext == "xlsx"
               else api_href(project.id, "tests", "export.bulk") + "?format=playwright")
        if ext == "docx":
            # cases as a document == the plan; steer to the register in Excel/Word.
            url = api_href(project.id, "tests", "export.xlsx")
            ext = "xlsx"
        return ("Here's the Test Case register.",
                [{"type": "export", "artifact": "test_cases", "format": ext, "url": url}])
    if "report" in low or "tcr" in low or section == "runs":
        run_id = ident if section == "runs" and ident else "latest"
        if run_id == "latest":
            return ("Open the run you want and ask again, or say 'export run <number> "
                    "as word/excel' and I'll give you the Test Completion Report.", [])
        ext = fmt or "docx"
        url = api_href(project.id, "runs", run_id, f"report.{ext}")
        return ("Here's the Test Completion Report for this run.",
                [{"type": "export", "artifact": "tcr", "format": ext, "url": url}])
    return None


def describe_page(page: str | None) -> str:
    """A one-line context string about the open page, for the model prompt."""
    if not page:
        return ""
    section, ident = _route(page)
    label = {"tests": "the Test Cases review board", "runs": "the Runs list",
             "requirements": "the Requirements / traceability view",
             "intelligence": "the Intelligence (flaky/RCA) view",
             "workspace": "the workspace canvas"}.get(section, f"the {section} page")
    if section == "runs" and ident:
        label = f"the detail of run {ident}"
    return f"The user is currently viewing {label}."
