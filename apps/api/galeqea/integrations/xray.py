"""Xray Cloud integration.

Xray's auth model has one detail that trips up every integration: the API key
pair (client id + client secret) does **not** expire, but the bearer token it
returns from ``POST /api/v2/authenticate`` expires after 24 hours. Re-
authenticating on every call is wasteful and rate-limit-prone; never
re-authenticating produces mysterious 401s a day after everything worked. So the
token is cached with its expiry and refreshed a little early.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from ..models.base import utcnow
from .base import Connection, IntegrationError, http_client, load_connection, safe_error

AUTH_URL = "https://xray.cloud.getxray.app/api/v2/authenticate"
BASE_URL = "https://xray.cloud.getxray.app/api/v2"

#: Refresh this far before the documented 24h expiry so a long run cannot
#: straddle the boundary and fail halfway through.
REFRESH_MARGIN = timedelta(minutes=45)
TOKEN_LIFETIME = timedelta(hours=24)


def authenticate(db: Session, connection: Connection) -> str:
    cache = connection.record.token_cache or {}
    expires_at = cache.get("expires_at")
    if cache.get("token") and expires_at:
        from datetime import datetime

        if datetime.fromisoformat(expires_at) - REFRESH_MARGIN > utcnow():
            return cache["token"]

    client_id = connection.secret("client_id")
    client_secret = connection.secret("client_secret")

    with http_client() as client:
        response = client.post(
            AUTH_URL,
            json={"client_id": client_id, "client_secret": client_secret},
            headers={"Content-Type": "application/json"},
        )
    if response.status_code != 200:
        raise safe_error(response, provider="Xray")

    # Xray returns the bare token as a JSON string, quotes included.
    token = response.text.strip().strip('"')
    if not token:
        raise IntegrationError("Xray returned an empty token")

    connection.record.token_cache = {
        "token": token,
        "expires_at": (utcnow() + TOKEN_LIFETIME).isoformat(),
        "obtained_at": utcnow().isoformat(),
    }
    connection.record.status = "connected"
    connection.record.last_checked_at = utcnow()
    db.flush()
    return token


def verify(db: Session, *, project_id: str) -> dict:
    connection = load_connection(db, project_id=project_id, provider="xray")
    token = authenticate(db, connection)
    return {
        "ok": True,
        "provider": "xray",
        "token_expires_at": (connection.record.token_cache or {}).get("expires_at"),
        "note": (
            "The API key pair does not expire; this bearer token does, after 24 hours. "
            "GaleQEA caches and refreshes it automatically."
        ),
        "token_prefix": token[:8] + "…",
    }


def push_results(db: Session, *, project_id: str, run_id: str, test_plan_key: str = "",
                 environments: str = "") -> dict:
    from sqlalchemy import select

    from ..models import DefectLink, Run, RunStatus, RunTest, TestCase

    connection = load_connection(db, project_id=project_id, provider="xray")

    run = db.get(Run, run_id)
    if run is None:
        raise IntegrationError(f"unknown run {run_id}")
    results = list(db.execute(select(RunTest).where(RunTest.run_id == run_id)).scalars())
    if not results:
        raise IntegrationError("that run has no results to push")

    case_ids = {r.test_case_id for r in results if r.test_case_id}
    cases = {c.id: c for c in db.execute(
        select(TestCase).where(TestCase.id.in_(case_ids))).scalars()}
    # Defects linked to any of these results, so the execution lists them.
    defects_by_result: dict[str, list[str]] = {}
    for link in db.execute(select(DefectLink).where(
            DefectLink.result_id.in_([r.id for r in results]))).scalars():
        defects_by_result.setdefault(link.result_id, []).append(link.key)

    status_map = {
        RunStatus.PASSED: "PASSED", RunStatus.FLAKY: "PASSED",
        RunStatus.FAILED: "FAILED", RunStatus.ERROR: "FAILED",
        RunStatus.SKIPPED: "TODO", RunStatus.NEEDS_REVIEW: "TODO", RunStatus.BLOCKED: "TODO",
    }
    xray_project = connection.config.get("project_key", "")
    tests = []
    for result in results:
        case = cases.get(result.test_case_id)
        entry: dict = {"status": status_map.get(result.status, "FAILED"),
                       "comment": (result.error_message or "")[:2000] or result.title}
        key = _xray_key(case)
        if key:
            entry["testKey"] = key
        else:
            # No Xray key → let Xray match/create by a STABLE definition (idempotent):
            # the GaleQEA test key. Same run pushed twice updates the same test.
            definition = (case.key if case else result.test_key) or result.title
            entry["testInfo"] = {"projectKey": xray_project, "type": "Generic",
                                 "summary": (case.title if case else result.title)[:250],
                                 "definition": definition}
            reqs = (case.requirement_refs if case else []) or []
            if reqs:
                entry["testInfo"]["requirementKeys"] = reqs
        if defects_by_result.get(result.id):
            entry["defects"] = defects_by_result[result.id]
        tests.append(entry)

    info = {
        "summary": f"GaleQEA run #{run.number}: {run.title}",
        "description": (run.triage or {}).get("headline", ""),
        "startDate": run.started_at.isoformat() if run.started_at else utcnow().isoformat(),
        "finishDate": run.finished_at.isoformat() if run.finished_at else utcnow().isoformat(),
    }
    if test_plan_key:
        info["testPlanKey"] = test_plan_key
    if environments:
        info["testEnvironments"] = [e.strip() for e in environments.split(";") if e.strip()]
    payload = {"info": info, "tests": tests}

    # Xray DC speaks a different base + Jira auth; Cloud uses the bearer token.
    if connection.config.get("deployment") == "dc":
        dc_base = connection.require("base_url").rstrip("/")
        headers = _dc_auth(connection)
        url = f"{dc_base}/rest/raven/2.0/import/execution"
    else:
        token = authenticate(db, connection)
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        url = f"{BASE_URL}/import/execution"

    with http_client() as client:
        response = client.post(url, json=payload, headers=headers)
    if response.status_code >= 400:
        raise safe_error(response, provider="Xray")

    body = response.json()
    key = body.get("key") or ((body.get("testExecIssue") or {}).get("key"))
    return {
        "ok": True,
        "test_execution_key": key,
        "exec_key": key,
        "pushed": len(tests),
        "skipped": 0,
        "url": body.get("self", ""),
    }


def _dc_auth(connection) -> dict:
    """Xray Data Center authenticates as Jira: a PAT bearer, or basic email:token."""
    import base64
    headers = {"Content-Type": "application/json"}
    pat = (connection.secrets.get("pat") if hasattr(connection, "secrets") else None)
    try:
        pat = connection.secret("pat")
    except IntegrationError:
        pat = None
    if pat:
        headers["Authorization"] = f"Bearer {pat}"
    else:
        cred = base64.b64encode(
            f"{connection.require('email')}:{connection.secret('api_token')}".encode()).decode()
        headers["Authorization"] = f"Basic {cred}"
    return headers


def _xray_key(case) -> str:
    """Xray keys travel on the test case as an 'xray:KEY' tag."""
    if case is None:
        return ""
    for tag in case.tags or []:
        if tag.lower().startswith("xray:"):
            return tag.split(":", 1)[1].upper()
    return ""
