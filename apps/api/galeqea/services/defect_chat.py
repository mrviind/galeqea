"""Deterministic chat verb: "file a bug for <failure>" → a gated defect approval.

No model needed. Resolves which failing result the tester means (an explicit result
id, "run #N <KEY>", a bare test key, or "the last failure"), then files a
``defect.create`` approval; nothing reaches the tracker until a human accepts.
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core import approvals
from ..models import Project, Run, RunTest, User
from ..models.testing import TERMINAL_RUN_STATES

_FAILED = {"failed", "error", "needs_review"} & set(TERMINAL_RUN_STATES) or {"failed", "error"}
_TRIGGER = re.compile(r"\b(?:file|open|raise|log)\s+(?:a\s+)?(?:bug|defect|issue)\b", re.I)


def _latest_failed(db: Session, project_id: str, *, test_key: str | None = None,
                   run_number: int | None = None) -> RunTest | None:
    q = (select(RunTest).join(Run, RunTest.run_id == Run.id)
         .where(Run.project_id == project_id, RunTest.status.in_(_FAILED)))
    if test_key:
        q = q.where(RunTest.test_key == test_key)
    if run_number is not None:
        q = q.where(Run.number == run_number)
    q = q.order_by(RunTest.finished_at.is_(None), RunTest.finished_at.desc())
    return db.execute(q).scalars().first()


def _resolve(db: Session, project_id: str, text: str) -> RunTest | None:
    # explicit result id (24-char hex from new_id)
    m = re.search(r"\b(?:result\s+)?([0-9a-f]{24})\b", text, re.I)
    if m:
        rt = db.get(RunTest, m.group(1))
        if rt and rt.run and rt.run.project_id == project_id:
            return rt
    run_number = None
    mr = re.search(r"run\s+#?(\d+)", text, re.I)
    if mr:
        run_number = int(mr.group(1))
    # a test key like APP-T-03 / DEMO-12
    mk = re.search(r"\b([A-Z][A-Z0-9]+-[A-Z0-9-]*\d[A-Z0-9-]*)\b", text)
    test_key = mk.group(1) if mk else None
    return _latest_failed(db, project_id, test_key=test_key, run_number=run_number)


def try_handle(db: Session, project: Project, text: str, user: User) -> tuple[str, list[dict]] | None:
    if not _TRIGGER.search(text):
        return None
    rt = _resolve(db, project.id, text)
    if rt is None:
        return ("I couldn't find a failing result to file a bug for. Try "
                "\"file a bug for run #42 APP-T-03\", a result id, or run the suite first.", [])

    from ..services import defects
    provider = defects.connected_tracker(db, project.id)
    if provider is None:
        return ("No issue tracker is connected. Add Jira, GitHub or GitLab under "
                "Settings → Integrations, then say \"file a bug for …\" again.", [])

    req = approvals.request(
        db, action="defect.create",
        title=f"File a bug for {rt.test_key or rt.title} ({provider})",
        project_id=project.id, resource_type="run_test", resource_id=rt.id,
        summary=f"{rt.error_type}: {rt.error_message}".strip(": ")[:300],
        payload={"arguments": {"run_test_id": rt.id, "provider": provider}},
        requested_by=user.id, requested_by_kind="agent",
    )
    db.commit()
    return (f"Queued a {provider} bug for {rt.test_key or rt.title}. Approval #{req.id} is "
            f"waiting for a human in the Approvals view. Nothing reaches {provider} until "
            f"it's accepted.",
            [{"type": "approval_pending", "approval_id": req.id, "action": "defect.create",
              "provider": provider, "result_id": rt.id}])
