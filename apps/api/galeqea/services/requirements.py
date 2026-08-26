"""The requirement-to-test pipeline.

Deliberately runs in two tiers so the workflow is never blocked on a model:

* **Deterministic scaffolding** parses the document, extracts addressable
  requirements, and derives a baseline test case per requirement - happy path,
  the negative paths its acceptance criteria imply, and an exploratory charter
  for anything flagged ambiguous. This is what No-AI mode produces, and it is
  genuinely useful on its own.
* **Model enrichment**, when configured, deepens that scaffold: better titles,
  real edge cases, boundary values, and concrete executable steps.

Both tiers produce *proposals*. Nothing becomes a test without a human.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..ai.embeddings import cosine, local_embed
from ..ai.prompts import system_prompt
from ..ai.providers.base import LLMProvider, Message, NoAIModeError, ProviderError, Role
from ..core import audit
from ..core.safety import scan, wrap_untrusted
from ..engine import ingest
from ..models import (
    AgentRole,
    DocKind,
    Project,
    RequirementDoc,
    RequirementItem,
    StepAction,
    TestCase,
    TestCategory,
    TestStatus,
    TestStep,
)
from ..models.base import utcnow

#: Cosine similarity above which two proposals are treated as the same test.
DUPLICATE_THRESHOLD = 0.88


@dataclass(slots=True)
class IngestResult:
    doc: RequirementDoc
    items: list[RequirementItem] = field(default_factory=list)
    summary: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    injection: dict | None = None


def ingest_document(
    db: Session,
    *,
    project_id: str,
    filename: str,
    data: bytes,
    title: str = "",
    kind: str = DocKind.REQUIREMENT,
    mime_type: str = "",
    uploaded_by: str | None = None,
) -> IngestResult:
    extracted = ingest.extract(data, filename, mime_type)

    # A requirement document is untrusted input: it arrives from outside and its
    # text is fed to a model. Scan it, surface anything suspicious to the user,
    # and never strip it silently - a hidden attack is worse than a visible one.
    injection = scan(extracted.text)

    doc = RequirementDoc(
        project_id=project_id,
        title=title or filename,
        kind=kind,
        source_filename=filename,
        mime_type=extracted.mime_type,
        content=extracted.text,
        content_sha256=extracted.sha256,
        page_count=extracted.page_count,
        uploaded_by=uploaded_by,
        meta={
            "warnings": extracted.warnings,
            "injection_scan": injection.as_dict(),
            "bytes": len(data),
        },
    )
    db.add(doc)
    db.flush()

    items: list[RequirementItem] = []
    if kind == DocKind.REQUIREMENT:
        project = db.get(Project, project_id)
        prefix = (project.key if project else "REQ").upper()[:8]
        # A spreadsheet already separated its rows and labelled its columns.
        # Re-deriving requirements from the rendered text would throw that away.
        candidates = extracted.structured or ingest.split_requirements(
            extracted.text, prefix=prefix
        )
        for candidate in candidates:
            item = RequirementItem(
                doc_id=doc.id,
                project_id=project_id,
                ref=candidate.ref,
                title=candidate.title,
                text=candidate.text,
                section=candidate.section,
                kind=candidate.kind,
                risk=candidate.risk,
                acceptance_criteria=candidate.acceptance_criteria,
                open_questions=candidate.open_questions,
                embedding=local_embed(f"{candidate.title} {candidate.text}"),
            )
            db.add(item)
            items.append(item)
        db.flush()

    audit.record(
        db,
        action="requirement.ingested",
        actor_id=uploaded_by,
        project_id=project_id,
        resource_type="requirement_doc",
        resource_id=doc.id,
        detail={
            "filename": filename, "requirements": len(items),
            "sha256": extracted.sha256[:16],
            "injection_suspicious": injection.suspicious,
        },
    )
    db.commit()

    return IngestResult(
        doc=doc,
        items=items,
        summary=ingest.summarize(
            [
                ingest.CandidateRequirement(
                    ref=i.ref, title=i.title, text=i.text, section=i.section,
                    kind=i.kind, risk=i.risk,
                    acceptance_criteria=i.acceptance_criteria,
                    open_questions=i.open_questions,
                )
                for i in items
            ]
        ),
        warnings=extracted.warnings,
        injection=injection.as_dict() if injection.suspicious else None,
    )


# --------------------------------------------------------------------------- #
# Proposal generation
# --------------------------------------------------------------------------- #
def scaffold_proposals(db: Session, project_id: str, items: list[RequirementItem]) -> list[dict]:
    """Deterministic baseline. Runs with no model and is useful on its own."""
    from ..intelligence import testdesign

    proposals: list[dict] = []
    for item in items:
        # Classical test design, applied by rule. This is the part of "AI reads
        # the requirement and writes tests" that needs no model: once the input
        # domain is known, boundary and partition analysis is arithmetic.
        # The splitter sets `title` to the first sentence of `text`, so naively
        # concatenating them analyses the same phrase twice and every pattern
        # matches twice — producing duplicate variables and duplicate values.
        source = item.text if item.title and item.title in item.text else (
            f"{item.title}. {item.text}".strip(". ")
        )
        design = testdesign.analyse(source, subject=item.ref)
        if design.values:
            proposals.append(_design_proposal(item, design))
        if design.decision_table:
            proposals.append(_decision_proposal(item, design))

        happy_title = _happy_title(item)
        proposals.append({
            "title": happy_title,
            "category": TestCategory.AUTOMATED if item.kind != "non_functional" else TestCategory.MANUAL,
            "priority": _priority(item.risk),
            "risk": item.risk,
            "rationale": (
                f"Verifies the primary obligation of {item.ref}. Derived from the "
                "requirement text; steps still need authoring against the real UI."
            ),
            "requirement_refs": [item.ref],
            "tags": _tags(item),
            "steps": _scaffold_steps(item),
            "source": "deterministic",
        })

        for index, criterion in enumerate(item.acceptance_criteria[:4]):
            proposals.append({
                "title": f"{item.ref} — acceptance criterion {index + 1}: {criterion[:90]}",
                "category": TestCategory.AUTOMATED,
                "priority": _priority(item.risk),
                "risk": item.risk,
                "rationale": f"Directly exercises an acceptance criterion stated in {item.ref}.",
                "requirement_refs": [item.ref],
                "tags": _tags(item),
                "steps": [{"action": StepAction.NOTE, "intent": criterion[:400],
                           "expected": "as stated in the acceptance criterion"}],
                "source": "deterministic",
            })

        if item.risk in {"high", "critical"}:
            proposals.append({
                "title": f"{item.ref} — negative path and error handling",
                "category": TestCategory.AUTOMATED,
                "priority": "high",
                "risk": item.risk,
                "rationale": (
                    f"{item.risk.title()}-risk requirements need their failure behaviour "
                    "verified, not only the happy path. Confirms the user is told what "
                    "went wrong and nothing is silently lost."
                ),
                "requirement_refs": [item.ref],
                "tags": [*_tags(item), "negative"],
                "steps": [{"action": StepAction.NOTE,
                           "intent": _negative_intent(item),
                           "expected": "a clear, actionable error; no partial state is committed"}],
                "source": "deterministic",
            })

        if item.open_questions:
            proposals.append({
                "title": f"{item.ref} — exploratory: unresolved ambiguity",
                "category": TestCategory.EXPLORATORY,
                "priority": "medium",
                "risk": item.risk,
                "rationale": (
                    "This requirement contains wording that is not precise enough to assert "
                    "against. Rather than inventing a threshold, this charter time-boxes "
                    "exploration and turns the findings into a question for the author."
                ),
                "requirement_refs": [item.ref],
                "tags": [*_tags(item), "exploratory"],
                "charter": (
                    f"Explore {item.title[:140]} for 30 minutes. Open questions to resolve: "
                    + "; ".join(item.open_questions)
                ),
                "steps": [],
                "source": "deterministic",
            })

    return dedupe(proposals)


async def enrich_proposals(
    provider: LLMProvider,
    *,
    items: list[RequirementItem],
    baseline: list[dict],
    project_context: str = "",
) -> list[dict]:
    """Deepen the scaffold with a model. Failure here degrades, never blocks."""
    requirement_text = "\n\n".join(
        f"[{i.ref}] risk={i.risk} kind={i.kind}\n{i.title}\n{i.text[:800]}"
        + (f"\nAcceptance criteria: {'; '.join(i.acceptance_criteria)}" if i.acceptance_criteria else "")
        + (f"\nOpen questions: {'; '.join(i.open_questions)}" if i.open_questions else "")
        for i in items[:40]
    )

    schema = {
        "type": "object",
        "properties": {
            "tests": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "category": {"type": "string", "enum": ["manual", "exploratory", "automated"]},
                        "priority": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "risk": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
                        "rationale": {"type": "string"},
                        "requirement_refs": {"type": "array", "items": {"type": "string"}},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "preconditions": {"type": "array", "items": {"type": "string"}},
                        "charter": {"type": "string"},
                        "steps": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "action": {"type": "string"},
                                    "intent": {"type": "string"},
                                    "expected": {"type": "string"},
                                    "target_role": {"type": "string"},
                                    "target_name": {"type": "string"},
                                    "value": {"type": "string"},
                                },
                                "required": ["action", "intent"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["title", "category", "priority", "rationale", "requirement_refs", "steps"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["tests"],
        "additionalProperties": False,
    }

    prompt = (
        f"{project_context}\n\n"
        "Design a test set for these requirements.\n\n"
        + wrap_untrusted(requirement_text, source="requirement document", kind="requirements")
        + "\n\nA deterministic pass already produced this baseline. Improve on it: sharpen "
        "titles, add the edge cases and boundary conditions it missed, correct any "
        "mis-categorisation, and give automated tests concrete steps with role/name "
        "targets. Do not repeat a baseline test unchanged, and do not propose two tests "
        "that differ only by a data value.\n\n"
        f"{json.dumps(baseline[:25], indent=2)[:8000]}"
    )

    result = await provider.complete(
        [Message(role=Role.USER, content=prompt)],
        system=system_prompt(AgentRole.TEST_DESIGNER),
        max_tokens=16000,
        response_format=schema,
    )

    body = result.text.strip()
    if body.startswith("```"):
        body = body.split("```", 2)[1].removeprefix("json").strip().rsplit("```", 1)[0]
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return baseline

    known_refs = {i.ref.upper() for i in items}
    enriched: list[dict] = []
    for test in payload.get("tests", []):
        refs = [r.upper() for r in test.get("requirement_refs", []) if r.upper() in known_refs]
        if not refs:
            # A test that traces to nothing cannot be reviewed against anything.
            continue
        test["requirement_refs"] = refs
        test["steps"] = [_normalise_step(s) for s in test.get("steps", [])]
        test["source"] = "model"
        enriched.append(test)

    # The model's output replaces the baseline only where it covers the same
    # ground. Anything it dropped comes back, because a requirement losing its
    # test to an enrichment pass is a silent regression in coverage.
    enriched_refs = {r.upper() for t in enriched for r in t.get("requirement_refs", [])}
    preserved = [
        proposal for proposal in baseline
        if not {r.upper() for r in proposal.get("requirement_refs", [])} & enriched_refs
    ]
    return dedupe([*enriched, *preserved])


async def generate(
    db: Session,
    *,
    project_id: str,
    doc_id: str | None = None,
    provider: LLMProvider | None = None,
) -> dict:
    stmt = select(RequirementItem).where(RequirementItem.project_id == project_id)
    if doc_id:
        stmt = stmt.where(RequirementItem.doc_id == doc_id)
    items = list(db.execute(stmt.order_by(RequirementItem.ref)).scalars())
    if not items:
        return {"proposals": [], "note": "No requirements have been ingested yet."}

    baseline = scaffold_proposals(db, project_id, items)
    source = "deterministic"

    if provider is not None:
        try:
            baseline = await enrich_proposals(
                provider, items=items, baseline=baseline,
                project_context=_context(db, project_id),
            )
            source = "hybrid"
        except (NoAIModeError, ProviderError) as exc:
            return {
                "proposals": baseline,
                "source": "deterministic",
                "note": f"Model enrichment unavailable ({exc}); showing the deterministic scaffold.",
            }

    existing = _existing_fingerprints(db, project_id)
    final: list[dict] = []
    suppressed = 0
    for proposal in baseline:
        vector = local_embed(f"{proposal['title']} {proposal.get('rationale', '')}")
        refs = {r.upper() for r in proposal.get("requirement_refs", [])}
        already = {r.upper() for p in final for r in p.get("requirement_refs", [])}
        # Same rule as within-batch dedup: never suppress the only coverage a
        # requirement has, however similar it looks to something that exists.
        if any(cosine(vector, other) >= DUPLICATE_THRESHOLD for other in existing) and not (refs - already):
            suppressed += 1
            continue
        proposal["_embedding"] = vector
        final.append(proposal)

    final, backfilled = ensure_coverage(db, project_id, items, final)

    covered = {r.upper() for p in final for r in p.get("requirement_refs", [])}
    uncovered = [i.ref for i in items if i.ref.upper() not in covered]

    note = f"{len(final)} proposal(s) from {len(items)} requirement(s)."
    if suppressed:
        note += f" {suppressed} near-duplicate(s) of existing tests were suppressed."
    if backfilled:
        note += (
            f" {len(backfilled)} requirement(s) had no proposal and were backfilled "
            f"so none is left untested: {', '.join(backfilled[:6])}"
            + ("…" if len(backfilled) > 6 else "") + "."
        )
    note += (
        " Every requirement now has at least one test."
        if not uncovered else
        f" WARNING: {len(uncovered)} requirement(s) still have no test: {', '.join(uncovered[:6])}."
    )
    note += " Nothing is created until you approve it."

    return {
        "proposals": final,
        "source": source,
        "requirement_count": len(items),
        "suppressed_duplicates": suppressed,
        "backfilled": backfilled,
        "uncovered": uncovered,
        # The guarantee, stated as a checkable fact rather than an intention.
        "every_requirement_covered": not uncovered,
        "note": note,
    }


def persist_proposals(
    db: Session, *, project_id: str, proposals: list[dict], author_kind: str = "agent"
) -> list[TestCase]:
    """Write proposals as PROPOSED test cases for the review board."""
    project = db.get(Project, project_id)
    prefix = (project.key if project else "TST").upper()
    base = db.execute(
        select(func.count()).select_from(TestCase).where(TestCase.project_id == project_id)
    ).scalar_one()

    created: list[TestCase] = []
    for offset, proposal in enumerate(proposals, start=1):
        case = TestCase(
            project_id=project_id,
            key=f"{prefix}-T-{base + offset:04d}",
            title=proposal["title"][:400],
            description=proposal.get("description", ""),
            category=proposal.get("category", TestCategory.MANUAL),
            status=TestStatus.PROPOSED,
            priority=proposal.get("priority", "medium"),
            risk=proposal.get("risk", "medium"),
            rationale=proposal.get("rationale", ""),
            requirement_refs=proposal.get("requirement_refs", []),
            tags=proposal.get("tags", []),
            preconditions=proposal.get("preconditions", []),
            charter=proposal.get("charter", ""),
            # The derived values are the point of a design-technique proposal;
            # dropping them would leave a step list nobody can re-derive.
            test_data=proposal.get("test_data") or {},
            embedding=proposal.get("_embedding") or local_embed(proposal["title"]),
            provenance={
                "origin": proposal.get("source", "deterministic"),
                "author_kind": author_kind,
                "generated_at": utcnow().isoformat(),
            },
        )
        db.add(case)
        db.flush()
        for index, step in enumerate(proposal.get("steps", [])):
            db.add(TestStep(
                test_case_id=case.id,
                index=index,
                action=step.get("action", StepAction.NOTE),
                intent=step.get("intent", ""),
                expected=step.get("expected", ""),
                target=step.get("target", {}),
                value=step.get("value", {}),
                options=step.get("options", {}),
            ))
        created.append(case)
    db.flush()
    return created


# --------------------------------------------------------------------------- #
def dedupe(proposals: list[dict]) -> list[dict]:
    """Suppress near-identical proposals - but never the last one for a requirement.

    Two requirements can be worded almost identically ("the user must be able to
    delete their account" / "the user must be able to close their account") and
    their happy-path proposals then collide. Dropping one silently leaves a
    requirement with no test at all, which is the exact failure the coverage
    guarantee exists to prevent. Similarity may remove a *duplicate*; it may
    never remove the only coverage a requirement has.
    """
    kept: list[tuple[dict, list[float]]] = []
    covered: set[str] = set()

    for proposal in proposals:
        refs = {r.upper() for r in proposal.get("requirement_refs", [])}
        vector = local_embed(f"{proposal['title']} {proposal.get('rationale', '')}")
        similar = any(cosine(vector, other) >= DUPLICATE_THRESHOLD for _, other in kept)

        # Keep it anyway if it is the sole coverage for any requirement it cites.
        sole_coverage = bool(refs - covered)
        if similar and not sole_coverage:
            continue

        kept.append((proposal, vector))
        covered |= refs

    return [p for p, _ in kept]


def ensure_coverage(
    db: Session, project_id: str, items: list[RequirementItem], proposals: list[dict]
) -> tuple[list[dict], list[str]]:
    """Guarantee every requirement ends up with at least one test.

    Three earlier steps can each drop the last proposal for a requirement:
    similarity de-duplication, model enrichment replacing the baseline, and
    suppression against tests that already exist. A requirement silently
    arriving with zero coverage is the worst possible outcome here - it looks
    like success. So coverage is *verified* at the end rather than assumed, and
    anything missing is backfilled and reported.
    """
    from ..models import TestStatus

    proposed_refs = {
        ref.upper() for proposal in proposals for ref in proposal.get("requirement_refs", [])
    }
    existing_refs: set[str] = set()
    for case in db.execute(
        select(TestCase).where(
            TestCase.project_id == project_id,
            TestCase.status.in_([TestStatus.APPROVED, TestStatus.PROPOSED]),
        )
    ).scalars():
        existing_refs |= {r.upper() for r in (case.requirement_refs or [])}

    backfilled: list[str] = []
    for item in items:
        if item.ref.upper() in proposed_refs or item.ref.upper() in existing_refs:
            continue
        proposals.append({
            "title": _happy_title(item),
            "category": (
                TestCategory.MANUAL if item.kind == "non_functional" else TestCategory.AUTOMATED
            ),
            "priority": _priority(item.risk),
            "risk": item.risk,
            "rationale": (
                f"Guarantees coverage of {item.ref}. Every requirement gets at least one "
                "test; this one was added because nothing else referenced it."
            ),
            "requirement_refs": [item.ref],
            "tags": [*_tags(item), "coverage-guarantee"],
            "steps": _scaffold_steps(item),
            "source": "coverage_guarantee",
        })
        backfilled.append(item.ref)

    return proposals, backfilled


def _existing_fingerprints(db: Session, project_id: str) -> list[list[float]]:
    rows = db.execute(
        select(TestCase.embedding).where(
            TestCase.project_id == project_id,
            TestCase.status.in_([TestStatus.APPROVED, TestStatus.PROPOSED]),
        )
    ).scalars()
    return [r for r in rows if r]


def _design_proposal(item: RequirementItem, design) -> dict:
    """One data-driven case carrying every derived value and its technique.

    Kept as a single case with a data table rather than one case per value: a
    reviewer approving "the length boundaries of REQ-014" is making one
    judgement, and splitting it into eleven near-identical cases makes the
    review worse while making the coverage number look better.
    """
    from ..intelligence.testdesign import summarise

    valid = [v for v in design.values if v.partition == "valid"]
    invalid = [v for v in design.values if v.partition == "invalid"]
    unspecified = [v for v in design.values if v.partition == "unspecified"]

    steps = [
        {"action": StepAction.NOTE,
         "intent": f"Set up the preconditions for {item.ref}",
         "expected": "the system is in the state the requirement assumes"},
    ]
    for value in design.values[:24]:
        steps.append({
            "action": StepAction.NOTE,
            "intent": (
                f"[{value.technique.replace('_', ' ')}] set {value.variable} to "
                f"{value.value!r} — {value.label}"
            ),
            "expected": value.expected,
        })

    questions = [
        f"{v.variable}: {v.expected}" for v in unspecified
    ] + list(item.open_questions or [])

    return {
        "title": f"{item.ref} — boundaries and partitions of {_domain_label(design)}",
        "category": TestCategory.AUTOMATED,
        "priority": _priority(item.risk),
        "risk": item.risk,
        "rationale": (
            f"Applies boundary value analysis and equivalence partitioning to the input "
            f"domain stated in {item.ref}: {summarise(design)} "
            "Off-by-one at a stated limit is the defect this exists to catch, so the "
            "values either side of each boundary are computed rather than guessed."
        ),
        "requirement_refs": [item.ref],
        "tags": [*_tags(item), "boundary-value", "equivalence-partition"],
        "preconditions": list(item.acceptance_criteria[:2]),
        "steps": steps,
        "test_data": {
            "variables": [v.as_dict() for v in design.variables],
            "valid": [v.as_dict() for v in valid],
            "invalid": [v.as_dict() for v in invalid],
            "unspecified": [v.as_dict() for v in unspecified],
        },
        "open_questions": questions,
        "source": "test_design",
    }


def _decision_proposal(item: RequirementItem, design) -> dict:
    steps = [{
        "action": StepAction.NOTE,
        "intent": " · ".join(
            f"{condition} = {'true' if value else 'false'}"
            for condition, value in row.conditions.items()
        ),
        "expected": row.expected,
    } for row in design.decision_table]

    return {
        "title": f"{item.ref} — decision table over {len(design.conditions)} condition(s)",
        "category": TestCategory.AUTOMATED,
        "priority": _priority(item.risk),
        "risk": item.risk,
        "rationale": (
            f"{item.ref} combines {len(design.conditions)} conditions. Testing only the "
            "all-true path leaves every other combination unverified, which is where "
            "compound-condition defects live."
        ),
        "requirement_refs": [item.ref],
        "tags": [*_tags(item), "decision-table"],
        "steps": steps,
        "test_data": {"conditions": design.conditions,
                      "rows": [r.as_dict() for r in design.decision_table]},
        "source": "test_design",
    }


def _domain_label(design) -> str:
    names = [v.name for v in design.variables][:2]
    return ", ".join(names) if names else "the stated input domain"


def _happy_title(item: RequirementItem) -> str:
    text = item.title.rstrip(".")
    if text.lower().startswith(("the system", "the user", "users", "a user")):
        return f"{item.ref} — {text}"
    return f"{item.ref} — verify {text[0].lower()}{text[1:]}" if text else item.ref


def _negative_intent(item: RequirementItem) -> str:
    """Phrase the negative path without grammatically mangling the requirement.

    A requirement title may be a statement ("Payment failures must be shown…"),
    a user story, or a fragment. Splicing any of those into "attempt to X"
    produces nonsense, so the requirement is quoted rather than conjugated.
    """
    subject = item.title.rstrip(". ").strip()[:180]
    return (
        f"Exercise the failure path for {item.ref} — \u201c{subject}\u201d. "
        "Drive it with invalid, missing or out-of-range input and observe what "
        "the user is told."
    )


def _priority(risk: str) -> str:
    return {"critical": "critical", "high": "high", "medium": "medium", "low": "low"}.get(risk, "medium")


def _tags(item: RequirementItem) -> list[str]:
    tags = [item.kind]
    if item.risk in {"high", "critical"}:
        tags.append("critical-path")
    if item.section:
        tags.append(item.section.lower().replace(" ", "-")[:24])
    return [t for t in tags if t][:5]


def _scaffold_steps(item: RequirementItem) -> list[dict]:
    return [
        {"action": StepAction.NOTE,
         "intent": f"Set up the preconditions for {item.ref}",
         "expected": "the system is in the state the requirement assumes"},
        {"action": StepAction.NOTE,
         "intent": item.title[:400],
         "expected": (item.acceptance_criteria[0][:300] if item.acceptance_criteria
                      else "the behaviour described in the requirement is observed")},
    ]


def _normalise_step(step: dict) -> dict:
    action = step.get("action", "note")
    valid = {a.value for a in StepAction}
    if action not in valid:
        action = StepAction.NOTE

    target: dict = {}
    if step.get("target_role") or step.get("target_name"):
        role, name = step.get("target_role", ""), step.get("target_name", "")
        target = {
            "role": role,
            "accessible_name": name,
            "ladder": [{"kind": "role", "role": role, "name": name}] if role and name else [],
        }
    value = {"text": step["value"]} if step.get("value") else {}
    return {
        "action": action,
        "intent": step.get("intent", "")[:1000],
        "expected": step.get("expected", "")[:1000],
        "target": target,
        "value": value,
        "options": {},
    }


def _context(db: Session, project_id: str) -> str:
    project = db.get(Project, project_id)
    if not project:
        return ""
    return (
        f"Application under test: {project.name}. "
        f"Environments: {', '.join(project.environments or {}) or 'not configured'}."
    )
