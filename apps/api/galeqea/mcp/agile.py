"""Agile ceremonies for quality engineering, driven from chat.

These tools run the four Scrum ceremonies over the one thing GaleQEA actually
knows about: test coverage of requirements. They are not a general project
manager — there are no people, story assignments or Jira sprints here. A
"sprint" is a batch of requirements a team commits to *covering with tests*, and
the ceremonies plan, track and reflect on that.

Everything is deterministic. The estimate is a rule over the requirement's own
shape (criteria count, risk, existing coverage, unresolved questions), the plan
is a rank over risk × coverage-gap against a stated capacity, and the standup and
retrospective read real run and coverage data. So all four work in No-AI mode and
give the same answer for the same state — a retrospective that changed every time
you asked would be worthless. A model, when configured, adds narrative on top; it
never invents the numbers.

Learned from the field (planning-poker tools, AI sprint planners): estimate from
history and shape rather than opinion, generate the backlog from what is
uncovered, and make the retro cite evidence, not vibes.
"""

from __future__ import annotations

from sqlalchemy import select

from ..ai.tools import ToolContext, registry
from ..models import RequirementItem

#: Fibonacci story points, the planning-poker scale. Effort is mapped onto the
#: nearest point so estimates are comparable and coarse on purpose — pretending
#: to 1-point precision on test effort is false confidence.
FIBONACCI = [1, 2, 3, 5, 8, 13]

RISK_WEIGHT = {"critical": 4.0, "high": 3.0, "medium": 2.0, "low": 1.0}


# --------------------------------------------------------------------------- #
# Deterministic estimation core
# --------------------------------------------------------------------------- #
def estimate_points(*, criteria: int, risk: str, existing_tests: int, open_questions: int) -> dict:
    """Story points for *covering* one requirement with tests.

    The rule, stated so a reviewer can argue with it:

    * Each acceptance criterion is at least one test — the base cost.
    * Higher-risk requirements need negative paths and edge cases the criteria do
      not spell out, so risk multiplies the base.
    * Requirements that already have tests are cheaper — some of the work exists.
    * Unresolved questions add cost *and* uncertainty: you cannot finish a test
      whose expected result is still undecided, so each open question adds effort
      and drops the confidence.
    """
    base = max(1, criteria)
    risk_factor = RISK_WEIGHT.get(risk, 2.0)
    raw = base * (0.6 + 0.35 * risk_factor)           # criteria scaled by risk
    raw += open_questions * 1.5                         # ambiguity is expensive
    raw -= min(existing_tests, base) * 0.8             # credit for work already done
    raw = max(1.0, raw)

    points = min(FIBONACCI, key=lambda f: abs(f - raw))
    confidence = 0.9 if open_questions == 0 else max(0.3, 0.9 - 0.2 * open_questions)

    drivers = []
    drivers.append(f"{criteria} acceptance criterion(s)")
    if risk in {"high", "critical"}:
        drivers.append(f"{risk} risk (needs negative and edge coverage)")
    if existing_tests:
        drivers.append(f"{existing_tests} existing test(s) reduce the work")
    if open_questions:
        drivers.append(f"{open_questions} open question(s) — cannot finish until resolved")

    return {
        "points": points,
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


# --------------------------------------------------------------------------- #
# estimate_test_effort
# --------------------------------------------------------------------------- #
@registry.register(
    "estimate_test_effort",
    description=(
        "Estimate the effort to cover a requirement with tests, in story points "
        "on the Fibonacci scale — planning poker, but by rule rather than "
        "opinion. Use it during backlog refinement or sprint planning to size "
        "work before committing to it. The estimate comes from the requirement's "
        "own shape: how many acceptance criteria it has, its risk (higher risk "
        "needs negative and edge coverage the criteria do not list), how many "
        "tests already exist, and how many questions are still open. A "
        "requirement with unresolved questions is flagged blocked — you cannot "
        "finish a test whose expected result is undecided — and its confidence "
        "drops. Pass a requirement_ref to size an ingested requirement, or the "
        "shape directly. The rule is deterministic, so the same requirement "
        "always sizes the same."
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
    title="Estimate test effort in story points",
    input_examples=[{"requirement_ref": "REQ-014"}, {"criteria": 3, "risk": "high", "existing_tests": 1}],
)
def estimate_test_effort(args: dict, ctx: ToolContext) -> dict:
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

    estimate = estimate_points(
        criteria=shape["criteria"], risk=shape["risk"],
        existing_tests=shape["existing_tests"], open_questions=shape["open_questions"],
    )
    return {
        "ok": True,
        "requirement": shape,
        "points": estimate["points"],
        "confidence": estimate["confidence"],
        "blocked": estimate["blocked"],
        "drivers": estimate["drivers"],
        "guidance": (
            f"{estimate['points']} point(s), confidence {estimate['confidence']}. "
            + ("This is blocked: resolve the open question(s) before committing it to a sprint — "
               "the estimate assumes they are answered."
               if estimate["blocked"] else
               "Refine the estimate with the team; the rule sizes shape, not the specifics of your app.")
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
# plan_test_sprint  (sprint planning)
# --------------------------------------------------------------------------- #
@registry.register(
    "plan_test_sprint",
    description=(
        "Propose a test-coverage sprint: which requirements to cover next, "
        "ordered by risk and coverage gap, sized to a capacity in story points. "
        "Use it to run sprint planning from chat — it reads the project's "
        "requirements and current coverage, estimates each uncovered or "
        "under-covered one, and fills the sprint highest-value-first until the "
        "capacity is spent, leaving the rest in a backlog with the reason each "
        "was deferred. Blocked requirements (unresolved questions) are surfaced "
        "separately rather than committed, because you cannot finish work whose "
        "expected result is undecided. The plan is a recommendation the team "
        "refines, not a command; it changes nothing. Deterministic, so the same "
        "state yields the same plan."
    ),
    parameters={
        "properties": {
            "capacity_points": {"type": "integer", "description": "Team capacity for this sprint, in story points. Default 20."},
            "include_covered": {"type": "boolean", "description": "Also consider requirements that already have some tests, for deepening coverage. Default false."},
            "risk_floor": {"type": "string", "enum": ["low", "medium", "high", "critical"], "description": "Only consider requirements at or above this risk. Default low."},
        },
        "required": [],
    },
    category="planning",
    scopes=["requirements:read", "tests:read"],
    title="Plan a test-coverage sprint",
    input_examples=[{"capacity_points": 20}, {"capacity_points": 13, "risk_floor": "high"}],
)
def plan_test_sprint(args: dict, ctx: ToolContext) -> dict:
    from ..intelligence.coverage import traceability_matrix

    if ctx is None:
        return {"ok": False, "error": "Sprint planning needs a project context."}

    capacity = int(args.get("capacity_points") or 20)
    include_covered = bool(args.get("include_covered"))
    floor = args.get("risk_floor", "low")
    floor_weight = RISK_WEIGHT.get(floor, 1.0)

    matrix = traceability_matrix(ctx.db, ctx.project_id)
    if not matrix:
        return {
            "ok": True, "capacity_points": capacity, "committed": [], "backlog": [], "blocked": [],
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
        estimate = estimate_points(criteria=criteria, risk=risk,
                                   existing_tests=len(tests), open_questions=questions)
        # Value = risk × how much is still uncovered. A critical requirement with
        # no tests is the highest-value work; a low-risk one already tested is the
        # lowest. This is what "highest-value-first" means concretely.
        gap = 1.0 if not tests else 0.4
        value = RISK_WEIGHT.get(risk, 2.0) * gap
        candidates.append({
            "ref": row["ref"], "title": row["title"], "risk": risk,
            "points": estimate["points"], "confidence": estimate["confidence"],
            "blocked": estimate["blocked"], "value": round(value, 2),
            "existing_tests": len(tests), "criteria": criteria, "open_questions": questions,
        })

    # Blocked items never enter the sprint; they go to a separate list so planning
    # surfaces the decision the team must make before the work is real.
    blocked = [c for c in candidates if c["blocked"]]
    ready = [c for c in candidates if not c["blocked"]]
    ready.sort(key=lambda c: (c["value"], -c["points"]), reverse=True)

    committed, backlog = [], []
    spent = 0
    for item in ready:
        if spent + item["points"] <= capacity:
            committed.append({**item, "reason": f"risk {item['risk']}, "
                              + ("no tests yet" if item["existing_tests"] == 0 else "deepening coverage")})
            spent += item["points"]
        else:
            backlog.append({**item, "reason": f"over capacity ({spent}+{item['points']} > {capacity})"})

    return {
        "ok": True,
        "capacity_points": capacity,
        "committed_points": spent,
        "committed": committed,
        "backlog": backlog,
        "blocked": [{**b, "reason": f"{b['open_questions']} open question(s) — resolve before committing"} for b in blocked],
        "guidance": _sprint_guidance(capacity, spent, committed, backlog, blocked),
        "_ui": {
            "pane": "requirements",
            "title": f"Sprint plan · {spent}/{capacity} points",
            "markdown": _sprint_markdown(capacity, spent, committed, backlog, blocked),
        },
    }


def _sprint_guidance(capacity, spent, committed, backlog, blocked) -> str:
    parts = [f"Proposed a sprint of {len(committed)} requirement(s) totalling {spent} of {capacity} points."]
    if blocked:
        parts.append(f"{len(blocked)} requirement(s) are blocked on open questions and were left out — "
                     "resolve those to bring them in.")
    if backlog:
        parts.append(f"{len(backlog)} more are ready but over capacity, in the backlog.")
    parts.append("This is a recommendation — refine it with the team before committing.")
    return " ".join(parts)


def _sprint_markdown(capacity, spent, committed, backlog, blocked) -> str:
    lines = [f"# Sprint plan — {spent}/{capacity} points", ""]
    if committed:
        lines += ["## Committed", ""]
        for c in committed:
            lines.append(f"- **{c['ref']}** ({c['points']}pt · {c['risk']}) — {c['title']}")
        lines.append("")
    if blocked:
        lines += ["## Blocked — resolve before committing", ""]
        for b in blocked:
            lines.append(f"- **{b['ref']}** — {b['open_questions']} open question(s): {b['title']}")
        lines.append("")
    if backlog:
        lines += ["## Backlog (ready, over capacity)", ""]
        for c in backlog[:10]:
            lines.append(f"- {c['ref']} ({c['points']}pt · {c['risk']}) — {c['title']}")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# test_standup  (daily standup)
# --------------------------------------------------------------------------- #
@registry.register(
    "test_standup",
    description=(
        "Give a standup on the state of testing: what is done, what is in "
        "progress, and what is blocked — the three questions of a daily scrum, "
        "answered from real data rather than memory. Use it to open a standup or "
        "to catch up after time away. Done is tests that ran and passed recently "
        "and requirements now covered; in progress is proposed tests awaiting "
        "review and requirements partially covered; blocked is failing or flaky "
        "tests, requirements with open questions, and anything queued for "
        "approval. It reads runs, coverage and the review queue, so it reflects "
        "what actually happened."
    ),
    parameters={
        "properties": {
            "since_runs": {"type": "integer", "description": "How many recent runs to treat as 'since last standup'. Default 3."},
        },
        "required": [],
    },
    category="planning",
    scopes=["runs:read", "tests:read", "requirements:read"],
    title="Stand-up: what's done, in progress, and blocked",
    input_examples=[{}, {"since_runs": 5}],
)
def test_standup(args: dict, ctx: ToolContext) -> dict:
    from ..intelligence.coverage import compute
    from ..models import ApprovalRequest, ApprovalStatus, Run, TestCase, TestStat, TestStatus

    if ctx is None:
        return {"ok": False, "error": "A standup needs a project context."}

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
    pending = ctx.db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.project_id == ctx.project_id,
            ApprovalRequest.status == ApprovalStatus.PENDING,
        )
    ).scalars().all()

    done = {
        "runs_since": len(recent_runs),
        "tests_passed": passed,
        "requirements_covered": len(coverage.get("covered", [])),
    }
    in_progress = {
        "tests_awaiting_review": len(proposed),
        "requirements_partially_covered": len(coverage.get("weak", [])),
    }
    blocked = {
        "tests_failing": failed,
        "tests_flaky": len(flaky),
        "requirements_uncovered": len(coverage.get("uncovered", [])),
        "awaiting_approval": len(pending),
    }

    return {
        "ok": True,
        "done": done,
        "in_progress": in_progress,
        "blocked": blocked,
        "flaky_tests": [{"key": c.key, "title": c.title} for c in flaky[:10]],
        "guidance": _standup_guidance(done, in_progress, blocked),
        "_ui": {
            "pane": "rca",
            "title": "Test standup",
            "review": {
                "verdict": "blocked" if blocked["tests_failing"] or blocked["awaiting_approval"]
                           else "needs_work" if blocked["requirements_uncovered"] else "sound",
                "findings": (
                    ([{"severity": "high", "step": None, "kind": "failing",
                       "message": f"{blocked['tests_failing']} test(s) failing in the last {len(recent_runs)} run(s)"}]
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


def _standup_guidance(done, in_progress, blocked) -> str:
    parts = [
        f"Done: {done['tests_passed']} test(s) passed across {done['runs_since']} run(s), "
        f"{done['requirements_covered']} requirement(s) covered.",
        f"In progress: {in_progress['tests_awaiting_review']} test(s) awaiting review.",
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
    parts.append("Blocked: " + (", ".join(b) if b else "nothing — the board is clear."))
    return " ".join(parts)


# --------------------------------------------------------------------------- #
# test_retrospective  (sprint retrospective)
# --------------------------------------------------------------------------- #
@registry.register(
    "test_retrospective",
    description=(
        "Run a retrospective on a period of testing: what went well, what did "
        "not, and concrete action items — every point cited to real data, not "
        "vibes. Use it to close a sprint or review a stretch of runs. Went-well "
        "is drawn from coverage gained, pass rate and stability; what-did-not "
        "from failures, flaky tests and requirements still uncovered; the action "
        "items are the specific next moves those findings imply (quarantine "
        "these flaky tests, cover these requirements, resolve these questions). "
        "A retrospective that changed every time you asked would be worthless, so "
        "this is deterministic over the runs and coverage it reads."
    ),
    parameters={
        "properties": {
            "over_runs": {"type": "integer", "description": "How many recent runs the retrospective spans. Default 10."},
        },
        "required": [],
    },
    category="planning",
    scopes=["runs:read", "tests:read", "requirements:read"],
    title="Retrospective on a period of testing",
    input_examples=[{}, {"over_runs": 20}],
)
def test_retrospective(args: dict, ctx: ToolContext) -> dict:
    from ..intelligence.coverage import compute
    from ..intelligence.flaky import assess
    from ..models import Run, TestCase, TestStat

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

    flaky = []
    for case in cases:
        stat = stats.get(case.id)
        if stat and assess(stat).score >= 0.3:
            flaky.append({"key": case.key, "title": case.title,
                          "flake_score": round(assess(stat).score, 2)})
    flaky.sort(key=lambda f: f["flake_score"], reverse=True)

    covered = coverage.get("covered", [])
    uncovered = coverage.get("uncovered", [])
    by_risk = coverage.get("by_risk", {})
    critical_gaps = [u for u in uncovered if u.get("risk") == "critical"]

    went_well, went_wrong, actions = [], [], []

    if runs:
        went_well.append(f"Ran {len(runs)} run(s); pass rate {pass_rate:.0%} over {total_tests} executions.")
    if covered:
        went_well.append(f"{len(covered)} requirement(s) have test coverage.")
    if not flaky and total_tests:
        went_well.append("No flaky tests — the suite's verdicts were trustworthy.")
    if not went_well:
        # A project that has defined requirements but not yet run anything has
        # still done real work worth acknowledging — the backlog exists.
        defined = len(covered) + len(uncovered)
        went_well.append(
            f"{defined} requirement(s) are documented and ready to plan against."
            if defined else "The project is set up and ready for its first sprint."
        )

    if total_failed:
        went_wrong.append(f"{total_failed} test execution(s) failed across the period.")
        actions.append("Diagnose the recurring failures with run_rca before the next sprint.")
    if flaky:
        went_wrong.append(f"{len(flaky)} flaky test(s) — verdicts that could not be trusted.")
        actions.append(f"Quarantine or fix the worst flaky tests: {', '.join(f['key'] for f in flaky[:3])}.")
    if critical_gaps:
        went_wrong.append(f"{len(critical_gaps)} critical requirement(s) still have no tests.")
        actions.append(f"Prioritise covering: {', '.join(u['ref'] for u in critical_gaps[:3])}.")
    elif uncovered:
        actions.append(f"Plan coverage for the {len(uncovered)} remaining uncovered requirement(s).")

    if not went_wrong:
        went_wrong.append("Nothing material — but confirm the coverage numbers reflect real assertions, not just presence.")
    if not actions:
        actions.append("Keep the cadence; consider deepening coverage on high-risk requirements.")

    return {
        "ok": True,
        "span_runs": len(runs),
        "metrics": {
            "pass_rate": round(pass_rate, 3),
            "executions": total_tests,
            "failed": total_failed,
            "flaky_tests": len(flaky),
            "requirements_covered": len(covered),
            "requirements_uncovered": len(uncovered),
        },
        "went_well": went_well,
        "went_wrong": went_wrong,
        "action_items": actions,
        "by_risk": by_risk,
        "guidance": (
            f"Retrospective over {len(runs)} run(s): {len(went_well)} thing(s) went well, "
            f"{len(went_wrong)} did not, {len(actions)} action item(s). Every point is from the "
            "run and coverage data — turn the action items into the next sprint's backlog."
        ),
        "_ui": {
            "pane": "requirements",
            "title": f"Retrospective · {len(runs)} runs",
            "markdown": _retro_markdown(len(runs), pass_rate, went_well, went_wrong, actions),
        },
    }


def _retro_markdown(span, pass_rate, went_well, went_wrong, actions) -> str:
    lines = [f"# Retrospective — last {span} run(s)", "", f"Pass rate **{pass_rate:.0%}**.", ""]
    lines += ["## What went well", ""] + [f"- {x}" for x in went_well] + [""]
    lines += ["## What didn't", ""] + [f"- {x}" for x in went_wrong] + [""]
    lines += ["## Action items", ""] + [f"{i}. {x}" for i, x in enumerate(actions, 1)]
    return "\n".join(lines) + "\n"
