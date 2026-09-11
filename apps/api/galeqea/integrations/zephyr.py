"""Zephyr Scale Cloud: push automation results as a JUnit execution.

Zephyr keeps tests in its own model (linked to Jira), not as Jira issues, so the
simplest reliable push is the JUnit automation endpoint: it matches results to
Zephyr test cases by the ``PROJ-T123`` key in each test name and can auto-create
the ones it hasn't seen. Bearer-token auth; the token does not expire on a schedule
the way Xray's does, so there's nothing to cache.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from .base import http_client, load_connection, safe_error

DEFAULT_BASE = "https://api.zephyrscale.smartbear.com/v2"


def verify(db: Session, *, project_id: str) -> dict:
    connection = load_connection(db, project_id=project_id, provider="zephyr_scale")
    base = (connection.config.get("base_url") or DEFAULT_BASE).rstrip("/")
    token = connection.secret("api_token")
    with http_client() as client:
        r = client.get(f"{base}/healthcheck",
                       headers={"Authorization": f"Bearer {token}"})
    if r.status_code >= 400:
        raise safe_error(r, provider="Zephyr Scale")
    return {"ok": True, "provider": "zephyr_scale"}


def push_junit(db: Session, *, project_id: str, junit_xml: str, cycle_name: str = "",
               auto_create: bool = True) -> dict:
    """POST the run's JUnit XML to Zephyr's automation endpoint."""
    connection = load_connection(db, project_id=project_id, provider="zephyr_scale")
    base = (connection.config.get("base_url") or DEFAULT_BASE).rstrip("/")
    project_key = connection.require("project_key")
    token = connection.secret("api_token")

    import json
    params = {"projectKey": project_key, "autoCreateTestCases": str(auto_create).lower()}
    files = {"file": ("results.xml", junit_xml.encode(), "application/xml")}
    if cycle_name:
        files["testCycle"] = ("cycle.json", json.dumps({"name": cycle_name}).encode(),
                              "application/json")
    with http_client() as client:
        r = client.post(f"{base}/automations/executions/junit", params=params,
                        files=files, headers={"Authorization": f"Bearer {token}"})
    if r.status_code >= 400:
        raise safe_error(r, provider="Zephyr Scale")
    body = r.json() if r.content else {}
    return {"ok": True, "provider": "zephyr_scale",
            "exec_key": body.get("testCycle", {}).get("key", "") if isinstance(body, dict) else "",
            "url": (body.get("testCycle", {}) or {}).get("self", "") if isinstance(body, dict) else ""}
