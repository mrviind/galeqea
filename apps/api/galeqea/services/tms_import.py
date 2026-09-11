"""Import test assets from other tools, as GaleQEA proposals, through the review gate.

Test *cases* (TestRail / Xray / Zephyr CSV, Gherkin ``.feature``) become PROPOSED test
cases carrying their origin as provenance; test *results* (JUnit / Allure / Playwright)
become a historical run. Nothing is trusted blindly: cases land as proposals a human
reviews, and CSV column mapping is proposed first (a guess a person confirms) rather than
assumed.
"""

from __future__ import annotations

import csv
import io
import re
import xml.etree.ElementTree as ET

from sqlalchemy.orm import Session

from ..core import audit
from ..models import Project, Run, RunTest
from ..models.base import utcnow
from ..models.testing import StepAction
from . import requirements as req_service


class TmsImportError(RuntimeError):
    """Safe-to-surface import problem."""


# --------------------------------------------------------------------------- #
# Format sniffing
# --------------------------------------------------------------------------- #
def sniff_format(filename: str, content: str) -> str:
    name = (filename or "").lower()
    head = content[:400].lstrip()
    if name.endswith(".feature") or head.lower().startswith("feature:"):
        return "gherkin"
    if name.endswith(".csv") or ("," in head and "\n" in head and "<" not in head[:1]):
        return "csv"
    if "<testsuite" in content[:2000] or "<testsuites" in content[:2000]:
        return "junit"
    if name.endswith(".json") or head.startswith("{") or head.startswith("["):
        return "allure"  # Allure / Playwright JSON result
    return "unknown"


# --------------------------------------------------------------------------- #
# Gherkin → proposals
# --------------------------------------------------------------------------- #
_STEP_KW = ("given", "when", "then", "and", "but")


def parse_gherkin(text: str) -> list[dict]:
    feature = ""
    proposals: list[dict] = []
    current: dict | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        low = line.lower()
        if low.startswith("feature:"):
            feature = line.split(":", 1)[1].strip()
        elif low.startswith("scenario:") or low.startswith("scenario outline:"):
            if current:
                proposals.append(current)
            current = {"title": line.split(":", 1)[1].strip(), "category": "manual",
                       "source": "gherkin", "rationale": f"From feature: {feature}" if feature else "",
                       "steps": [], "tags": ["imported", "gherkin"]}
        elif current is not None and low.split(" ", 1)[0] in _STEP_KW:
            keyword = low.split(" ", 1)[0]
            # Imported steps are manual instructions (NOTE); a Then line also carries
            # its assertion as the step's expected result.
            current["steps"].append({"action": StepAction.NOTE, "intent": line,
                                     "expected": line if keyword == "then" else ""})
    if current:
        proposals.append(current)
    if not proposals:
        raise TmsImportError("no scenarios found in that .feature file")
    return proposals


# --------------------------------------------------------------------------- #
# CSV → proposals (with a proposed column mapping)
# --------------------------------------------------------------------------- #
_MAP_HINTS = {
    "title": ("title", "name", "summary", "test case", "case"),
    "steps": ("steps", "step", "steps (step)", "test steps", "action"),
    "expected": ("expected", "expected result", "result", "steps (expected result)"),
    "priority": ("priority", "importance"),
    "tags": ("tags", "labels", "component"),
}


def csv_headers(content: str) -> list[str]:
    reader = csv.reader(io.StringIO(content))
    for row in reader:
        return [h.strip() for h in row]
    return []


def guess_mapping(headers: list[str]) -> dict:
    lower = {h.lower(): h for h in headers}
    mapping = {}
    for field, hints in _MAP_HINTS.items():
        for hint in hints:
            if hint in lower:
                mapping[field] = lower[hint]
                break
    return mapping


def parse_csv(content: str, mapping: dict) -> list[dict]:
    if not mapping.get("title"):
        raise TmsImportError("a 'title' column mapping is required")
    proposals: list[dict] = []
    for row in csv.DictReader(io.StringIO(content)):
        title = (row.get(mapping["title"]) or "").strip()
        if not title:
            continue
        steps = []
        raw_steps = (row.get(mapping.get("steps", "")) or "").strip()
        expected = (row.get(mapping.get("expected", "")) or "").strip()
        for i, line in enumerate([s for s in re.split(r"\r?\n|\d+\.\s", raw_steps) if s.strip()]):
            steps.append({"action": StepAction.NOTE, "intent": line.strip(),
                          "expected": expected if i == 0 else ""})
        tags = [t.strip() for t in (row.get(mapping.get("tags", "")) or "").split(",") if t.strip()]
        proposals.append({
            "title": title, "category": "manual", "source": "csv",
            "priority": (row.get(mapping.get("priority", "")) or "medium").strip().lower() or "medium",
            "tags": ["imported", *tags], "steps": steps,
            "rationale": "Imported from a CSV test export.",
        })
    if not proposals:
        raise TmsImportError("no rows with a title were found")
    return proposals


# --------------------------------------------------------------------------- #
# JUnit results → a historical run
# --------------------------------------------------------------------------- #
def parse_junit(xml: str) -> list[dict]:
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise TmsImportError(f"could not parse JUnit XML: {exc}") from exc
    cases = []
    for tc in root.iter("testcase"):
        status = "passed"
        message = ""
        failure = tc.find("failure") if tc.find("failure") is not None else tc.find("error")
        if failure is not None:
            status = "failed"
            message = failure.get("message", "") or (failure.text or "")[:500]
        elif tc.find("skipped") is not None:
            status = "skipped"
        name = tc.get("name", "")
        cases.append({"name": name, "classname": tc.get("classname", ""),
                      "status": status, "message": message,
                      "duration_ms": int(float(tc.get("time", 0) or 0) * 1000)})
    if not cases:
        raise TmsImportError("no <testcase> elements found")
    return cases


# --------------------------------------------------------------------------- #
# Persisters
# --------------------------------------------------------------------------- #
def import_cases(db: Session, project: Project, *, proposals: list[dict], actor,
                 source: str) -> dict:
    created = req_service.persist_proposals(db, project_id=project.id, proposals=proposals,
                                            author_kind="import")
    audit.record(db, action="tests.imported", actor_id=getattr(actor, "id", None),
                 actor_label=getattr(actor, "email", ""), project_id=project.id,
                 resource_type="test_case", resource_id="",
                 detail={"source": source, "count": len(created)})
    return {"ok": True, "source": source, "count": len(created),
            "created": [{"id": c.id, "key": c.key, "title": c.title} for c in created]}


def import_results_as_run(db: Session, project: Project, *, results: list[dict], source: str,
                          title: str = "") -> dict:
    from sqlalchemy import func, select
    number = (db.execute(select(func.count()).select_from(Run)
                         .where(Run.project_id == project.id)).scalar_one() or 0) + 1
    passed = sum(1 for r in results if r["status"] == "passed")
    failed = sum(1 for r in results if r["status"] == "failed")
    run = Run(project_id=project.id, number=number, title=title or f"Imported {source} results",
              trigger="import", status="failed" if failed else "passed",
              finished_at=utcnow(),
              totals={"total": len(results), "passed": passed, "failed": failed,
                      "skipped": len(results) - passed - failed})
    db.add(run)
    db.flush()
    for r in results:
        db.add(RunTest(run_id=run.id, test_case_id="", test_key=(r.get("name") or "")[:48],
                       title=(r.get("name") or "")[:400], status=r["status"],
                       error_message=r.get("message", ""), finished_at=utcnow(),
                       duration_ms=r.get("duration_ms", 0)))
    db.flush()
    audit.record(db, action="results.imported", project_id=project.id,
                 resource_type="run", resource_id=run.id,
                 detail={"source": source, "count": len(results)})
    return {"ok": True, "source": source, "run_id": run.id, "run_number": run.number,
            "count": len(results), "passed": passed, "failed": failed}


# --------------------------------------------------------------------------- #
# One entry point that routes by format
# --------------------------------------------------------------------------- #
def import_file(db: Session, project: Project, *, filename: str, content: str, actor,
                mapping: dict | None = None, fmt: str = "") -> dict:
    fmt = fmt or sniff_format(filename, content)
    if fmt == "gherkin":
        return import_cases(db, project, proposals=parse_gherkin(content), actor=actor,
                            source="gherkin")
    if fmt == "csv":
        if not mapping:
            headers = csv_headers(content)
            return {"ok": False, "needs_mapping": True, "headers": headers,
                    "suggested": guess_mapping(headers)}
        return import_cases(db, project, proposals=parse_csv(content, mapping), actor=actor,
                            source="csv")
    if fmt == "junit":
        return import_results_as_run(db, project, results=parse_junit(content), source="junit")
    raise TmsImportError(f"unsupported or unrecognised format {fmt!r} "
                         "(supported: gherkin .feature, CSV, JUnit XML)")
