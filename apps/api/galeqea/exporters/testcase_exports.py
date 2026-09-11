"""Portable test-case exporters (WO#9-C).

Zero lock-in, by construction: a team can take GaleQEA's cases into TestRail, Xray,
Qase, a Gherkin suite or a Playwright project without a GaleQEA runtime. Each
exporter renders the *stored* case: its steps, its rationale, and its traceability
(`requirement_refs` and rule-level `covers`) so the link back to the spec survives
the trip.

Everything here is deterministic and needs no model.
"""

from __future__ import annotations

import csv
import io
import json
import re

EXPORT_FORMATS = ("testrail_csv", "testrail_steps_csv", "xray_csv", "qase_json",
                  "gherkin", "playwright")


def _steps(case) -> list:
    return sorted(case.steps, key=lambda s: s.index)


def _refs(case) -> list[str]:
    # Requirement refs plus the finer rule ids the case covers (WO#9-B).
    return list(dict.fromkeys([*(case.requirement_refs or []), *(case.covers or [])]))


# --------------------------------------------------------------------------- #
# TestRail CSV
# --------------------------------------------------------------------------- #
def testrail_csv(cases, *, separated_steps: bool = False) -> str:
    """TestRail case import CSV.

    ``separated_steps=False`` is the **Text** template: one row per case, the steps
    joined into the ``Steps`` field. ``True`` is the **Steps (separated)** template:
    one row per step, the case columns repeated on the first row of each case.
    """
    buf = io.StringIO()
    if not separated_steps:
        writer = csv.writer(buf)
        writer.writerow(["Title", "Section", "Template", "Type", "Priority",
                         "References", "Preconditions", "Steps", "Expected Result"])
        for case in cases:
            steps = _steps(case)
            writer.writerow([
                case.title,
                (case.provenance or {}).get("section", "") or case.category,
                "Test Case (Text)",
                case.category,
                _tr_priority(case.priority),
                ", ".join(_refs(case)),
                "\n".join(case.preconditions or []),
                "\n".join(f"{i + 1}. {s.intent or s.action}" for i, s in enumerate(steps)),
                "\n".join(s.expected for s in steps if s.expected),
            ])
    else:
        writer = csv.writer(buf)
        writer.writerow(["Title", "Section", "Template", "Priority", "References",
                         "Steps (Step)", "Steps (Expected Result)"])
        for case in cases:
            steps = _steps(case) or [None]
            for i, s in enumerate(steps):
                writer.writerow([
                    case.title if i == 0 else "",
                    ((case.provenance or {}).get("section", "") or case.category) if i == 0 else "",
                    "Test Case (Steps)" if i == 0 else "",
                    _tr_priority(case.priority) if i == 0 else "",
                    ", ".join(_refs(case)) if i == 0 else "",
                    (s.intent or s.action) if s else "",
                    (s.expected or "") if s else "",
                ])
    return buf.getvalue()


def _tr_priority(priority: str) -> str:
    return {"critical": "Critical", "high": "High", "medium": "Medium",
            "low": "Low"}.get(priority, "Medium")


# --------------------------------------------------------------------------- #
# Xray Test Case Importer CSV
# --------------------------------------------------------------------------- #
def xray_csv(cases) -> str:
    """Xray Test Case Importer CSV.

    Manual tests emit one row per step (Action/Data/Result) with the case columns on
    the first row; a case tagged ``cucumber`` emits a single Cucumber-type row whose
    ``Cucumber`` column carries the Gherkin. The ``Tests`` column links coverage back
    to the requirement/rule the case covers ("Tests" is Xray's Test→Requirement link).
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["TCID", "Test Type", "Summary", "Priority", "Labels",
                     "Action", "Data", "Result", "Cucumber", "Tests"])
    for case in cases:
        tests_link = "; ".join(_refs(case))
        labels = " ".join(case.tags or [])
        if "cucumber" in (case.tags or []) or "pairwise" in (case.tags or []):
            writer.writerow([case.key, "Cucumber", case.title, _xr_priority(case.priority),
                             labels, "", "", "", gherkin_feature(case), tests_link])
            continue
        steps = _steps(case) or [None]
        for i, s in enumerate(steps):
            writer.writerow([
                case.key if i == 0 else "",
                "Manual" if i == 0 else "",
                case.title if i == 0 else "",
                _xr_priority(case.priority) if i == 0 else "",
                labels if i == 0 else "",
                (s.intent or s.action) if s else "",
                json.dumps(s.value) if (s and s.value) else "",
                (s.expected or "") if s else "",
                "",
                tests_link if i == 0 else "",
            ])
    return buf.getvalue()


def _xr_priority(priority: str) -> str:
    return {"critical": "Highest", "high": "High", "medium": "Medium",
            "low": "Low"}.get(priority, "Medium")


# --------------------------------------------------------------------------- #
# Qase JSON
# --------------------------------------------------------------------------- #
def qase_json(cases) -> str:
    """Qase test-case import JSON (a list of cases with steps and metadata)."""
    _prio = {"critical": "high", "high": "high", "medium": "medium", "low": "low"}
    out = []
    for case in cases:
        out.append({
            "title": case.title,
            "description": case.rationale or case.description,
            "preconditions": "\n".join(case.preconditions or []),
            "priority": _prio.get(case.priority, "medium"),
            "severity": _prio.get(case.risk, "medium"),
            "type": "functional",
            "is_flaky": bool(getattr(case, "flake_score", 0) and case.flake_score > 0.2),
            "tags": list(case.tags or []),
            "references": _refs(case),
            "steps": [
                {"action": s.intent or s.action, "expected_result": s.expected or "",
                 "data": json.dumps(s.value) if s.value else ""}
                for s in _steps(case)
            ],
        })
    return json.dumps({"cases": out}, indent=2)


# --------------------------------------------------------------------------- #
# Gherkin .feature, with an Examples table from BVA / pairwise rows
# --------------------------------------------------------------------------- #
def gherkin_feature(case) -> str:
    tags = case.tags or ["galeqea"]
    lines = [
        f"# Generated by GaleQEA from {case.key}",
        f"@{' @'.join(tags)}",
        f"Feature: {case.title}",
        f"  {case.rationale}" if case.rationale else "  ",
        "",
    ]
    for ref in _refs(case):
        lines.append(f"  # covers: {ref}")

    examples = _examples_table(case)
    if examples:
        header, rows = examples
        lines.append(f"  Scenario Outline: {case.title}")
        for pre in case.preconditions or []:
            lines.append(f"    Given {pre}")
        lines.append(f"    When the input <{header[0]}> is applied")
        if len(header) > 1:
            lines.append(f"    Then the outcome is <{header[-1]}>")
        else:
            lines.append("    Then the system behaves as specified")
        lines.append("")
        lines.append("    Examples:")
        lines.append("      | " + " | ".join(header) + " |")
        for row in rows:
            lines.append("      | " + " | ".join(str(c) for c in row) + " |")
    else:
        lines.append(f"  Scenario: {case.title}")
        for pre in case.preconditions or []:
            lines.append(f"    Given {pre}")
        keyword = "When"
        for step in _steps(case):
            if str(step.action).startswith("expect"):
                lines.append(f"    Then {step.expected or step.intent}")
            else:
                lines.append(f"    {keyword} {step.intent}")
                keyword = "And"
    return "\n".join(lines) + "\n"


def _examples_table(case) -> tuple[list[str], list[list]] | None:
    """Build a Gherkin Examples table from a case's stored BVA/pairwise rows."""
    data = case.test_data or {}
    # Pairwise / decision rows: dict per row.
    rows = data.get("rows")
    if rows and isinstance(rows, list) and isinstance(rows[0], dict):
        # decision-table rows carry {"conditions": {...}, "expected": ...}
        if "conditions" in rows[0]:
            header = list(rows[0]["conditions"].keys()) + ["expected"]
            return header, [[*r["conditions"].values(), r.get("expected", "")] for r in rows]
        header = list(rows[0].keys())
        return header, [[r.get(h, "") for h in header] for r in rows]
    # Boundary / partition values: valid + invalid lists of TestValue dicts.
    values = (data.get("valid") or []) + (data.get("invalid") or [])
    if values:
        header = ["value", "label", "partition", "expected"]
        return header, [[v.get("value", ""), v.get("label", ""), v.get("partition", ""),
                         v.get("expected", "")] for v in values]
    return None


# --------------------------------------------------------------------------- #
# Round-trip: stale-case detection against a new requirement set
# --------------------------------------------------------------------------- #
_REF_RX = re.compile(r"\b([A-Z][A-Z0-9]{1,14}-\d+(?:\.\d+)*(?:-R\d+)?)\b")
_REF_COLUMNS = ("references", "tests", "requirement", "requirements", "coverage", "covers", "links")


def parse_imported_refs(content: str, filename: str) -> list[dict]:
    """Extract (key, title, refs) from an existing TestRail/Xray CSV export or a
    Gherkin .feature, for round-trip stale-case analysis (WO#9-C)."""
    name = filename.lower()
    if name.endswith(".feature") or "Feature:" in content[:200]:
        cases: list[dict] = []
        current = None
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith(("Scenario:", "Scenario Outline:")):
                if current:
                    cases.append(current)
                current = {"key": "", "title": stripped.split(":", 1)[1].strip(), "refs": []}
            elif current is not None and (
                "covers" in stripped.lower() or stripped.startswith("@")
                or "requirement" in stripped.lower()
            ):
                current["refs"] += [r.replace(" ", "").upper() for r in _REF_RX.findall(stripped)]
        if current:
            cases.append(current)
        for c in cases:
            c["refs"] = list(dict.fromkeys(c["refs"]))
        return cases

    # CSV (TestRail / Xray importer shapes).
    reader = csv.DictReader(io.StringIO(content))
    out: list[dict] = []
    for row in reader:
        lowered = {(k or "").strip().lower(): (v or "") for k, v in row.items()}
        title = lowered.get("title") or lowered.get("summary") or lowered.get("name") or ""
        key = lowered.get("tcid") or lowered.get("id") or lowered.get("key") or ""
        refs: list[str] = []
        for col in _REF_COLUMNS:
            if lowered.get(col):
                refs += [r.replace(" ", "").upper() for r in _REF_RX.findall(lowered[col])]
        if title or key or refs:
            out.append({"key": key, "title": title, "refs": list(dict.fromkeys(refs))})
    return out


def stale_cases(imported_refs: list[dict], current_refs: set[str]) -> dict:
    """Compare cases from an imported TMS export against the current requirements.

    ``imported_refs`` is ``[{"key","title","refs":[...]}]`` (a case and the refs it
    claims to cover). Returns the cases whose every referenced requirement has
    vanished (**stale**, the spec moved on), the cases that reference nothing
    (**unlinked**), and the current requirements no imported case covers (**newly
    uncovered**). This is what makes "did the PRD change break my suite?" answerable.
    """
    current = {r.upper() for r in current_refs}
    stale, unlinked = [], []
    covered: set[str] = set()
    for case in imported_refs:
        refs = {r.upper() for r in (case.get("refs") or [])}
        if not refs:
            unlinked.append(case.get("key") or case.get("title"))
            continue
        covered |= refs & current
        if not (refs & current):
            stale.append({"key": case.get("key") or case.get("title"),
                          "orphaned_refs": sorted(refs)})
    newly_uncovered = sorted(current - covered)
    return {"stale": stale, "unlinked": unlinked, "newly_uncovered": newly_uncovered,
            "summary": {"stale": len(stale), "unlinked": len(unlinked),
                        "newly_uncovered": len(newly_uncovered)}}


# --------------------------------------------------------------------------- #
def export_cases(cases, fmt: str, *, base_url: str = "") -> tuple[str, str]:
    """Render a list of cases in ``fmt``; returns (content, filename)."""
    if fmt == "testrail_csv":
        return testrail_csv(cases), "testrail-import.csv"
    if fmt == "testrail_steps_csv":
        return testrail_csv(cases, separated_steps=True), "testrail-steps-import.csv"
    if fmt == "xray_csv":
        return xray_csv(cases), "xray-import.csv"
    if fmt == "qase_json":
        return qase_json(cases), "qase-import.json"
    if fmt == "gherkin":
        return "\n".join(gherkin_feature(c) for c in cases), "tests.feature"
    if fmt == "playwright":
        from ..engine.codegen import render
        return ("\n\n".join(render(c, target="playwright", base_url=base_url) for c in cases),
                "tests.spec.ts")
    raise ValueError(f"unknown export format: {fmt!r} (one of {', '.join(EXPORT_FORMATS)})")
