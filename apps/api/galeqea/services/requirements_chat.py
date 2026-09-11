"""Deterministic chat verbs for the requirements → tests pipeline (WO#9-C).

- "generate tests from <doc>"           → run generation for a document
- "what's ambiguous in <doc>?"          → the open questions / assumptions to resolve
- "export tests for REQ-014 as testrail csv" → a download for a scoped export
- "show traceability" / "... for <x>"   → the RTM summary
- "archive requirement doc <id>"        → hide a superseded doc (confirm to apply)
- "dedupe requirement docs"             → remove byte-identical duplicate docs (confirm to apply)

Each returns ``(reply_text, blocks) | None``; ``None`` means "not my verb".
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import settings
from ..exporters import EXPORT_FORMATS
from ..models import Project, RequirementDoc, RequirementItem, User
from ..reports.common import api_href

_FORMAT_WORDS = {
    "testrail": "testrail_csv", "test rail": "testrail_csv",
    "testrail steps": "testrail_steps_csv", "xray": "xray_csv", "qase": "qase_json",
    "gherkin": "gherkin", "cucumber": "gherkin", "feature": "gherkin",
    "playwright": "playwright",
}


def _find_doc(db: Session, project: Project, phrase: str) -> RequirementDoc | None:
    phrase = phrase.strip().strip("\"'")
    docs = list(db.execute(
        select(RequirementDoc).where(RequirementDoc.project_id == project.id)
        .order_by(RequirementDoc.created_at.desc())
    ).scalars())
    if not docs:
        return None
    if not phrase or phrase in ("the doc", "the document", "it", "the latest", "latest"):
        return docs[0]
    low = phrase.lower()
    for d in docs:
        if d.id == phrase or low in (d.title or "").lower() or low in (d.source_filename or "").lower():
            return d
    return docs[0]


async def try_handle(db: Session, project: Project, text: str, user: User) -> tuple[str, list[dict]] | None:
    low = text.strip().lower()

    if m := re.search(r"(?:what'?s|what is|find|show)\s+(?:the\s+)?ambigu\w*\s+(?:in\s+)?(.*)", low):
        return _ambiguous(db, project, m.group(1))

    if m := re.search(r"export\s+tests?\s+(?:for\s+([\w.\- ]+?)\s+)?(?:as|to|in)\s+([\w ]+?)\s*(?:csv|json)?$", low):
        return _export(db, project, ref=(m.group(1) or "").strip(), fmt_word=m.group(2).strip())

    if re.search(r"\b(generate|create|draft)\s+tests?\s+(?:from|for)\b", low):
        m = re.search(r"tests?\s+(?:from|for)\s+(.*)", low)
        return await _generate(db, project, (m.group(1) if m else "").strip())

    if re.search(r"\b(show|display|open)\s+(?:the\s+)?traceab", low):
        return _traceability(db, project)

    if m := re.search(r"archive\s+requirement\s+doc(?:ument)?\s+([\w-]+)", low):
        return _archive(db, project, m.group(1), confirm="confirm" in low or "apply" in low)

    if re.search(r"dedupe?\s+requirement\s+doc", low) or re.search(r"deduplicate\s+requirement", low):
        return _dedupe(db, project, apply="confirm" in low or "apply" in low or "yes" in low)

    return None


def _ambiguous(db: Session, project: Project, phrase: str) -> tuple[str, list[dict]]:
    doc = _find_doc(db, project, phrase)
    if doc is None:
        return ("No requirement documents have been ingested yet.", [])
    items = list(db.execute(
        select(RequirementItem).where(RequirementItem.doc_id == doc.id)).scalars())
    flagged = [(i.ref, q) for i in items for q in (i.open_questions or [])]
    if not flagged:
        return (f"Nothing ambiguous flagged in **{doc.title}**. Every requirement has a "
                "measurable obligation. (Generation would still tag any it had to assume.)", [])
    lines = "\n".join(f"- **{ref}**: {q}" for ref, q in flagged[:20])
    return (f"**{len(flagged)}** open question(s) in **{doc.title}**, worth confirming with "
            f"the author before asserting:\n{lines}\n\nTests still generate on a best-effort "
            "reading, tagged `ASSUMPTION:` so nothing silently guesses.",
            [{"type": "ambiguities", "doc_id": doc.id, "questions":
              [{"ref": r, "question": q} for r, q in flagged]}])




async def _generate(db: Session, project: Project, phrase: str) -> tuple[str, list[dict]]:
    from ..ai.providers.registry import default_provider
    from ..services import requirements as req
    doc = _find_doc(db, project, phrase)
    if doc is None:
        return ("No requirement documents to generate from. Upload a PRD first "
                "(Requirements → upload).", [])
    provider = default_provider() if settings.ai_enabled else None
    result = await req.generate(db, project_id=project.id, doc_id=doc.id, provider=provider)
    proposals = result.get("proposals", [])
    created = []
    if proposals:
        created = req.persist_proposals(db, project_id=project.id, proposals=proposals)
        db.commit()
    covered = result.get("coverage", {})
    src = result.get("source", "deterministic")
    return (f"Generated **{len(created)}** proposed test case(s) from **{doc.title}** "
            f"({src}). They're on the review board as PROPOSED. Approve, edit, or "
            "regenerate before they count as coverage.",
            [{"type": "tests_generated", "doc_id": doc.id, "created": len(created),
              "source": src, "coverage": covered}])


def _export(db: Session, project: Project, *, ref: str, fmt_word: str) -> tuple[str, list[dict]]:
    fmt = None
    for word, f in _FORMAT_WORDS.items():
        if word in fmt_word:
            fmt = f
            break
    if fmt is None or fmt not in EXPORT_FORMATS:
        return (f"I can export as: {', '.join(sorted(set(_FORMAT_WORDS.values())))}. "
                "For example: \"export tests for REQ-014 as testrail csv\".", [])
    url = api_href(project.id, "tests", "export.bulk") + f"?format={fmt}"
    if ref:
        url += f"&ref={ref.upper().strip()}"
    scope = f"for {ref.upper()}" if ref else "for the whole project"
    return (f"Ready to export tests {scope} as **{fmt.replace('_', ' ')}**. "
            f"Download: {url}",
            [{"type": "export", "format": fmt, "ref": ref.upper() if ref else "",
              "url": url}])


def _traceability(db: Session, project: Project) -> tuple[str, list[dict]]:
    from ..intelligence.coverage import traceability_matrix
    matrix = traceability_matrix(db, project.id)
    total = len(matrix)
    covered = sum(1 for r in matrix if r["covered"])
    rules_total = sum(r.get("rules_total", 0) for r in matrix)
    rules_covered = sum(r.get("rules_covered", 0) for r in matrix)
    p1 = [r["ref"] for r in matrix if r.get("priority") == "P1" and not r["covered"]]
    url = api_href(project.id, "traceability", "report.html")
    msg = (f"**Traceability**: {covered}/{total} requirements covered, "
           f"{rules_covered}/{rules_total} rules covered.")
    if p1:
        msg += f" Uncovered **P1**: {', '.join(p1[:8])}."
    msg += f" Full matrix: {url}"
    return (msg, [{"type": "traceability", "summary": {
        "requirements": total, "covered": covered,
        "rules_total": rules_total, "rules_covered": rules_covered,
        "uncovered_p1": p1}, "url": url}])


def _archive(db: Session, project: Project, doc_id: str, *, confirm: bool) -> tuple[str, list[dict]]:
    from ..services import requirements as req
    doc = db.get(RequirementDoc, doc_id)
    if doc is None or doc.project_id != project.id:
        return (f"No requirement doc `{doc_id}` in this project.", [])
    if not confirm:
        return (f"Archive **{doc.title}** ({doc.id})? It will be hidden from the matrix and "
                "lists (reversible). Say \"archive requirement doc "
                f"{doc.id} confirm\" to apply.",
                [{"type": "confirm_required", "action": "archive_doc", "doc_id": doc.id}])
    result = req.archive_requirement_doc(db, project_id=project.id, doc_id=doc.id)
    db.commit()
    return (f"Archived **{doc.title}**. It no longer counts in coverage or the RTM.",
            [{"type": "doc_archived", **result}])


def _dedupe(db: Session, project: Project, *, apply: bool) -> tuple[str, list[dict]]:
    from ..services import requirements as req
    preview = req.dedupe_requirement_docs(db, project_id=project.id, apply=False)
    groups = preview["duplicate_groups"]
    if not groups:
        return ("No duplicate requirement documents, so nothing to tidy. "
                "(Re-uploading the same file already updates in place.)", [])
    n = sum(len(g["duplicates"]) for g in groups)
    if not apply:
        return (f"Found **{len(groups)}** duplicated document(s). {n} redundant copy(ies) "
                "would be removed, keeping the newest of each. Say \"dedupe requirement docs "
                "confirm\" to apply.",
                [{"type": "confirm_required", "action": "dedupe_docs", **preview}])
    result = req.dedupe_requirement_docs(db, project_id=project.id, apply=True)
    db.commit()
    return (f"Removed **{result['summary']['removed']}** duplicate document(s); the RTM no "
            "longer double-counts them.", [{"type": "docs_deduped", **result}])
