"""The run report: a single run in three shapes.

Grouped by *test type* (functional, a11y, perf, visual, …) so a release-readiness
read is one glance, and carrying a cost block that makes the promise visible: a
pure execution run calls the model zero times and costs zero; re-running what was
already built is free. The JUnit rendering drops straight into any CI pipeline.
"""

from __future__ import annotations

from sqlalchemy import select

from ..models import RCAReport, RunStepRecord, RunTest, TestCase
from ..models.appmodel import HealEvent
from .common import (
    api_href,
    cap_markdown,
    envelope,
    md_table,
    strip_control_chars,
    ui_href,
    xml_attr,
    xml_text,
)

#: Heal strategies that actually call a model, the only model spend inside a run.
_MODEL_HEAL_STRATEGIES = {"semantic_llm", "vision_model"}

_PASSING = {"passed", "flaky"}
_SKIPPED = {"skipped", "blocked"}


def _test_type(case: TestCase | None, key: str = "") -> str:
    from ..services.test_plan import golden_path_type
    return golden_path_type(case, key)


def _unit(case: TestCase | None) -> str:
    """The target unit a test covers (a page path, form, endpoint …) for the row."""
    prov = getattr(case, "provenance", None) or {} if case else {}
    return prov.get("unit") or ""


def _detail_name(item: dict) -> str:
    """`key · unit · label`, so a reader sees which page/form failed without
    opening the test."""
    from ..services.test_plan import type_label
    parts = [item["key"] or item["title"]]
    if item.get("unit"):
        parts.append(item["unit"])
    parts.append(type_label(item["test_type"]))
    return " · ".join(p for p in parts if p)


def build_run_report(db, project, run) -> dict:
    """The canonical JSON report for one run. Deterministic; ordered by test key."""
    results = list(db.execute(
        select(RunTest).where(RunTest.run_id == run.id).order_by(RunTest.test_key)
    ).scalars())
    case_ids = [r.test_case_id for r in results if r.test_case_id]
    cases = {c.id: c for c in db.execute(
        select(TestCase).where(TestCase.id.in_(case_ids))
    ).scalars()} if case_ids else {}

    steps_by_test: dict[str, list] = {}
    if results:
        for st in db.execute(
            select(RunStepRecord)
            .where(RunStepRecord.run_test_id.in_([r.id for r in results]))
            .order_by(RunStepRecord.index)
        ).scalars():
            steps_by_test.setdefault(st.run_test_id, []).append(st)

    items = []
    for r in results:
        case = cases.get(r.test_case_id)
        items.append({
            "id": r.id,
            "key": r.test_key,
            "title": strip_control_chars(r.title) if r.title else r.title,
            "test_type": _test_type(case, r.test_key),
            "unit": _unit(case),
            "status": r.status,
            "browser": r.browser,
            "duration_ms": r.duration_ms,
            "classification": r.classification or None,
            "manual": bool((r.metrics or {}).get("manual")),
            "executed_by": (r.metrics or {}).get("executed_by_name", "") if (r.metrics or {}).get("manual") else "",
            "healed": bool(r.healed),
            "signature": r.failure_signature or None,
            "error": {"type": r.error_type, "message": strip_control_chars(r.error_message)}
            if r.error_message else None,
            "requirement_refs": list(getattr(case, "requirement_refs", None) or []),
            "steps": [
                {"index": s.index, "action": s.action, "intent": s.intent}
                for s in steps_by_test.get(r.id, [])
            ],
            "href": api_href(project.id, "runs", run.id, "results", r.id, "steps"),
            "ui_href": ui_href("runs", run.id),
        })

    # Built-but-unexecuted tests must show as skipped rows with a reason, never
    # silently vanish from the results (see the run board's not_run).
    items.extend(_skipped_rows(db, project, run, {r.test_key for r in results}))

    # Group by test type for the readiness read.
    by_type: dict[str, dict] = {}
    for it in items:
        g = by_type.setdefault(it["test_type"], {"total": 0, "passed": 0, "failed": 0, "skipped": 0})
        g["total"] += 1
        if it["status"] in _PASSING:
            g["passed"] += 1
        elif it["status"] in _SKIPPED:
            g["skipped"] += 1
        else:
            g["failed"] += 1

    passed = sum(1 for it in items if it["status"] in _PASSING)
    failed = sum(1 for it in items if it["status"] not in _PASSING and it["status"] not in _SKIPPED)
    skipped = sum(1 for it in items if it["status"] in _SKIPPED)

    cost = _run_cost(db, [r.id for r in results])
    verdict = _readiness(run.status, failed, by_type)

    return envelope(
        project.id, "run",
        ("runs", run.id, "report.json"),
        {
            "run": {
                "id": run.id, "number": run.number, "title": run.title, "status": run.status,
                "trigger": run.trigger, "environment": run.environment, "base_url": run.base_url,
                "git_sha": run.git_sha, "git_branch": run.git_branch,
                "duration_ms": run.duration_ms,
                "started_at": run.started_at.isoformat() if run.started_at else None,
                "finished_at": run.finished_at.isoformat() if run.finished_at else None,
                "ui_href": ui_href("runs", run.id),
            },
            "readiness": verdict,
            "summary": {
                "total": len(items),  # includes skipped rows, so nothing is uncounted
                "passed": passed, "failed": failed, "skipped": skipped,
                "pass_rate": round(passed / len(items), 3) if items else 0.0,
            },
            "cost": cost,
            "by_test_type": by_type,
            "results": items,
        },
    )


def _skipped_rows(db, project, run, ran_keys: set[str]) -> list[dict]:
    """Selected-but-unexecuted tests, as skipped rows with a reason. Keeps the run
    honest: a quarantined or unapproved test that never ran is visible, not absent."""
    from ..models import TestStatus
    selected = [k for k in ((run.selection or {}).get("keys") or []) if k not in ran_keys]
    if not selected:
        return []
    cases = {c.key: c for c in db.execute(
        select(TestCase).where(TestCase.project_id == project.id,
                               TestCase.key.in_(selected))).scalars()}
    rows = []
    for key in selected:
        case = cases.get(key)
        reason = ("quarantined" if case is not None and case.quarantined
                  else "not approved" if case is not None and case.status != TestStatus.APPROVED
                  else "not run")
        rows.append({
            "id": "", "key": key, "title": getattr(case, "title", key),
            "test_type": _test_type(case, key), "unit": _unit(case),
            "status": "skipped", "skip_reason": reason, "browser": "",
            "duration_ms": 0, "classification": None, "healed": False,
            "signature": None, "error": None, "requirement_refs": [], "steps": [],
            "href": "", "ui_href": ui_href("runs", run.id),
        })
    return rows


def _run_cost(db, run_test_ids: list[str]) -> dict:
    """The model spend attributable to this run. Execution and deterministic
    healing call no model, so this is 0/0/$0 for an ordinary re-run; only a
    semantic or vision heal spends anything, and those are counted here."""
    model_calls = 0
    heals = 0
    cache_hits = 0
    if run_test_ids:
        strategies = list(db.execute(select(HealEvent.strategy).where(
            HealEvent.run_test_id.in_(run_test_ids))).scalars())
        heals = len(strategies)
        model_calls = sum(1 for s in strategies if s in _MODEL_HEAL_STRATEGIES)
        cache_hits = sum(1 for s in strategies if s == "cache")
    tokens = model_calls * 2000  # rough; only non-zero when a model heal fired
    return {
        "model_calls": model_calls,
        "tokens_used": tokens,
        "cost_estimate_usd": round(tokens / 1_000_000 * 5.0, 4),  # order-of-magnitude
        # WO#8: step-cache hit rate. Heals resolved from cache spend zero tokens.
        "heals": heals,
        "cache_hits": cache_hits,
        "cache_hit_rate": round(cache_hits / heals, 3) if heals else None,
        "note": "Execution and deterministic healing call no model, so re-runs cost nothing.",
    }


def _readiness(run_status: str, failed: int, by_type: dict | None = None) -> dict:
    """The release verdict. A failure in a *critical* type (security, accessibility)
    is never a soft "review"; it blocks. Types are resolved through the one shared
    helper, so the readiness rule and the by-type table can't disagree."""
    from ..services.test_plan import CRITICAL_TYPES, type_label

    if run_status in _SKIPPED:
        return {"verdict": "not_run", "reason": "the run did not execute"}
    critical = {t: g["failed"] for t, g in (by_type or {}).items()
                if t in CRITICAL_TYPES and g.get("failed")}
    if failed == 0 and run_status in _PASSING:
        return {"verdict": "ready", "reason": "all tests passed"}
    if critical:
        detail = ", ".join(f"{n} {type_label(t).lower()}" for t, n in sorted(critical.items()))
        return {"verdict": "not_ready", "reason": f"critical failures: {detail}",
                "critical_types": sorted(critical)}
    if run_status == "flaky" or (failed and run_status in _PASSING):
        return {"verdict": "review", "reason": "passed but with flaky or healed results"}
    return {"verdict": "not_ready", "reason": f"{failed} test(s) failed"}


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #
def run_report_markdown(report: dict) -> str:
    run = report["run"]
    s = report["summary"]
    cost = report["cost"]
    verdict = report["readiness"]
    from ..services.test_plan import type_label
    target = run.get("base_url") or ""
    lines = [
        f"# Run #{run['number']}: {run['title'] or 'untitled'}",
        "",
        f"**Readiness: {verdict['verdict'].replace('_', ' ')}** ({verdict['reason']}). "
        f"{s['passed']}/{s['total']} passed, {s['failed']} failed, {s['skipped']} skipped "
        f"({s['pass_rate']:.0%} pass rate).",
        f"environment: `{run['environment']}` · target: {target or '-'}"
        + (f" · commit `{run['git_sha'][:8]}`" if run['git_sha'] else ""),
        f"Model cost: **{cost['model_calls']} call(s), {cost['tokens_used']:,} token(s), "
        f"${cost['cost_estimate_usd']}**"
        + (f" · step cache {cost['cache_hits']}/{cost['heals']} hit" if cost.get("heals") else "")
        + f". {cost['note']}",
        "",
        "## By test type",
        "",
        md_table(["Type", "Total", "Passed", "Failed", "Skipped"],
                 [[type_label(t), g["total"], g["passed"], g["failed"], g["skipped"]]
                  for t, g in sorted(report["by_test_type"].items())]),
        "## Results",
        "",
        md_table(["Test", "Status", "ms", "Notes"],
                 [[_detail_name(it), it["status"], it["duration_ms"],
                   (it["error"]["message"] if it["error"]
                    else it.get("skip_reason") or ("healed" if it["healed"] else ""))]
                  for it in report["results"]]),
    ]
    return cap_markdown("\n".join(lines) + "\n", report["href"])


# --------------------------------------------------------------------------- #
# JUnit XML: one <testsuite> per test type, one <testcase> per test
# --------------------------------------------------------------------------- #
def run_report_junit(report: dict) -> str:
    run = report["run"]
    results = report["results"]
    by_type: dict[str, list] = {}
    for it in results:
        by_type.setdefault(it["test_type"], []).append(it)

    total = report["summary"]["total"]
    failures = report["summary"]["failed"]
    skipped = report["summary"]["skipped"]

    suites_name = run["title"] or f'run-{run["number"]}'
    out = ['<?xml version="1.0" encoding="UTF-8"?>']
    out.append(
        f'<testsuites name={xml_attr(suites_name)} '
        f'tests="{total}" failures="{failures}" skipped="{skipped}" '
        f'time="{run["duration_ms"] / 1000:.3f}">'
    )
    for test_type, items in sorted(by_type.items()):
        suite_fail = sum(1 for it in items if it["status"] not in _PASSING and it["status"] not in _SKIPPED)
        suite_skip = sum(1 for it in items if it["status"] in _SKIPPED)
        suite_time = sum(it["duration_ms"] for it in items) / 1000
        out.append(
            f'  <testsuite name={xml_attr(test_type)} tests="{len(items)}" '
            f'failures="{suite_fail}" skipped="{suite_skip}" time="{suite_time:.3f}">'
        )
        out.append("    <properties>")
        out.append(f'      <property name="run_url" value={xml_attr(run["ui_href"])}/>')
        out.append(f'      <property name="environment" value={xml_attr(run["environment"])}/>')
        if run["git_sha"]:
            out.append(f'      <property name="git_sha" value={xml_attr(run["git_sha"])}/>')
        out.append("    </properties>")
        for it in items:
            out.append(
                f'    <testcase name={xml_attr(_detail_name(it))} '
                f'classname={xml_attr(test_type)} time="{it["duration_ms"] / 1000:.3f}">'
            )
            if it["requirement_refs"]:
                out.append(
                    f'      <property name="requirement_refs" value={xml_attr(",".join(it["requirement_refs"]))}/>'
                )
            if it["status"] in _SKIPPED:
                out.append("      <skipped/>")
            elif it["status"] not in _PASSING:
                err = it["error"] or {}
                out.append(
                    f'      <failure message={xml_attr(err.get("message", it["status"]))} '
                    f'type={xml_attr(err.get("type") or "AssertionError")}>'
                    f'{xml_text(err.get("message", ""))}</failure>'
                )
            step_log = "\n".join(
                f'{st["index"]}. {st["action"]}: {st["intent"]}' for st in it["steps"]
            )
            if step_log:
                out.append(f"      <system-out>{xml_text(step_log)}</system-out>")
            out.append("    </testcase>")
        out.append("  </testsuite>")
    out.append("</testsuites>")
    return "\n".join(out) + "\n"


def latest_rca_for_run(db, project_id: str, run_id: str) -> RCAReport | None:
    return db.execute(
        select(RCAReport).where(RCAReport.project_id == project_id, RCAReport.run_id == run_id)
        .order_by(RCAReport.created_at.desc())
    ).scalars().first()
