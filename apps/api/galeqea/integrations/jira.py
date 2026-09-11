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
        me = client.get(f"{base}/rest/api/3/myself", headers=_auth_headers(connection))
        if me.status_code >= 400:
            raise safe_error(me, provider="Jira")
        # Which projects this token can see, so the user can confirm they picked the
        # right one for imports vs. defects. Best-effort: a permission gap here should
        # not fail the whole verification.
        projects = []
        proj = client.get(f"{base}/rest/api/3/project/search?maxResults=50",
                          headers=_auth_headers(connection))
        if proj.status_code < 400:
            projects = [{"key": p.get("key"), "name": p.get("name")}
                        for p in (proj.json().get("values") or [])]
    body = me.json()
    return {"ok": True, "account": body.get("displayName"), "email": body.get("emailAddress"),
            "projects": projects}


def create_issue(
    db: Session,
    *,
    project_id: str,
    summary: str,
    description: str = "",
    adf: dict | None = None,
    issue_type: str = "Bug",
    priority: str = "",
    labels: list[str] | None = None,
    project_key: str = "",
    run_test_id: str = "",
    rca_id: str = "",
    **_ignored,
) -> dict:
    connection = load_connection(db, project_id=project_id, provider="jira")
    base = connection.require("base_url").rstrip("/")
    # Bugs may target a different Jira project than imports (e.g. defects → CLD,
    # stories → XSP): prefer an explicit key, then the connection's defect project.
    jira_project = (project_key or connection.config.get("defect_project_key")
                    or connection.require("project_key"))

    fields: dict = {
        "project": {"key": jira_project},
        "summary": summary[:250],
        "issuetype": {"name": issue_type},
        # A caller may pass a fully-built ADF document (rich defect body); otherwise
        # the plain description is wrapped into paragraphs.
        "description": adf or _adf(description),
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


def add_comment(db: Session, *, project_id: str, issue_key: str,
                text: str = "", adf: dict | None = None) -> dict:
    """Comment on an existing issue (used when a known failure recurs)."""
    connection = load_connection(db, project_id=project_id, provider="jira")
    base = connection.require("base_url").rstrip("/")
    with http_client() as client:
        response = client.post(
            f"{base}/rest/api/3/issue/{issue_key}/comment",
            json={"body": adf or _adf(text)}, headers=_auth_headers(connection),
        )
    if response.status_code >= 400:
        raise safe_error(response, provider="Jira")
    return {"ok": True, "id": response.json().get("id")}


def add_attachment(db: Session, *, project_id: str, issue_key: str, filename: str,
                   data: bytes, content_type: str = "application/octet-stream") -> dict:
    """Attach evidence bytes to an issue. Jira requires the CSRF-exempt header and
    a multipart part literally named ``file``."""
    connection = load_connection(db, project_id=project_id, provider="jira")
    base = connection.require("base_url").rstrip("/")
    headers = _auth_headers(connection)
    # multipart sets its own Content-Type boundary; the JSON one would break it.
    headers = {k: v for k, v in headers.items() if k.lower() != "content-type"}
    headers["X-Atlassian-Token"] = "no-check"
    with http_client() as client:
        response = client.post(
            f"{base}/rest/api/3/issue/{issue_key}/attachments",
            files={"file": (filename, data, content_type)}, headers=headers,
        )
    if response.status_code >= 400:
        raise safe_error(response, provider="Jira")
    return {"ok": True, "attached": filename}


#: Jira status categories that mean "no longer an open blocker".
_DONE_CATEGORIES = {"done"}


def get_issue_status(db: Session, *, project_id: str, issue_key: str) -> dict:
    """Current status name + whether it is a done/terminal state (for readiness)."""
    connection = load_connection(db, project_id=project_id, provider="jira")
    base = connection.require("base_url").rstrip("/")
    with http_client() as client:
        response = client.get(
            f"{base}/rest/api/3/issue/{issue_key}?fields=status",
            headers=_auth_headers(connection),
        )
    if response.status_code >= 400:
        raise safe_error(response, provider="Jira")
    status = ((response.json().get("fields") or {}).get("status") or {})
    category = ((status.get("statusCategory") or {}).get("key") or "").lower()
    return {"ok": True, "status": status.get("name", ""),
            "done": category in _DONE_CATEGORIES}


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


# --------------------------------------------------------------------------- #
# A small hand-written ADF builder (heading / paragraph / bulletList / codeBlock
# / link), enough to render a defect body richly without pulling a dependency.
# --------------------------------------------------------------------------- #
def adf_doc(*nodes: dict) -> dict:
    return {"type": "doc", "version": 1, "content": [n for n in nodes if n]}


def adf_heading(text: str, level: int = 3) -> dict:
    return {"type": "heading", "attrs": {"level": level},
            "content": [{"type": "text", "text": text[:250]}]}


def adf_text(text: str) -> dict:
    return {"type": "text", "text": (text or "")[:30000]}


def adf_link(text: str, href: str) -> dict:
    return {"type": "text", "text": text[:500],
            "marks": [{"type": "link", "attrs": {"href": href}}]}


def adf_paragraph(*inline: dict) -> dict:
    return {"type": "paragraph", "content": [n for n in inline if n] or [adf_text("")]}


def adf_bullet_list(items: list[list[dict]]) -> dict:
    """Each item is a list of inline nodes."""
    return {
        "type": "bulletList",
        "content": [
            {"type": "listItem",
             "content": [{"type": "paragraph", "content": inline or [adf_text("")]}]}
            for inline in items
        ] or [{"type": "listItem", "content": [adf_paragraph(adf_text("None"))]}],
    }


def adf_code_block(text: str, language: str = "") -> dict:
    node = {"type": "codeBlock", "content": [{"type": "text", "text": (text or "")[:30000]}]}
    if language:
        node["attrs"] = {"language": language}
    return node


def search(db: Session, *, project_id: str, jql: str, limit: int = 20) -> dict:
    """A bounded search for the UI. Uses the current ``/search/jql`` endpoint (the
    legacy ``/search`` was removed) and returns a compact issue list."""
    connection = load_connection(db, project_id=project_id, provider="jira")
    base = connection.require("base_url").rstrip("/")
    issues = _search_jql(connection, base, jql,
                         fields=["summary", "status", "priority", "labels"],
                         cap=limit)
    return {
        "total": len(issues),
        "issues": [
            {"key": i["key"], "summary": (i["fields"].get("summary") or ""),
             "status": ((i["fields"].get("status") or {}).get("name")),
             "url": f"{base}/browse/{i['key']}"}
            for i in issues
        ],
    }


def _search_jql(connection, base: str, jql: str, *, fields: list[str], cap: int = 500,
                page_size: int = 100) -> list[dict]:
    """Page through ``POST /rest/api/3/search/jql`` following ``nextPageToken`` (the
    response has no ``total``), collecting up to ``cap`` issues."""
    collected: list[dict] = []
    token = None
    with http_client() as client:
        while len(collected) < cap:
            payload = {"jql": jql, "maxResults": min(page_size, cap - len(collected)),
                       "fields": fields}
            if token:
                payload["nextPageToken"] = token
            response = client.post(f"{base}/rest/api/3/search/jql", json=payload,
                                   headers=_auth_headers(connection))
            if response.status_code >= 400:
                raise safe_error(response, provider="Jira")
            body = response.json()
            collected.extend(body.get("issues", []))
            token = body.get("nextPageToken")
            if not token or not body.get("issues"):
                break
    return collected[:cap]


def search_issues(db: Session, *, project_id: str, jql: str, fields: list[str] | None = None,
                  cap: int = 500) -> list[dict]:
    """Full issue payloads for an import: description included, all pages followed."""
    connection = load_connection(db, project_id=project_id, provider="jira")
    base = connection.require("base_url").rstrip("/")
    return _search_jql(connection, base, jql,
                       fields=fields or ["summary", "description", "labels", "issuetype",
                                         "priority", "updated"],
                       cap=cap)


def link_issues(db: Session, *, project_id: str, inward_key: str, outward_key: str,
                link_type: str = "Test") -> dict:
    """Create a Jira issue link (default type "Test", the Xray coverage link)."""
    connection = load_connection(db, project_id=project_id, provider="jira")
    base = connection.require("base_url").rstrip("/")
    with http_client() as client:
        response = client.post(
            f"{base}/rest/api/3/issueLink",
            json={"type": {"name": link_type},
                  "inwardIssue": {"key": inward_key}, "outwardIssue": {"key": outward_key}},
            headers=_auth_headers(connection))
    if response.status_code >= 400:
        raise safe_error(response, provider="Jira")
    return {"ok": True, "linked": f"{inward_key}↔{outward_key}", "type": link_type}


# --------------------------------------------------------------------------- #
# ADF → Markdown (enough of the node set to render a story description readably)
# --------------------------------------------------------------------------- #
def adf_to_markdown(node: dict | None) -> str:
    if not node:
        return ""
    return _adf_nodes(node.get("content", [])).strip()


def _adf_nodes(nodes: list[dict]) -> str:
    return "".join(_adf_node(n) for n in (nodes or []))


def _adf_inline(nodes: list[dict]) -> str:
    out = []
    for n in nodes or []:
        t = n.get("type")
        if t == "text":
            text = n.get("text", "")
            marks = {m.get("type") for m in n.get("marks", [])}
            if "code" in marks:
                text = f"`{text}`"
            if "strong" in marks:
                text = f"**{text}**"
            if "em" in marks:
                text = f"*{text}*"
            link = next((m for m in n.get("marks", []) if m.get("type") == "link"), None)
            if link:
                text = f"[{text}]({link.get('attrs', {}).get('href', '')})"
            out.append(text)
        elif t == "hardBreak":
            out.append("\n")
        elif t in ("inlineCard", "mention"):
            attrs = n.get("attrs", {})
            out.append(attrs.get("url") or attrs.get("text") or "")
    return "".join(out)


def _adf_node(node: dict) -> str:
    t = node.get("type")
    if t == "paragraph":
        return _adf_inline(node.get("content", [])) + "\n\n"
    if t == "heading":
        level = (node.get("attrs", {}) or {}).get("level", 2)
        return f"{'#' * int(level)} {_adf_inline(node.get('content', []))}\n\n"
    if t == "bulletList":
        return "".join(f"- {_adf_inline(_first_para(li))}\n"
                       for li in node.get("content", [])) + "\n"
    if t == "orderedList":
        return "".join(f"{i}. {_adf_inline(_first_para(li))}\n"
                       for i, li in enumerate(node.get("content", []), 1)) + "\n"
    if t == "codeBlock":
        return f"```\n{_adf_inline(node.get('content', []))}\n```\n\n"
    if t == "blockquote":
        return f"> {_adf_nodes(node.get('content', [])).strip()}\n\n"
    if t == "rule":
        return "---\n\n"
    if t in ("text", "hardBreak"):
        return _adf_inline([node])
    return _adf_nodes(node.get("content", []))


def _first_para(list_item: dict) -> list[dict]:
    for child in list_item.get("content", []):
        if child.get("type") == "paragraph":
            return child.get("content", [])
    return []
