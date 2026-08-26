"""Root-cause analysis.

An RCA that says "the test failed because the element was not found" is a
restatement, not an analysis. GaleQEA builds a real one in two passes:

**Pass 1 - deterministic evidence gathering (always runs, no model needed).**
Collect the failing step, the console errors and network failures that preceded
it, the failure's history, the healing record, sibling failures in the same run,
and the commits touching correlated paths. This alone is often enough, and it is
what makes RCA available in No-AI mode.

**Pass 2 - hypothesis ranking (model, when configured).** The model receives
*only* the structured evidence bundle and must cite the evidence ids it used.
An uncited hypothesis is dropped before it is ever shown, which is the cheapest
available defence against a confident, invented cause.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..ai.providers.base import LLMProvider, Message, NoAIModeError, ProviderError, Role
from ..models import FailureSignature, RCAReport, Run, RunStatus, RunStepRecord, RunTest, TestStat
from .flaky import assess
from .signatures import classify_error

CATEGORY_HINTS = {
    "environment": "infrastructure or environment, not the application under test",
    "test_defect": "the test itself is out of date with the application",
    "timing": "a race or an insufficient wait",
    "assertion": "the application behaved differently than the test expects",
    "server_error": "a backend fault surfaced in the UI",
    "auth_or_permission": "authentication, session or permissions",
    "accessibility": "an accessibility regression",
    "performance": "a performance regression",
}


@dataclass(slots=True)
class Evidence:
    id: str
    kind: str
    summary: str
    detail: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"id": self.id, "kind": self.kind, "summary": self.summary, "detail": self.detail}


def gather_evidence(db: Session, run_test: RunTest) -> list[Evidence]:
    """Deterministic, model-free. Every hypothesis must cite from this list."""
    run = db.get(Run, run_test.run_id)
    items: list[Evidence] = []

    steps = list(
        db.execute(
            select(RunStepRecord)
            .where(RunStepRecord.run_test_id == run_test.id)
            .order_by(RunStepRecord.index)
        ).scalars()
    )
    failing = next((s for s in steps if s.status in {"failed", "error"}), None)
    if failing:
        items.append(Evidence(
            id="E1", kind="failing_step",
            summary=f"step {failing.index} ({failing.action}) failed: {failing.error_message[:200]}",
            detail={
                "index": failing.index, "action": failing.action, "intent": failing.intent,
                "locator": failing.resolved_locator, "error": failing.error_message[:800],
            },
        ))
        passed_before = [s for s in steps if s.index < failing.index and s.status == "passed"]
        if passed_before:
            items.append(Evidence(
                id="E2", kind="progress",
                summary=f"the first {len(passed_before)} step(s) passed, so the journey "
                        f"reached '{passed_before[-1].intent[:80]}' before breaking",
                detail={"last_good_step": passed_before[-1].index},
            ))

    if run_test.console_errors:
        items.append(Evidence(
            id="E3", kind="console",
            summary=f"{len(run_test.console_errors)} browser console error(s); "
                    f"first: {run_test.console_errors[0].get('text', '')[:160]}",
            detail={"errors": run_test.console_errors[:8]},
        ))
    if run_test.network_failures:
        first = run_test.network_failures[0]
        items.append(Evidence(
            id="E4", kind="network",
            summary=f"{len(run_test.network_failures)} failed/5xx request(s); "
                    f"first: {first.get('url', '')[:120]} → {first.get('status') or first.get('failure')}",
            detail={"failures": run_test.network_failures[:8]},
        ))

    stat = db.execute(
        select(TestStat).where(TestStat.test_case_id == run_test.test_case_id)
    ).scalar_one_or_none()
    if stat:
        verdict = assess(stat)
        items.append(Evidence(
            id="E5", kind="history",
            summary=f"this test has run {stat.runs}x with a {stat.passes / max(1, stat.runs):.0%} "
                    f"pass rate; flake score {verdict.score:.2f} (confidence {verdict.confidence:.2f})",
            detail={"flake": verdict.as_dict(), "recent": (stat.outcome_window or [])[:8]},
        ))

    if run_test.failure_signature:
        known = db.execute(
            select(FailureSignature).where(
                FailureSignature.signature == run_test.failure_signature
            )
        ).scalar_one_or_none()
        if known:
            items.append(Evidence(
                id="E6", kind="signature",
                summary=f"this exact failure signature has occurred {known.occurrences}x "
                        f"across {len(known.affected_tests or [])} test(s)"
                        + (f"; tracked as {known.ticket_ref}" if known.ticket_ref else ""),
                detail={"occurrences": known.occurrences, "tests": known.affected_tests,
                        "known_issue": known.known_issue, "ticket": known.ticket_ref},
            ))

    siblings = list(
        db.execute(
            select(RunTest).where(
                RunTest.run_id == run_test.run_id,
                RunTest.status.in_([RunStatus.FAILED, RunStatus.ERROR]),
                RunTest.id != run_test.id,
            )
        ).scalars()
    )
    if siblings:
        shared = [s for s in siblings if s.failure_signature == run_test.failure_signature]
        items.append(Evidence(
            id="E7", kind="blast_radius",
            summary=(
                f"{len(siblings)} other test(s) failed in the same run"
                + (f", {len(shared)} with the identical signature - this points at a "
                   "shared cause rather than a single broken journey" if shared else "")
            ),
            detail={"sibling_keys": [s.test_key for s in siblings][:12],
                    "same_signature": [s.test_key for s in shared][:12]},
        ))

    if run_test.healed:
        items.append(Evidence(
            id="E8", kind="healing",
            summary="locators had to be healed during this run - the UI has drifted from "
                    "what the test was authored against",
            detail={},
        ))

    if run and run.git_sha:
        items.append(Evidence(
            id="E9", kind="version",
            summary=f"ran against {run.git_branch or 'unknown branch'} @ {run.git_sha[:10]} "
                    f"in environment '{run.environment}'",
            detail={"sha": run.git_sha, "branch": run.git_branch, "env": run.environment,
                    "ci": run.ci_metadata},
        ))

    return items


def heuristic_hypotheses(run_test: RunTest, evidence: list[Evidence]) -> list[dict]:
    """Model-free ranking. This is what No-AI mode reports."""
    kinds = {e.kind for e in evidence}
    coarse = classify_error(run_test.error_type, run_test.error_message)
    out: list[dict] = []

    if "network" in kinds:
        out.append({
            "cause": "a backend request failed or returned a server error, so the UI never "
                     "reached the state the test expected",
            "category": "server_error",
            "confidence": 0.72,
            "cites": ["E4", "E1"],
            "next_step": "check the failing endpoint's logs for the same timestamp",
        })
    if "console" in kinds:
        out.append({
            "cause": "an unhandled front-end exception interrupted rendering before the "
                     "asserted element appeared",
            "category": "product_defect",
            "confidence": 0.6,
            "cites": ["E3", "E1"],
            "next_step": "reproduce in a browser and read the stack in the console",
        })
    if coarse == "test_defect" or "healing" in kinds:
        out.append({
            "cause": "the application's markup changed and the test's locators no longer "
                     "describe the element",
            "category": "test_defect",
            "confidence": 0.68,
            "cites": [e.id for e in evidence if e.kind in {"failing_step", "healing"}],
            "next_step": "review the proposed heal and, if correct, approve it into the App Model",
        })
    if coarse == "environment":
        out.append({
            "cause": "the environment was unreachable or unstable; this is not a product defect",
            "category": "environment",
            "confidence": 0.8,
            "cites": ["E1"],
            "next_step": "confirm the target environment is up, then re-run",
        })
    history = next((e for e in evidence if e.kind == "history"), None)
    if history and history.detail.get("flake", {}).get("score", 0) >= 0.5:
        out.append({
            "cause": "the test is historically non-deterministic; this failure is likely "
                     "the same instability rather than a new regression",
            "category": "timing_flake",
            "confidence": round(0.5 + history.detail["flake"]["score"] * 0.3, 2),
            "cites": ["E5"],
            "next_step": "stabilise the wait condition, or quarantine while it is fixed",
        })
    blast = next((e for e in evidence if e.kind == "blast_radius"), None)
    if blast and blast.detail.get("same_signature"):
        out.append({
            "cause": "multiple tests failed with an identical signature, indicating one shared "
                     "upstream cause rather than several independent defects",
            "category": "dependency",
            "confidence": 0.75,
            "cites": ["E7"],
            "next_step": "fix the shared cause first and re-run the whole set",
        })

    if not out:
        out.append({
            "cause": CATEGORY_HINTS.get(coarse, "cause could not be determined from the "
                                                 "available evidence"),
            "category": coarse if coarse != "unknown" else "unknown",
            "confidence": 0.3,
            "cites": [e.id for e in evidence[:2]],
            "next_step": "open the Playwright trace and step through the failure",
        })

    out.sort(key=lambda h: h["confidence"], reverse=True)
    return out


async def analyze(
    db: Session,
    run_test: RunTest,
    *,
    provider: LLMProvider | None = None,
    project_id: str = "",
) -> RCAReport:
    evidence = gather_evidence(db, run_test)
    hypotheses = heuristic_hypotheses(run_test, evidence)
    generated_by = "heuristic"
    model = ""

    if provider is not None:
        try:
            refined = await _llm_hypotheses(provider, run_test, evidence, hypotheses)
            if refined:
                hypotheses = refined
                generated_by = "hybrid"
                model = getattr(provider, "model", "")
        except (NoAIModeError, ProviderError):
            pass  # deterministic analysis stands on its own

    top = hypotheses[0]
    report = RCAReport(
        project_id=project_id or "",
        run_id=run_test.run_id,
        run_test_id=run_test.id,
        test_case_id=run_test.test_case_id,
        signature=run_test.failure_signature,
        category=top.get("category", "unknown"),
        summary=top.get("cause", ""),
        hypotheses=hypotheses,
        evidence=[e.as_dict() for e in evidence],
        suggested_fix=top.get("next_step", ""),
        confidence=float(top.get("confidence", 0.0)),
        generated_by=generated_by,
        model=model,
    )
    db.add(report)
    db.flush()
    return report


async def _llm_hypotheses(
    provider: LLMProvider,
    run_test: RunTest,
    evidence: list[Evidence],
    baseline: list[dict],
) -> list[dict]:
    bundle = json.dumps([e.as_dict() for e in evidence], indent=2)[:14000]
    system = (
        "You perform root-cause analysis on an automated test failure.\n\n"
        "You are given a numbered evidence bundle. Produce ranked hypotheses.\n\n"
        "Hard rules:\n"
        "- Every hypothesis MUST cite at least one evidence id (E1, E2, ...). A "
        "hypothesis you cannot support with the bundle must not be returned.\n"
        "- Distinguish a defect in the *product* from a defect in the *test* from an "
        "*environment* problem. Getting this wrong wastes an engineer's afternoon.\n"
        "- Calibrate confidence honestly. 0.9 means the evidence is close to "
        "conclusive; use 0.3-0.5 when you are inferring.\n"
        "- 'next_step' must be a concrete action, not 'investigate further'.\n\n"
        "Return JSON: {\"hypotheses\": [{\"cause\": str, \"category\": str, "
        "\"confidence\": number, \"cites\": [str], \"next_step\": str}]}\n"
        "Valid categories: product_defect, test_defect, environment, data, "
        "timing_flake, dependency, infrastructure, requirement_gap, server_error."
    )
    user = (
        f"Test: {run_test.test_key} - {run_test.title}\n"
        f"Status: {run_test.status}\n"
        f"Error type: {run_test.error_type}\n"
        f"Error: {run_test.error_message[:1200]}\n\n"
        f"Evidence bundle:\n{bundle}\n\n"
        f"A deterministic analyser already proposed:\n{json.dumps(baseline, indent=2)[:2000]}\n"
        "Improve on it: merge, re-rank, correct, or discard those as the evidence warrants."
    )

    result = await provider.complete(
        [Message(role=Role.USER, content=user)],
        system=system,
        max_tokens=2000,
        response_format={
            "type": "object",
            "properties": {
                "hypotheses": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "cause": {"type": "string"},
                            "category": {"type": "string"},
                            "confidence": {"type": "number"},
                            "cites": {"type": "array", "items": {"type": "string"}},
                            "next_step": {"type": "string"},
                        },
                        "required": ["cause", "category", "confidence", "cites", "next_step"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["hypotheses"],
            "additionalProperties": False,
        },
    )

    body = result.text.strip()
    if body.startswith("```"):
        body = body.split("```", 2)[1].removeprefix("json").strip().rsplit("```", 1)[0]
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return []

    valid_ids = {e.id for e in evidence}
    out = [
        h for h in payload.get("hypotheses", [])
        # The citation requirement is enforced here, not merely requested.
        if h.get("cites") and set(h["cites"]) & valid_ids
    ]
    for h in out:
        h["cites"] = [c for c in h["cites"] if c in valid_ids]
        h["confidence"] = max(0.0, min(1.0, float(h.get("confidence", 0.5))))
        h["source"] = "model"
    out.sort(key=lambda h: h["confidence"], reverse=True)
    return out
