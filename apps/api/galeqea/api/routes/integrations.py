from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from ...core import audit
from ...core.vault import hint_for, seal
from ...db import get_db
from ...integrations.base import IntegrationError
from ...models import IntegrationConnection, Project, Role, User, VaultSecret
from ...models.base import utcnow
from ..deps import current_user, get_project

router = APIRouter(prefix="/api/projects/{project_id}/integrations", tags=["integrations"])

#: Each provider declares its non-secret settings and its secret fields, so the
#: UI can render the right form and the API can refuse an incomplete connection.
PROVIDER_SPECS: dict[str, dict] = {
    "jira": {
        "label": "Jira Cloud",
        "config": ["base_url", "email", "project_key"],
        "secrets": ["api_token"],
        "help": "Create an API token at id.atlassian.com → Security → API tokens.",
    },
    "xray": {
        "label": "Xray Cloud",
        "config": ["project_key"],
        "secrets": ["client_id", "client_secret"],
        "help": (
            "Xray → Global Settings → API Keys. The key pair does not expire; the "
            "bearer token it issues lasts 24 hours and GaleQEA refreshes it for you."
        ),
    },
    "zephyr_scale": {
        "label": "Zephyr Scale",
        "config": ["project_key", "base_url"],
        "secrets": ["api_token"],
        "help": (
            "Zephyr → API keys. Tests live in Zephyr's own model linked to Jira, "
            "not as Jira issues, so steps are created in a second call."
        ),
    },
    "testrail": {
        "label": "TestRail",
        "config": ["base_url", "username", "section_id"],
        "secrets": ["api_key"],
        "help": "My Settings → API Keys. section_id is the suite section cases land in.",
    },
    "github": {"label": "GitHub", "config": ["repo"], "secrets": ["token"],
               "help": "A fine-grained PAT with Contents and Pull requests write access."},
    "gitlab": {"label": "GitLab", "config": ["base_url", "project_id"], "secrets": ["token"],
               "help": "A project access token with api scope."},
    "bitbucket": {"label": "Bitbucket", "config": ["workspace", "repo", "username"],
                  "secrets": ["app_password"], "help": "Create an app password with repo write."},
    "jenkins": {"label": "Jenkins", "config": ["base_url", "job", "username"],
                "secrets": ["api_token"], "help": "User → Configure → API Token."},
    "github_actions": {"label": "GitHub Actions", "config": ["repo"], "secrets": ["token"],
                       "help": "A PAT with actions:read."},
    "gitlab_ci": {"label": "GitLab CI", "config": ["base_url", "project_id"], "secrets": ["token"],
                  "help": "A token with read_api scope."},
    "azure_devops": {"label": "Azure DevOps", "config": ["organization", "project"],
                     "secrets": ["token"],
                     "help": "A PAT with Work Items read/write and Test Management read."},
}


@router.get("/providers")
def providers():
    return {"providers": [{"provider": k, **v} for k, v in PROVIDER_SPECS.items()]}


@router.get("")
def list_connections(project: Project = Depends(get_project), db: Session = Depends(get_db)):
    rows = db.execute(
        select(IntegrationConnection).where(IntegrationConnection.project_id == project.id)
    ).scalars()
    return [
        {"id": c.id, "provider": c.provider, "name": c.name, "config": c.config,
         "enabled": c.enabled, "status": c.status, "status_detail": c.status_detail,
         "secrets": sorted(c.secret_refs or {}),
         "last_checked_at": c.last_checked_at.isoformat() if c.last_checked_at else None}
        for c in rows
    ]


@router.post("")
def connect(
    payload: dict,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if not user.at_least(Role.ADMIN):
        raise HTTPException(403, "connecting an integration requires the admin role")
    provider = payload.get("provider", "")
    spec = PROVIDER_SPECS.get(provider)
    if spec is None:
        raise HTTPException(400, f"unknown provider {provider!r}")

    config = payload.get("config") or {}
    missing = [k for k in spec["config"] if not config.get(k)]
    if missing:
        raise HTTPException(400, f"missing required settings: {', '.join(missing)}")
    supplied = payload.get("secrets") or {}
    missing_secrets = [k for k in spec["secrets"] if not supplied.get(k)]
    if missing_secrets:
        raise HTTPException(400, f"missing required credentials: {', '.join(missing_secrets)}")

    connection = db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.project_id == project.id,
            IntegrationConnection.provider == provider,
        )
    ).scalar_one_or_none()
    if connection is None:
        connection = IntegrationConnection(
            project_id=project.id, provider=provider,
            name=payload.get("name") or spec["label"], created_by=user.id,
        )
        db.add(connection)
    connection.config = config
    connection.enabled = True
    connection.status = "unverified"

    refs = dict(connection.secret_refs or {})
    for name, value in supplied.items():
        if not value:
            continue
        secret_name = f"{provider}.{name}"
        secret = db.execute(
            select(VaultSecret).where(
                VaultSecret.project_id == project.id, VaultSecret.name == secret_name
            )
        ).scalar_one_or_none()
        if secret is None:
            secret = VaultSecret(project_id=project.id, name=secret_name, kind=provider,
                                 created_by=user.id)
            db.add(secret)
            db.flush()
        secret.ciphertext = seal(value, aad=f"{project.id}:{secret_name}")
        secret.hint = hint_for(value)
        refs[name] = secret.id
    connection.secret_refs = refs
    db.flush()

    audit.record(
        db, action="integration.connected", actor_id=user.id, actor_label=user.email,
        project_id=project.id, resource_type="integration", resource_id=connection.id,
        detail={"provider": provider, "config_keys": sorted(config),
                "secret_names": sorted(refs)},
    )
    db.commit()
    return {"id": connection.id, "provider": provider, "status": connection.status}


@router.post("/{provider}/verify")
def verify(
    provider: str,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
):
    verifiers = {}
    from ...integrations import jira, xray

    verifiers["jira"] = jira.verify
    verifiers["xray"] = xray.verify
    fn = verifiers.get(provider)
    if fn is None:
        raise HTTPException(400, f"verification is not implemented for {provider!r}")

    connection = db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.project_id == project.id,
            IntegrationConnection.provider == provider,
        )
    ).scalar_one_or_none()
    try:
        result = fn(db, project_id=project.id)
        if connection:
            connection.status = "connected"
            connection.status_detail = ""
            connection.last_checked_at = utcnow()
        db.commit()
        return result
    except IntegrationError as exc:
        if connection:
            connection.status = "error"
            connection.status_detail = str(exc)
            connection.last_checked_at = utcnow()
            db.commit()
        raise HTTPException(400, str(exc)) from exc


@router.post("/ci/report")
async def ci_report(
    payload: dict, project: Project = Depends(get_project), db: Session = Depends(get_db)
):
    from ...integrations.ci import fetch_report, parse_report

    if content := payload.get("content"):
        # Direct upload path: works with no CI connection at all, which matters
        # for air-gapped installs that cannot reach a CI API.
        try:
            return parse_report(content).as_dict()
        except IntegrationError as exc:
            raise HTTPException(400, str(exc)) from exc
    try:
        return await fetch_report(
            db, project_id=project.id,
            provider=payload.get("provider", ""), reference=payload.get("reference", ""),
        )
    except IntegrationError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/{connection_id}")
def disconnect(
    connection_id: str,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    user: User = Depends(current_user),
):
    if not user.at_least(Role.ADMIN):
        raise HTTPException(403, "requires the admin role")
    connection = db.get(IntegrationConnection, connection_id)
    if connection is None or connection.project_id != project.id:
        raise HTTPException(404, "connection not found")
    audit.record(db, action="integration.disconnected", actor_id=user.id,
                 project_id=project.id, resource_type="integration",
                 resource_id=connection_id, detail={"provider": connection.provider})
    db.delete(connection)
    db.commit()
    return {"disconnected": connection_id}
