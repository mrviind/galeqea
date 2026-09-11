"""Confluence Cloud (v2 REST): publish a release-report page.

Same Atlassian account as Jira (email + API-token basic auth), but a different API
surface (``/wiki/api/v2``). Publishing is find-or-create then update-in-place: the
first publish creates the page under a fixed parent, later ones bump ``version.number``
(a 409 means someone edited it meanwhile, so re-read the version and retry once).
"""

from __future__ import annotations

import base64

from sqlalchemy.orm import Session

from .base import Connection, IntegrationError, http_client, load_connection, safe_error


def _auth(connection: Connection) -> dict:
    cred = base64.b64encode(
        f"{connection.require('email')}:{connection.secret('api_token')}".encode()).decode()
    return {"Authorization": f"Basic {cred}", "Content-Type": "application/json",
            "Accept": "application/json"}


def _base(connection: Connection) -> str:
    # base_url is the site root; Confluence lives under /wiki.
    root = connection.require("base_url").rstrip("/")
    return root if root.endswith("/wiki") else root + "/wiki"


def verify(db: Session, *, project_id: str) -> dict:
    connection = load_connection(db, project_id=project_id, provider="confluence")
    base = _base(connection)
    space_key = connection.config.get("space_key", "")
    with http_client() as client:
        r = client.get(f"{base}/api/v2/spaces" + (f"?keys={space_key}" if space_key else ""),
                       headers=_auth(connection))
    if r.status_code >= 400:
        raise safe_error(r, provider="Confluence")
    spaces = [{"key": s.get("key"), "id": s.get("id"), "name": s.get("name")}
              for s in (r.json().get("results") or [])]
    return {"ok": True, "provider": "confluence", "spaces": spaces}


def _space_id(client, base, headers, space_key) -> str:
    r = client.get(f"{base}/api/v2/spaces?keys={space_key}", headers=headers)
    if r.status_code >= 400:
        raise safe_error(r, provider="Confluence")
    results = r.json().get("results") or []
    if not results:
        raise IntegrationError(f"no Confluence space with key {space_key!r}")
    return str(results[0]["id"])


def _find_page(client, base, headers, space_id, title):
    r = client.get(f"{base}/api/v2/pages",
                   params={"space-id": space_id, "title": title, "body-format": "storage"},
                   headers=headers)
    if r.status_code >= 400:
        raise safe_error(r, provider="Confluence")
    results = r.json().get("results") or []
    if not results:
        return None
    p = results[0]
    return {"id": str(p["id"]), "version": (p.get("version") or {}).get("number", 1)}


def publish_page(db: Session, *, project_id: str, title: str, storage_html: str,
                 space_key: str = "", parent_id: str = "",
                 known_page_id: str = "", known_version: int = 0) -> dict:
    """Create the page or update it in place. Returns {page_id, version, url}."""
    connection = load_connection(db, project_id=project_id, provider="confluence")
    base = _base(connection)
    headers = _auth(connection)
    space_key = space_key or connection.config.get("space_key", "")
    parent_id = parent_id or connection.config.get("parent_id", "")
    if not space_key:
        raise IntegrationError("a Confluence space key is required")

    with http_client() as client:
        space_id = _space_id(client, base, headers, space_key)
        found = ({"id": known_page_id, "version": known_version} if known_page_id
                 else _find_page(client, base, headers, space_id, title))

        if found is None:
            body = {"spaceId": space_id, "status": "current", "title": title,
                    "body": {"representation": "storage", "value": storage_html}}
            if parent_id:
                body["parentId"] = parent_id
            r = client.post(f"{base}/api/v2/pages", json=body, headers=headers)
            if r.status_code >= 400:
                raise safe_error(r, provider="Confluence")
            page = r.json()
            page_id, version = str(page["id"]), (page.get("version") or {}).get("number", 1)
        else:
            page_id, version = found["id"], found["version"]
            page = _update_page(client, base, headers, page_id, title, storage_html, version)
            version = (page.get("version") or {}).get("number", version + 1)

        _label(client, base, headers, page_id, "test-report")

    webui = ((page.get("_links") or {}).get("webui")) or f"/wiki/pages/{page_id}"
    root = connection.require("base_url").rstrip("/").removesuffix("/wiki")
    return {"ok": True, "page_id": page_id, "version": version, "url": root + webui}


def _update_page(client, base, headers, page_id, title, storage_html, version) -> dict:
    def _put(next_version):
        return client.put(f"{base}/api/v2/pages/{page_id}", headers=headers, json={
            "id": page_id, "status": "current", "title": title,
            "body": {"representation": "storage", "value": storage_html},
            "version": {"number": next_version}})

    r = _put(version + 1)
    if r.status_code == 409:
        # Someone edited it, so re-read the current version and retry once.
        cur = client.get(f"{base}/api/v2/pages/{page_id}", headers=headers)
        if cur.status_code < 400:
            latest = (cur.json().get("version") or {}).get("number", version)
            r = _put(latest + 1)
    if r.status_code >= 400:
        raise safe_error(r, provider="Confluence")
    return r.json()


def _label(client, base, headers, page_id, label) -> None:
    # Labels are still a v1 endpoint.
    v1 = base + "/rest/api/content"
    try:
        client.post(f"{v1}/{page_id}/label", headers=headers,
                    json=[{"prefix": "global", "name": label}])
    except Exception:  # noqa: BLE001 - a label failure must not fail the publish
        pass
