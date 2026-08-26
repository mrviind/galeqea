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


def push_results(db: Session, *, project_id: str, run_id: str, test_plan_key: str = "") -> dict:
    from sqlalchemy import select

    from ..models import Run, RunStatus, RunTest

    connection = load_connection(db, project_id=project_id, provider="xray")
    token = authenticate(db, connection)

    run = db.get(Run, run_id)
    if run is None:
        raise IntegrationError(f"unknown run {run_id}")
    results = list(db.execute(select(RunTest).where(RunTest.run_id == run_id)).scalars())
    if not results:
        raise IntegrationError("that run has no results to push")

    # Resolve every Xray key in one query rather than per result.
    from ..models import TestCase

    case_ids = {r.test_case_id for r in results if r.test_case_id}
    cases = {
        c.id: c
        for c in db.execute(select(TestCase).where(TestCase.id.in_(case_ids))).scalars()
    }

    status_map = {
        RunStatus.PASSED: "PASSED",
        RunStatus.FAILED: "FAILED",
        RunStatus.ERROR: "FAILED",
        RunStatus.SKIPPED: "TODO",
        RunStatus.NEEDS_REVIEW: "TODO",
        RunStatus.BLOCKED: "TODO",
    }
    payload = {
        "info": {
            "summary": f"GaleQEA run #{run.number} — {run.title}",
            "description": (run.triage or {}).get("headline", ""),
            "startDate": run.started_at.isoformat() if run.started_at else utcnow().isoformat(),
            "finishDate": run.finished_at.isoformat() if run.finished_at else utcnow().isoformat(),
            **({"testPlanKey": test_plan_key} if test_plan_key else {}),
        },
        "tests": [
            {
                "testKey": _xray_key(cases.get(result.test_case_id)),
                "status": status_map.get(result.status, "FAILED"),
                "comment": (result.error_message or "")[:2000] or result.title,
            }
            for result in results
            if _xray_key(cases.get(result.test_case_id))
        ],
    }
    if not payload["tests"]:
        raise IntegrationError(
            "none of the results carry an Xray test key. Tag the tests with their Xray "
            "key (e.g. 'xray:PROJ-123') so results can be matched."
        )

    with http_client() as client:
        response = client.post(
            f"{BASE_URL}/import/execution",
            json=payload,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
    if response.status_code >= 400:
        raise safe_error(response, provider="Xray")

    body = response.json()
    return {
        "ok": True,
        "test_execution_key": body.get("key"),
        "pushed": len(payload["tests"]),
        "skipped": len(results) - len(payload["tests"]),
        "url": body.get("self", ""),
    }


def _xray_key(case) -> str:
    """Xray keys travel on the test case as an 'xray:KEY' tag."""
    if case is None:
        return ""
    for tag in case.tags or []:
        if tag.lower().startswith("xray:"):
            return tag.split(":", 1)[1].upper()
    return ""
