"""Git provider integrations.

GaleQEA proposes script changes as *pull requests*, never as direct commits to a
default branch. That is not a limitation - it is the same principle as the
approval gate applied to code: the diff goes where the team already reviews
diffs, in their own tooling, with their own required checks.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from sqlalchemy.orm import Session

from .base import Connection, IntegrationError, http_client, load_connection, safe_error


@dataclass(slots=True)
class ProposedChange:
    path: str
    content: str
    message: str


def open_pull_request(
    db: Session,
    *,
    project_id: str,
    provider: str,
    branch: str,
    title: str,
    body: str,
    changes: list[ProposedChange],
    base_branch: str = "",
) -> dict:
    connection = load_connection(db, project_id=project_id, provider=provider)
    handler = {"github": _github_pr, "gitlab": _gitlab_mr, "bitbucket": _bitbucket_pr}.get(provider)
    if handler is None:
        raise IntegrationError(f"unsupported git provider {provider!r}")
    if not changes:
        raise IntegrationError("no file changes were supplied")
    return handler(connection, branch, title, body, changes, base_branch)


# --------------------------------------------------------------------------- #
def _github_pr(connection: Connection, branch, title, body, changes, base_branch):
    repo = connection.require("repo")
    token = connection.secret("token")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}
    api = f"https://api.github.com/repos/{repo}"

    with http_client() as client:
        repo_info = client.get(api, headers=headers)
        if repo_info.status_code >= 400:
            raise safe_error(repo_info, provider="GitHub")
        base = base_branch or repo_info.json().get("default_branch", "main")

        ref = client.get(f"{api}/git/ref/heads/{base}", headers=headers)
        if ref.status_code >= 400:
            raise safe_error(ref, provider="GitHub")
        base_sha = ref.json()["object"]["sha"]

        created = client.post(
            f"{api}/git/refs", headers=headers,
            json={"ref": f"refs/heads/{branch}", "sha": base_sha},
        )
        if created.status_code >= 400 and created.status_code != 422:  # 422 = already exists
            raise safe_error(created, provider="GitHub")

        for change in changes:
            existing = client.get(
                f"{api}/contents/{change.path}", headers=headers, params={"ref": branch}
            )
            payload = {
                "message": change.message,
                "content": base64.b64encode(change.content.encode()).decode(),
                "branch": branch,
            }
            if existing.status_code == 200:
                payload["sha"] = existing.json()["sha"]
            written = client.put(f"{api}/contents/{change.path}", headers=headers, json=payload)
            if written.status_code >= 400:
                raise safe_error(written, provider="GitHub")

        pull = client.post(
            f"{api}/pulls", headers=headers,
            json={"title": title, "body": body, "head": branch, "base": base},
        )
        if pull.status_code >= 400:
            raise safe_error(pull, provider="GitHub")
        result = pull.json()

    return {"ok": True, "provider": "github", "number": result.get("number"),
            "url": result.get("html_url"), "branch": branch, "files": len(changes)}


def _gitlab_mr(connection: Connection, branch, title, body, changes, base_branch):
    base_url = connection.config.get("base_url", "https://gitlab.com").rstrip("/")
    project = connection.require("project_id")
    headers = {"PRIVATE-TOKEN": connection.secret("token")}
    api = f"{base_url}/api/v4/projects/{project}"

    with http_client() as client:
        info = client.get(api, headers=headers)
        if info.status_code >= 400:
            raise safe_error(info, provider="GitLab")
        base = base_branch or info.json().get("default_branch", "main")

        client.post(f"{api}/repository/branches", headers=headers,
                    params={"branch": branch, "ref": base})

        actions = []
        for change in changes:
            existing = client.get(
                f"{api}/repository/files/{change.path.replace('/', '%2F')}",
                headers=headers, params={"ref": branch},
            )
            actions.append({
                "action": "update" if existing.status_code == 200 else "create",
                "file_path": change.path,
                "content": change.content,
            })
        commit = client.post(
            f"{api}/repository/commits", headers=headers,
            json={"branch": branch, "commit_message": changes[0].message, "actions": actions},
        )
        if commit.status_code >= 400:
            raise safe_error(commit, provider="GitLab")

        merge = client.post(
            f"{api}/merge_requests", headers=headers,
            json={"source_branch": branch, "target_branch": base,
                  "title": title, "description": body},
        )
        if merge.status_code >= 400:
            raise safe_error(merge, provider="GitLab")
        result = merge.json()

    return {"ok": True, "provider": "gitlab", "number": result.get("iid"),
            "url": result.get("web_url"), "branch": branch, "files": len(changes)}


def _bitbucket_pr(connection: Connection, branch, title, body, changes, base_branch):
    workspace = connection.require("workspace")
    repo = connection.require("repo")
    username = connection.require("username")
    auth = (username, connection.secret("app_password"))
    api = f"https://api.bitbucket.org/2.0/repositories/{workspace}/{repo}"

    with http_client() as client:
        info = client.get(api, auth=auth)
        if info.status_code >= 400:
            raise safe_error(info, provider="Bitbucket")
        base = base_branch or info.json().get("mainbranch", {}).get("name", "main")

        files = {change.path: (None, change.content) for change in changes}
        commit = client.post(
            f"{api}/src", auth=auth, files=files,
            data={"branch": branch, "message": changes[0].message},
        )
        if commit.status_code >= 400:
            raise safe_error(commit, provider="Bitbucket")

        pull = client.post(
            f"{api}/pullrequests", auth=auth,
            json={"title": title, "description": body,
                  "source": {"branch": {"name": branch}},
                  "destination": {"branch": {"name": base}}},
        )
        if pull.status_code >= 400:
            raise safe_error(pull, provider="Bitbucket")
        result = pull.json()

    return {"ok": True, "provider": "bitbucket", "number": result.get("id"),
            "url": (result.get("links", {}).get("html", {}) or {}).get("href"),
            "branch": branch, "files": len(changes)}


def changed_paths_for_commit(db: Session, *, project_id: str, provider: str, sha: str) -> list[str]:
    """Feed predictive test selection from a real commit."""
    connection = load_connection(db, project_id=project_id, provider=provider)
    if provider != "github":
        raise IntegrationError(f"commit inspection is not implemented for {provider!r} yet")
    repo = connection.require("repo")
    headers = {"Authorization": f"Bearer {connection.secret('token')}",
               "Accept": "application/vnd.github+json"}
    with http_client() as client:
        response = client.get(f"https://api.github.com/repos/{repo}/commits/{sha}", headers=headers)
    if response.status_code >= 400:
        raise safe_error(response, provider="GitHub")
    return [f["filename"] for f in response.json().get("files", [])]
