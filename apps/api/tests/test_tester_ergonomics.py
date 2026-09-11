"""WO#6 6-G. Tester ergonomics: evidence-bundle zip, manual runner (next-untested +
attachment paste), and tester-authored SBTM exploratory sessions.
"""

from __future__ import annotations

import io
import zipfile

from sqlalchemy import select

from galeqea.models import Artifact, ExplorationSession, Run, RunStepRecord, RunTest
from galeqea.services import evidence_bundle, manual_session


def _run(db, project, status="failed"):
    run = Run(project_id=project.id, number=1, title="Smoke", status=status,
              environment="staging", git_sha="abc123")
    db.add(run)
    db.flush()
    return run


# --------------------------------------------------------------------------- #
# Evidence bundle
# --------------------------------------------------------------------------- #
def test_evidence_zip_has_metadata_and_artifacts(db, project, tmp_path):
    run = _run(db, project)
    rt = RunTest(run_id=run.id, test_case_id="", test_key="APP-T-1", title="pay", status="failed",
                 error_type="TimeoutError", error_message="boom", browser="chromium")
    db.add(rt)
    db.flush()
    db.add(RunStepRecord(run_test_id=rt.id, index=0, action="click", intent="click Pay",
                         status="failed"))
    shot = tmp_path / "fail.png"
    shot.write_bytes(b"\x89PNG\r\n")
    db.add(Artifact(run_id=run.id, run_test_id=rt.id, kind="screenshot", path=str(shot),
                    meta={"backend": "local"}))
    db.commit()

    data, name = evidence_bundle.build_zip(db, rt.id)
    assert name.endswith(".zip")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        assert "metadata.json" in names
        assert any(n.startswith("screenshot/") for n in names)
        import json
        meta = json.loads(z.read("metadata.json"))
        assert meta["test"]["key"] == "APP-T-1" and meta["steps"]


def test_http_evidence_zip(db, project, tmp_path):
    from fastapi.testclient import TestClient

    from galeqea.main import app
    run = _run(db, project)
    rt = RunTest(run_id=run.id, test_case_id="", test_key="APP-T-2", title="x", status="failed")
    db.add(rt)
    db.commit()
    client = TestClient(app)
    r = client.get(f"/api/projects/{project.id}/runs/{run.id}/results/{rt.id}/evidence.zip")
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"


# --------------------------------------------------------------------------- #
# Manual runner
# --------------------------------------------------------------------------- #
def test_next_untested(db, project):
    from fastapi.testclient import TestClient

    from galeqea.main import app
    run = _run(db, project, status="running")
    db.add_all([
        RunTest(run_id=run.id, test_case_id="", test_key="A", status="passed"),
        RunTest(run_id=run.id, test_case_id="", test_key="B", status="needs_review"),
    ])
    db.commit()
    client = TestClient(app)
    r = client.get(f"/api/projects/{project.id}/runs/{run.id}/next-untested").json()
    assert r["done"] is False and r["next"]["key"] == "B"


def test_attachment_paste_creates_artifact(db, project):
    from fastapi.testclient import TestClient

    from galeqea.main import app
    run = _run(db, project)
    rt = RunTest(run_id=run.id, test_case_id="", test_key="A", status="needs_review")
    db.add(rt)
    db.commit()
    client = TestClient(app)
    r = client.post(f"/api/projects/{project.id}/runs/{run.id}/results/{rt.id}/attachment",
                    files={"file": ("pasted.png", b"\x89PNG\r\n", "image/png")})
    assert r.status_code == 201 and r.json()["artifact_id"]
    assert db.execute(select(Artifact).where(Artifact.run_test_id == rt.id)).scalars().first()


# --------------------------------------------------------------------------- #
# SBTM sessions
# --------------------------------------------------------------------------- #
def test_manual_session_lifecycle(db, project, humans):
    s = manual_session.start(db, project, charter="Explore checkout", minutes=30,
                             actor=humans["author"])
    db.commit()
    assert s.strategy == "manual" and s.status == "running"

    manual_session.add_entry(db, project, kind="note", text="cart total looks off")
    manual_session.add_entry(db, project, kind="bug", text="coupon field rejects valid code")
    db.commit()

    rep = manual_session.end(db, project, actor=humans["author"])
    db.commit()
    assert rep["notes"] == 1 and rep["bugs"] == 1
    assert db.get(ExplorationSession, s.id).status == "finished"


def test_only_one_running_session(db, project, humans):
    manual_session.start(db, project, charter="A", actor=humans["author"])
    db.commit()
    import pytest
    with pytest.raises(manual_session.ManualSessionError):
        manual_session.start(db, project, charter="B", actor=humans["author"])


def test_chat_session_verbs(db, project, humans):
    from galeqea.services import manual_session_chat as mc
    text, blocks = mc.try_handle(db, project, "start exploratory session on checkout, 20 min",
                                 humans["author"])
    assert blocks and blocks[0]["type"] == "exploratory_session_card"
    assert blocks[0]["timebox_minutes"] == 20

    # note:/bug: land while running, and the reply acknowledges the running tally
    text, blocks = mc.try_handle(db, project, "bug: pay button disabled on Safari", humans["author"])
    assert "bug logged" in text.lower() and "1 entry" in text and "min left" in text
    assert blocks and blocks[0]["type"] == "exploratory_session_card"

    text, blocks = mc.try_handle(db, project, "end session", humans["author"])
    assert blocks[0]["bugs"] == 1


def test_note_prefix_ignored_without_active_session(db, project, humans):
    from galeqea.services import manual_session_chat as mc
    # no session running → "note:" is not intercepted (returns None so normal chat handles it)
    assert mc.try_handle(db, project, "note: this should pass through", humans["author"]) is None
