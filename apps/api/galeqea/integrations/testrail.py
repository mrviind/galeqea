"""TestRail: push results into a run.

TestRail matches by its own numeric case id, carried on a GaleQEA test as a
``testrail:123`` tag. A push creates a run (all matched cases) then posts a result
per case. Basic auth with email + API key; ~180 requests/minute.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Run, RunStatus, RunTest, TestCase
from .base import IntegrationError, http_client, load_connection, safe_error

#: TestRail status ids.
_STATUS = {RunStatus.PASSED: 1, RunStatus.BLOCKED: 2, RunStatus.NEEDS_REVIEW: 4,
           RunStatus.FAILED: 5, RunStatus.ERROR: 5, RunStatus.FLAKY: 1}


def _auth(connection):
    return (connection.require("username"), connection.secret("api_key"))


def _api(connection) -> str:
    return connection.require("base_url").rstrip("/") + "/index.php?/api/v2"


def verify(db: Session, *, project_id: str) -> dict:
    connection = load_connection(db, project_id=project_id, provider="testrail")
    with http_client() as client:
        r = client.get(f"{_api(connection)}/get_user_by_email&email={connection.require('username')}",
                       auth=_auth(connection))
    if r.status_code >= 400:
        raise safe_error(r, provider="TestRail")
    return {"ok": True, "provider": "testrail", "user": (r.json() or {}).get("name")}


def _testrail_case_id(case) -> str:
    for tag in (case.tags if case else []) or []:
        if tag.lower().startswith("testrail:"):
            return tag.split(":", 1)[1]
    return ""


def push_results(db: Session, *, project_id: str, run_id: str, run_name: str = "") -> dict:
    connection = load_connection(db, project_id=project_id, provider="testrail")
    tr_project = connection.config.get("project_id")
    if not tr_project:
        raise IntegrationError("set the TestRail project_id in the connection to push results")

    run = db.get(Run, run_id)
    if run is None:
        raise IntegrationError(f"unknown run {run_id}")
    results = list(db.execute(select(RunTest).where(RunTest.run_id == run_id)).scalars())
    case_ids = {r.test_case_id for r in results if r.test_case_id}
    cases = {c.id: c for c in db.execute(
        select(TestCase).where(TestCase.id.in_(case_ids))).scalars()}

    mapped = [(r, _testrail_case_id(cases.get(r.test_case_id))) for r in results]
    mapped = [(r, cid) for (r, cid) in mapped if cid]
    if not mapped:
        raise IntegrationError("no results carry a 'testrail:<case id>' tag to match on")

    api = _api(connection)
    with http_client() as client:
        created = client.post(
            f"{api}/add_run/{tr_project}", auth=_auth(connection),
            json={"name": run_name or f"GaleQEA run #{run.number}: {run.title}",
                  "include_all": False, "case_ids": [int(cid) for _r, cid in mapped]})
        if created.status_code >= 400:
            raise safe_error(created, provider="TestRail")
        tr_run_id = created.json()["id"]

        payload = {"results": [
            {"case_id": int(cid), "status_id": _STATUS.get(r.status, 5),
             "comment": (r.error_message or r.title or "")[:2000],
             "elapsed": f"{max(round((r.duration_ms or 0) / 1000), 1)}s"}
            for r, cid in mapped]}
        res = client.post(f"{api}/add_results_for_cases/{tr_run_id}", auth=_auth(connection),
                          json=payload)
        if res.status_code >= 400:
            raise safe_error(res, provider="TestRail")

    return {"ok": True, "provider": "testrail", "exec_key": str(tr_run_id),
            "pushed": len(mapped),
            "url": f"{connection.require('base_url').rstrip('/')}/index.php?/runs/view/{tr_run_id}"}
