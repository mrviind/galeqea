"""Quality-planning tools, driven from chat.

Four tools over the one thing GaleQEA knows about, test coverage of requirements
and the runs that exercise it: what to cover next, what it will cost, where things
stand today, and how quality moved over a stretch of time. There are no people,
no capacity, no story points and no sprints here. This is a testing tool, and the
unit of work is a *requirement covered by tests*, never a person's time.

Everything is deterministic. The cost is a rule over the requirement's own shape
(criteria count, risk, existing coverage, unresolved questions); the coverage plan
is a rank over risk × coverage-gap × recent-failure history; the status brief and
the retrospective read real run, coverage and heal data. So all four work with no
model and give the same answer for the same state. A retrospective that changed
every time you asked would be worthless. A model, when configured, adds narrative
on top; it never invents the numbers.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select

from ..ai.tools import ToolContext, registry
from ..models import RequirementItem
from ..models.base import utcnow

RISK_WEIGHT = {"critical": 4.0, "high": 3.0, "medium": 2.0, "low": 1.0}

#: One acceptance criterion is at least one test; higher risk adds negative and
#: edge tests the criteria don't spell out. Coarse on purpose.
_RISK_EXTRA_TESTS = {"critical": 3, "high": 2, "medium": 1, "low": 0}
#: Rough authoring cost of one test with a model, and the wall-clock of running
#: one. Both are order-of-magnitude estimates the user can calibrate; the point
#: they make is the one that matters. See ``estimate_coverage``'s ``rerun_tokens``.
_TOKENS_PER_TEST = 4000
_RUN_MINUTES_PER_TEST = 0.4


# --------------------------------------------------------------------------- #
# Deterministic estimation core
# --------------------------------------------------------------------------- #
def estimate_coverage(*, criteria: int, risk: str, existing_tests: int, open_questions: int) -> dict:
    """What it costs to cover one requirement with tests, in QE units, not points.

    Three numbers, because they are the three things a tester actually spends:

    * **tests to author**: one per acceptance criterion, plus negative/edge tests
      that higher risk needs, minus what already exists.
    * **run minutes**: wall-clock to execute the full set once.
    * **build tokens**: model tokens to *author* the missing tests. Re-running
      them afterwards costs **zero** tokens, which is the whole "pay to build, not
      to re-run" promise expressed as a number.

    Unresolved questions block the work (you can't finish a test whose expected
    result is undecided) and drop the confidence.
    """
    base = max(1, criteria)
    needed = base + _RISK_EXTRA_TESTS.get(risk, 1)
    to_author = max(0, needed - min(existing_tests, needed))
    confidence = 0.9 if open_questions == 0 else max(0.3, 0.9 - 0.2 * open_questions)

    drivers = [f"{criteria} acceptance criterion(s) → ~{base} base test(s)"]
    if risk in {"high", "critical"}:
        drivers.append(f"{risk} risk adds {_RISK_EXTRA_TESTS[risk]} negative/edge test(s)")
    if existing_tests:
        drivers.append(f"{existing_tests} existing test(s) already cover part of it")
    if open_questions:
        drivers.append(f"{open_questions} open question(s): cannot finish until resolved")

    return {
        "tests_to_author": to_author,
        "tests_total": needed,
        "run_minutes": round(needed * _RUN_MINUTES_PER_TEST, 1),
        "build_tokens": to_author * _TOKENS_PER_TEST,
        "rerun_tokens": 0,  # re-running a built test never calls the model
        "confidence": round(confidence, 2),
        "blocked": open_questions > 0,
        "drivers": drivers,
    }


def _requirement_shape(item: RequirementItem, test_count: int) -> dict:
    return {
        "ref": item.ref,
        "title": item.title,
        "risk": item.risk,
        "criteria": len(item.acceptance_criteria or []),
        "open_questions": len(item.open_questions or []),
        "existing_tests": test_count,
    }


def _failure_signal(tests: list[dict]) -> float:
    """Fraction of a requirement's tests whose last outcome was bad (failed,
    errored or flaky). A recent-failure history is a reason to cover it deeper."""
    if not tests:
        return 0.0
    bad = sum(1 for t in tests
              if str(t.get("last_status", "")).lower() in {"failed", "error", "flaky"})
    return round(bad / len(tests), 2)


# --------------------------------------------------------------------------- #
# estimate_coverage_cost
# --------------------------------------------------------------------------- #
@registry.register(
    "estimate_coverage_cost",
    description=(
        "Estimate what it costs to cover a requirement with tests, as three QE "
        "numbers, not story points: how many tests to author, how many minutes the "
        "set takes to run, and how many model tokens authoring them costs. "
        "Re-running the tests afterwards costs zero tokens, and that zero is "
        "reported explicitly. The estimate comes from the requirement's own shape: "
        "acceptance criteria, risk (higher risk needs negative and edge tests the "
        "criteria don't list), tests that already exist, and unresolved questions "
        "(which block the work and lower confidence). Pass a requirement_ref to "
        "size an ingested requirement, or the shape directly. Deterministic: the "
        "same requirement always sizes the same."
    ),
    parameters={
        "properties": {
            "requirement_ref": {"type": "string", "description": "Requirement to size, e.g. REQ-014. Read from the project."},
            "criteria": {"type": "integer", "description": "Number of acceptance criteria, if sizing without an ingested requirement."},
            "risk": {"type": "string", "enum": ["low", "medium", "high", "critical"], "description": "Risk level, when sizing directly."},
            "existing_tests": {"type": "integer", "description": "Tests that already cover this, when sizing directly. Default 0."},
            "open_questions": {"type": "integer", "description": "Unresolved questions, when sizing directly. Default 0."},
        },
        "required": [],
    },
    category="planning",
    scopes=["requirements:read", "tests:read"],
    title="Estimate coverage cost (tests, run-minutes, tokens)",
    input_examples=[{"requirement_ref": "REQ-014"}, {"criteria": 3, "risk": "high", "existing_tests": 1}],
)
def estimate_coverage_cost(args: dict, ctx: ToolContext) -> dict:
    ref = (args.get("requirement_ref") or "").strip().upper()
    if ref:
        if ctx is None:
            return {"ok": False, "error": "Sizing an ingested requirement needs a project context."}
        item, test_count = _load_requirement(ctx, ref)
        if item is None:
            return {"ok": False, "error": f"No requirement {ref} in this project. Pass the shape directly, or check the ref."}
        shape = _requirement_shape(item, test_count)
    else:
        if "criteria" not in args and "risk" not in args:
            return {"ok": False, "error": "Give a requirement_ref, or the shape (criteria, risk) to size directly."}
        shape = {
            "ref": None, "title": None,
            "risk": args.get("risk", "medium"),
            "criteria": int(args.get("criteria") or 1),
            "open_questions": int(args.get("open_questions") or 0),
            "existing_tests": int(args.get("existing_tests") or 0),
        }

    est = estimate_coverage(
        criteria=shape["criteria"], risk=shape["risk"],
        existing_tests=shape["existing_tests"], open_questions=shape["open_questions"],
    )
    return {
        "ok": True,
        "requirement": shape,
        **{k: est[k] for k in
           ("tests_to_author", "tests_total", "run_minutes", "build_tokens", "rerun_tokens",
            "confidence", "blocked", "drivers")},
        "guidance": (
            f"~{est['tests_to_author']} test(s) to author (~{est['build_tokens']:,} build tokens, once), "
            f"then ~{est['run_minutes']} min to run the {est['tests_total']}-test set, "
            f"and 0 tokens on every re-run after. "
            + ("Blocked: resolve the open question(s) first; the estimate assumes they're answered."
               if est["blocked"] else "Calibrate the per-test figures to your suite over time.")
        ),
    }


def _load_requirement(ctx: ToolContext, ref: str):
    from ..models import TestCase

    item = ctx.db.execute(
        select(RequirementItem).where(
            RequirementItem.project_id == ctx.project_id, RequirementItem.ref == ref
        )
    ).scalars().first()
    if item is None:
        return None, 0
    tests = ctx.db.execute(select(TestCase).where(TestCase.project_id == ctx.project_id)).scalars()
    count = sum(1 for c in tests if ref in (c.requirement_refs or []))
    return item, count


# --------------------------------------------------------------------------- #
# plan_coverage
# --------------------------------------------------------------------------- #
@registry.register(
    "plan_coverage",
    description=(
        "Rank which requirements to cover with tests next: a prioritised 'cover "
        "these' list, highest-value first. The value of covering a requirement is "
        "its risk × how much is still uncovered × its recent-failure history: a "
        "critical requirement with no tests and recent failures rises to the top; a "
        "low-risk, well-covered, green one sinks. Each item carries the reason it "
        "ranks where it does and its coverage cost. Requirements with unresolved "
        "questions are listed separately as blocked, because you can't finish work "
        "whose expected result is undecided. It reads real requirements, coverage "
        "and run outcomes, changes nothing, and is deterministic: the same state "
        "yields the same plan."
    ),
    parameters={
        "properties": {
            "top": {"type": "integer", "description": "How many requirements to return in the cover-next list. Default 10."},
            "include_covered": {"type": "boolean", "description": "Also rank requirements that already have some tests, for deepening coverage. Default false."},
            "risk_floor": {"type": "string", "enum": ["low", "medium", "high", "critical"], "description": "Only consider requirements at or above this risk. Default low."},
        },
        "required": [],
    },
    category="planning",
    scopes=["requirements:read", "tests:read", "runs:read"],
    title="Plan coverage: what to test next",
    input_examples=[{}, {"top": 5, "risk_floor": "high"}],
)
def plan_coverage(args: dict, ctx: ToolContext) -> dict:
    from ..intelligence.coverage import traceability_matrix

    if ctx is None:
        return {"ok": False, "error": "Coverage planning needs a project context."}

    top = max(1, int(args.get("top") or 10))
    include_covered = bool(args.get("include_covered"))
    floor = args.get("risk_floor", "low")
    floor_weight = RISK_WEIGHT.get(floor, 1.0)

    matrix = traceability_matrix(ctx.db, ctx.project_id)
    if not matrix:
        return {
            "ok": True, "cover_next": [], "blocked": [],
            "guidance": "No requirements are ingested yet, so there is nothing to plan. "
                        "Upload a requirement document first.",
        }

    candidates = []
    for row in matrix:
        risk = row.get("risk", "medium")
        if RISK_WEIGHT.get(risk, 2.0) < floor_weight:
            continue
        tests = row.get("tests") or []
        if tests and not include_covered:
            continue
        criteria = len(row.get("acceptance_criteria") or [])
        questions = len(row.get("open_questions") or [])
        est = estimate_coverage(criteria=criteria, risk=risk,
                                existing_tests=len(tests), open_questions=questions)
        gap = 1.0 if not tests else 0.4
        failures = _failure_signal(tests)
        # Priority = risk × uncovered-gap × recent-failure boost. Failures on an
        # already-covered requirement are what pull it back up the list.
        priority = round(RISK_WEIGHT.get(risk, 2.0) * gap * (1.0 + failures), 2)
        reason_bits = [f"{risk} risk", "no tests yet" if not tests else "deepening coverage"]
        if failures:
            reason_bits.append(f"recent failures ({int(failures * 100)}% of its tests)")
        candidates.append({
            "ref": row["ref"], "title": row["title"], "risk": risk,
            "priority": priority, "failure_signal": failures,
            "tests_to_author": est["tests_to_author"], "run_minutes": est["run_minutes"],
            "build_tokens": est["build_tokens"], "existing_tests": len(tests),
            "open_questions": questions, "blocked": est["blocked"],
            "reason": ", ".join(reason_bits),
        })

    blocked = [c for c in candidates if c["blocked"]]
    ready = sorted((c for c in candidates if not c["blocked"]),
                   key=lambda c: (c["priority"], -c["tests_to_author"]), reverse=True)
    cover_next = ready[:top]

    return {
        "ok": True,
        "cover_next": cover_next,
        "deferred": ready[top:],
        "blocked": [{**b, "reason": f"{b['open_questions']} open question(s): resolve before covering"} for b in blocked],
        "guidance": _plan_guidance(cover_next, ready[top:], blocked),
        "_ui": {
            "pane": "requirements",
            "title": f"Coverage plan · {len(cover_next)} to cover next",
            "markdown": _plan_markdown(cover_next, blocked),
        },
    }


def _plan_guidance(cover_next, deferred, blocked) -> str:
    parts = [f"Ranked {len(cover_next)} requirement(s) to cover next, highest value first."]
    if blocked:
        parts.append(f"{len(blocked)} are blocked on open questions and left out. Resolve those to rank them.")
    if deferred:
        parts.append(f"{len(deferred)} more are ranked below the cut.")
    parts.append("This is a recommendation; it changes nothing.")
    return " ".join(parts)


def _plan_markdown(cover_next, blocked) -> str:
    lines = ["# Coverage plan: what to test next", ""]
    if cover_next:
        lines += ["## Cover next", ""]
        for c in cover_next:
            lines.append(f"- **{c['ref']}** ({c['risk']} · ~{c['tests_to_author']} test(s)): {c['title']}  \n  _{c['reason']}_")
        lines.append("")
    if blocked:
        lines += ["## Blocked: resolve before covering", ""]
        for b in blocked:
            lines.append(f"- **{b['ref']}** ({b['open_questions']} open question(s)): {b['title']}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# test_status_brief
# --------------------------------------------------------------------------- #
@registry.register(
    "test_status_brief",
    description=(
        "A brief on where testing stands right now: recent run outcomes, new "
        "failures, flaky tests, what's blocked, and what to run today, all from "
        "real data, not memory. Use it to catch up after time away or to open a "
        "review. Recent is the pass/fail of the last few runs; new failures are "
        "tests that failed most recently; flaky are the ones whose verdicts can't "
        "be trusted; blocked is failing/queued-for-approval work and uncovered "
        "critical requirements; and it suggests the next run to make."
    ),
    parameters={
        "properties": {
            "since_runs": {"type": "integer", "description": "How many recent runs the brief covers. Default 3."},
        },
        "required": [],
    },
    category="planning",
    scopes=["runs:read", "tests:read", "requirements:read"],
    title="Status brief: recent runs, failures, what to run",
    input_examples=[{}, {"since_runs": 5}],
)
def test_status_brief(args: dict, ctx: ToolContext) -> dict:
    from ..intelligence.coverage import compute
    from ..models import ApprovalRequest, ApprovalStatus, Run, TestCase, TestStat, TestStatus

    if ctx is None:
        return {"ok": False, "error": "A status brief needs a project context."}

    since = max(1, int(args.get("since_runs") or 3))
    recent_runs = list(ctx.db.execute(
        select(Run).where(Run.project_id == ctx.project_id).order_by(Run.number.desc()).limit(since)
    ).scalars())

    coverage = compute(ctx.db, ctx.project_id, persist=False)
    cases = list(ctx.db.execute(select(TestCase).where(TestCase.project_id == ctx.project_id)).scalars())
    stats = {s.test_case_id: s for s in ctx.db.execute(
        select(TestStat).where(TestStat.project_id == ctx.project_id)).scalars()}

    passed = sum((r.totals or {}).get("passed", 0) for r in recent_runs)
    failed = sum((r.totals or {}).get("failed", 0) for r in recent_runs)
    proposed = [c for c in cases if c.status == TestStatus.PROPOSED]
    flaky = [c for c in cases if (s := stats.get(c.id)) and getattr(s, "flake_score", 0) >= 0.3]
    new_failures = [c for c in cases
                    if (s := stats.get(c.id)) and str(getattr(s, "last_status", "")).lower() in {"failed", "error"}]
    pending = ctx.db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.project_id == ctx.project_id,
            ApprovalRequest.status == ApprovalStatus.PENDING,
        )
    ).scalars().all()

    recent = {"runs": len(recent_runs), "tests_passed": passed, "tests_failed": failed,
              "requirements_covered": len(coverage.get("covered", []))}
    blocked = {
        "tests_failing": len(new_failures),
        "tests_flaky": len(flaky),
        "requirements_uncovered": len(coverage.get("uncovered", [])),
        "awaiting_approval": len(pending),
    }
    if new_failures:
        run_today = f"Re-run the {len(new_failures)} failing test(s) to confirm, then diagnose with run_rca."
    elif coverage.get("uncovered"):
        run_today = "Author coverage for the top uncovered requirements (plan_coverage)."
    elif proposed:
        run_today = f"Review the {len(proposed)} test(s) awaiting approval."
    else:
        run_today = "Suite is green and covered. Run the regression suite to keep it that way."

    return {
        "ok": True,
        "recent": recent,
        "new_failures": [{"key": c.key, "title": c.title} for c in new_failures[:10]],
        "flaky_tests": [{"key": c.key, "title": c.title} for c in flaky[:10]],
        "blocked": blocked,
        "awaiting_review": len(proposed),
        "run_today": run_today,
        "guidance": _brief_guidance(recent, blocked, run_today),
        "_ui": {
            "pane": "rca",
            "title": "Status brief",
            "review": {
                "verdict": "blocked" if blocked["tests_failing"] or blocked["awaiting_approval"]
                           else "needs_work" if blocked["requirements_uncovered"] else "sound",
                "findings": (
                    ([{"severity": "high", "step": None, "kind": "failing",
                       "message": f"{blocked['tests_failing']} test(s) failing"}]
                     if blocked["tests_failing"] else [])
                    + ([{"severity": "medium", "step": None, "kind": "flaky",
                         "message": f"{blocked['tests_flaky']} flaky test(s) undermining confidence"}]
                       if blocked["tests_flaky"] else [])
                    + ([{"severity": "medium", "step": None, "kind": "approval",
                         "message": f"{blocked['awaiting_approval']} item(s) waiting on your approval"}]
                       if blocked["awaiting_approval"] else [])
                ),
            },
        },
    }


def _brief_guidance(recent, blocked, run_today) -> str:
    parts = [
        f"Recent: {recent['tests_passed']} passed, {recent['tests_failed']} failed across "
        f"{recent['runs']} run(s); {recent['requirements_covered']} requirement(s) covered.",
    ]
    b = []
    if blocked["tests_failing"]:
        b.append(f"{blocked['tests_failing']} failing")
    if blocked["tests_flaky"]:
        b.append(f"{blocked['tests_flaky']} flaky")
    if blocked["awaiting_approval"]:
        b.append(f"{blocked['awaiting_approval']} awaiting approval")
    if blocked["requirements_uncovered"]:
        b.append(f"{blocked['requirements_uncovered']} requirement(s) uncovered")
    parts.append("Blocked: " + (", ".join(b) if b else "nothing, the board is clear."))
    parts.append(f"Today: {run_today}")
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# quality_retrospective
# --------------------------------------------------------------------------- #
@registry.register(
    "quality_retrospective",
    description=(
        "Report how quality moved over a stretch of runs: failures caught and which "
        "recurred, the flaky-test count, how many locator heals were applied, and "
        "the coverage picture, with every number from real run, heal and coverage data, "
        "not vibes. Use it to review a period of testing. It reports what improved, "
        "what regressed, and the concrete next moves (diagnose these recurring "
        "failures, quarantine these flaky tests, cover these requirements). "
        "Deterministic over the runs it reads."
    ),
    parameters={
        "properties": {
            "over_runs": {"type": "integer", "description": "How many recent runs the retrospective spans. Default 10."},
            "days": {"type": "integer", "description": "Alternatively, span the last N days (used for the heal count window). Default derives from the runs."},
        },
        "required": [],
    },
    category="planning",
    scopes=["runs:read", "tests:read", "requirements:read"],
    title="Quality retrospective: failures, flake, heals, coverage",
    input_examples=[{}, {"over_runs": 20}],
)
def quality_retrospective(args: dict, ctx: ToolContext) -> dict:
    from ..intelligence.coverage import compute
    from ..intelligence.flaky import assess
    from ..models import HealEvent, Run, TestCase, TestStat

    if ctx is None:
        return {"ok": False, "error": "A retrospective needs a project context."}

    span = max(1, int(args.get("over_runs") or 10))
    runs = list(ctx.db.execute(
        select(Run).where(Run.project_id == ctx.project_id).order_by(Run.number.desc()).limit(span)
    ).scalars())

    coverage = compute(ctx.db, ctx.project_id, persist=False)
    cases = list(ctx.db.execute(select(TestCase).where(TestCase.project_id == ctx.project_id)).scalars())
    stats = {s.test_case_id: s for s in ctx.db.execute(
        select(TestStat).where(TestStat.project_id == ctx.project_id)).scalars()}

    total_tests = sum((r.totals or {}).get("total", 0) for r in runs)
    total_passed = sum((r.totals or {}).get("passed", 0) for r in runs)
    total_failed = sum((r.totals or {}).get("failed", 0) for r in runs)
    pass_rate = (total_passed / total_tests) if total_tests else 0.0

    # "Defect escape" proxy without prod telemetry: a failure that recurred across
    # runs is a defect the suite kept catching but that stayed unfixed.
    recurring = sum(1 for s in stats.values() if getattr(s, "failures", 0) >= 2)

    flaky = []
    for case in cases:
        stat = stats.get(case.id)
        if stat and assess(stat).score >= 0.3:
            flaky.append({"key": case.key, "title": case.title,
                          "flake_score": round(assess(stat).score, 2)})
    flaky.sort(key=lambda f: f["flake_score"], reverse=True)

    # Heals applied over the window: the App Model's self-maintenance, quantified.
    window_start = _window_start(runs, args.get("days"))
    heals_applied = ctx.db.execute(
        select(HealEvent).where(
            HealEvent.project_id == ctx.project_id, HealEvent.created_at >= window_start,
        )
    ).scalars().all()
    heal_count = len(heals_applied)

    covered = coverage.get("covered", [])
    uncovered = coverage.get("uncovered", [])
    by_risk = coverage.get("by_risk", {})
    critical_gaps = [u for u in uncovered if u.get("risk") == "critical"]

    improved, regressed, actions = [], [], []
    if runs:
        improved.append(f"Ran {len(runs)} run(s); pass rate {pass_rate:.0%} over {total_tests} executions.")
    if covered:
        improved.append(f"{len(covered)} requirement(s) have test coverage.")
    if heal_count:
        improved.append(f"{heal_count} locator heal(s) applied: coverage self-maintained through UI drift.")
    if not flaky and total_tests:
        improved.append("No flaky tests, so the suite's verdicts were trustworthy.")
    if not improved:
        defined = len(covered) + len(uncovered)
        improved.append(f"{defined} requirement(s) are documented and ready to cover."
                        if defined else "The project is set up and ready for its first run.")

    if recurring:
        regressed.append(f"{recurring} test(s) failed repeatedly: recurring defects, not one-offs.")
        actions.append("Diagnose the recurring failures with run_rca and fix them at the source.")
    elif total_failed:
        regressed.append(f"{total_failed} test execution(s) failed across the period.")
        actions.append("Diagnose the failures with run_rca.")
    if flaky:
        regressed.append(f"{len(flaky)} flaky test(s) with verdicts that could not be trusted.")
        actions.append(f"Quarantine or fix the worst flaky tests: {', '.join(f['key'] for f in flaky[:3])}.")
    if critical_gaps:
        regressed.append(f"{len(critical_gaps)} critical requirement(s) still have no tests.")
        actions.append(f"Cover these first: {', '.join(u['ref'] for u in critical_gaps[:3])}.")
    elif uncovered:
        actions.append(f"Plan coverage for the {len(uncovered)} remaining uncovered requirement(s).")

    if not regressed:
        regressed.append("Nothing material, but confirm the coverage numbers reflect real assertions, not just presence.")
    if not actions:
        actions.append("Keep the cadence; consider deepening coverage on high-risk requirements.")

    return {
        "ok": True,
        "span_runs": len(runs),
        "metrics": {
            "pass_rate": round(pass_rate, 3),
            "executions": total_tests,
            "failures": total_failed,
            "recurring_failures": recurring,
            "flaky_tests": len(flaky),
            "heals_applied": heal_count,
            "requirements_covered": len(covered),
            "requirements_uncovered": len(uncovered),
        },
        "improved": improved,
        "regressed": regressed,
        "action_items": actions,
        "by_risk": by_risk,
        "guidance": (
            f"Retrospective over {len(runs)} run(s): {len(improved)} improvement(s), "
            f"{len(regressed)} regression(s), {len(actions)} action item(s). Every point is from the "
            "run, heal and coverage data."
        ),
        "_ui": {
            "pane": "requirements",
            "title": f"Quality retrospective · {len(runs)} runs",
            "markdown": _retro_markdown(len(runs), pass_rate, heal_count, improved, regressed, actions),
        },
    }


def _window_start(runs, days) -> datetime:
    """Start of the heal-count window: an explicit days back, else the oldest run
    in the span, else 30 days as a floor."""
    if days:
        return utcnow() - timedelta(days=int(days))
    starts = [r.created_at for r in runs if getattr(r, "created_at", None)]
    if starts:
        return min(starts)
    return utcnow() - timedelta(days=30)


def _retro_markdown(span, pass_rate, heal_count, improved, regressed, actions) -> str:
    lines = [f"# Quality retrospective: last {span} run(s)", "",
             f"Pass rate **{pass_rate:.0%}** · **{heal_count}** heal(s) applied.", ""]
    lines += ["## What improved", ""] + [f"- {x}" for x in improved] + [""]
    lines += ["## What regressed", ""] + [f"- {x}" for x in regressed] + [""]
    lines += ["## Action items", ""] + [f"{i}. {x}" for i, x in enumerate(actions, 1)]
    return "\n".join(lines) + "\n"
