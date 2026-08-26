"""Jira Cloud integration (REST v3)."""

from __future__ import annotations

from sqlalchemy.orm import Session

from .base import http_client, load_connection, safe_error


def _auth_headers(connection) -> dict:
    import base64

    email = connection.require("email")
    token = connection.secret("api_token")
    credential = base64.b64encode(f"{email}:{token}".encode()).decode()
    return {
        "Authorization": f"Basic {credential}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def verify(db: Session, *, project_id: str) -> dict:
    connection = load_connection(db, project_id=project_id, provider="jira")
    base = connection.require("base_url").rstrip("/")
    with http_client() as client:
        response = client.get(f"{base}/rest/api/3/myself", headers=_auth_headers(connection))
    if response.status_code >= 400:
        raise safe_error(response, provider="Jira")
    body = response.json()
    return {"ok": True, "account": body.get("displayName"), "email": body.get("emailAddress")}


def create_issue(
    db: Session,
    *,
    project_id: str,
    summary: str,
    description: str,
    issue_type: str = "Bug",
    priority: str = "",
    labels: list[str] | None = None,
    run_test_id: str = "",
    rca_id: str = "",
    **_ignored,
) -> dict:
    connection = load_connection(db, project_id=project_id, provider="jira")
    base = connection.require("base_url").rstrip("/")
    jira_project = connection.require("project_key")

    fields: dict = {
        "project": {"key": jira_project},
        "summary": summary[:250],
        "issuetype": {"name": issue_type},
        "description": _adf(description),
        # Marks provenance in Jira itself, so nobody later wonders whether a
        # human or an agent filed it.
        "labels": [*(labels or []), "galeqea", "ai-assisted"],
    }
    if priority:
        fields["priority"] = {"name": priority}

    with http_client() as client:
        response = client.post(
            f"{base}/rest/api/3/issue", json={"fields": fields}, headers=_auth_headers(connection)
        )
    if response.status_code >= 400:
        raise safe_error(response, provider="Jira")

    body = response.json()
    key = body.get("key", "")
    return {
        "ok": True,
        "key": key,
        "id": body.get("id"),
        "url": f"{base}/browse/{key}" if key else "",
        "run_test_id": run_test_id,
        "rca_id": rca_id,
    }


def _adf(text: str) -> dict:
    """Jira v3 requires Atlassian Document Format, not markdown."""
    paragraphs = [p for p in (text or "").split("\n\n") if p.strip()] or [""]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": p[:30000]}]}
            for p in paragraphs
        ],
    }


def search(db: Session, *, project_id: str, jql: str, limit: int = 20) -> dict:
    connection = load_connection(db, project_id=project_id, provider="jira")
    base = connection.require("base_url").rstrip("/")
    with http_client() as client:
        response = client.post(
            f"{base}/rest/api/3/search",
            json={"jql": jql, "maxResults": min(limit, 100),
                  "fields": ["summary", "status", "priority", "labels"]},
            headers=_auth_headers(connection),
        )
    if response.status_code >= 400:
        raise safe_error(response, provider="Jira")
    body = response.json()
    return {
        "total": body.get("total", 0),
        "issues": [
            {"key": i["key"], "summary": i["fields"]["summary"],
             "status": (i["fields"].get("status") or {}).get("name"),
             "url": f"{base}/browse/{i['key']}"}
            for i in body.get("issues", [])
        ],
    }
