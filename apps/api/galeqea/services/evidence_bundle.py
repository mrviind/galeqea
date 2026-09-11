"""One-click evidence bundle for a failing result: a single zip a tester can attach
to a bug or hand to a developer: every artifact (screenshot / video / trace / console /
HAR) plus a metadata.json describing the failure.
"""

from __future__ import annotations

import io
import json
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Artifact, DefectLink, Run, RunStepRecord, RunTest
from .artifacts import open_artifact


class EvidenceError(RuntimeError):
    pass


def build_zip(db: Session, run_test_id: str) -> tuple[bytes, str]:
    """Return (zip bytes, filename) for a result's evidence bundle."""
    rt = db.get(RunTest, run_test_id)
    if rt is None:
        raise EvidenceError("no such result")
    run = db.get(Run, rt.run_id)
    artifacts = list(db.execute(
        select(Artifact).where(Artifact.run_test_id == rt.id)).scalars())
    steps = db.execute(
        select(RunStepRecord).where(RunStepRecord.run_test_id == rt.id)
        .order_by(RunStepRecord.index)).scalars().all()
    defects = list(db.execute(
        select(DefectLink).where(DefectLink.result_id == rt.id)).scalars())

    metadata = {
        "test": {"key": rt.test_key, "title": rt.title, "status": rt.status,
                 "browser": rt.browser, "error_type": rt.error_type,
                 "error_message": rt.error_message,
                 "failure_signature": rt.failure_signature},
        "run": {"number": run.number if run else None,
                "environment": run.environment if run else None,
                "git_sha": run.git_sha if run else None} if run else {},
        "steps": [{"index": s.index, "action": s.action, "intent": s.intent,
                   "status": s.status, "error": s.error_message} for s in steps],
        "defects": [{"tracker": d.tracker, "key": d.key, "url": d.url} for d in defects],
        "console_errors": rt.console_errors or [],
        "network_failures": rt.network_failures or [],
    }

    buf = io.BytesIO()
    included = 0
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("metadata.json", json.dumps(metadata, indent=2, default=str))
        for art in artifacts:
            try:
                data, _ctype, filename = open_artifact(art)
            except FileNotFoundError:
                continue
            z.writestr(f"{art.kind}/{filename}", data)
            included += 1
    buf.seek(0)
    name = f"evidence-{rt.test_key or rt.id}.zip".replace("/", "_")
    return buf.getvalue(), name
