"""Shared integration plumbing: credential resolution and connection health."""

from __future__ import annotations

from dataclasses import dataclass

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.vault import ResolvedSecret, unseal
from ..models import IntegrationConnection, VaultSecret
from ..models.base import utcnow


class IntegrationError(RuntimeError):
    """Raised with a message safe to show a user - never contains a credential."""


@dataclass(slots=True)
class Connection:
    record: IntegrationConnection
    secrets: dict[str, ResolvedSecret]

    @property
    def config(self) -> dict:
        return self.record.config or {}

    def secret(self, name: str) -> str:
        found = self.secrets.get(name)
        if found is None:
            raise IntegrationError(
                f"the '{self.record.provider}' connection is missing its '{name}' credential. "
                "Add it under Settings → Integrations."
            )
        return found.value

    def require(self, key: str) -> str:
        value = self.config.get(key)
        if not value:
            raise IntegrationError(
                f"the '{self.record.provider}' connection is missing required setting '{key}'"
            )
        return value


def load_connection(db: Session, *, project_id: str, provider: str) -> Connection:
    record = db.execute(
        select(IntegrationConnection).where(
            IntegrationConnection.project_id == project_id,
            IntegrationConnection.provider == provider,
            IntegrationConnection.enabled.is_(True),
        )
    ).scalar_one_or_none()
    if record is None:
        raise IntegrationError(
            f"no enabled '{provider}' connection is configured for this project"
        )

    secrets: dict[str, ResolvedSecret] = {}
    for name, secret_id in (record.secret_refs or {}).items():
        secret = db.get(VaultSecret, secret_id)
        if secret is None:
            continue
        secrets[name] = ResolvedSecret(
            name=name,
            value=unseal(secret.ciphertext, aad=f"{secret.project_id}:{secret.name}"),
        )
        secret.last_used_at = utcnow()
    return Connection(record=record, secrets=secrets)


def http_client(*, timeout: float = 45.0) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(timeout, connect=15.0), follow_redirects=True)


def safe_error(response: httpx.Response, *, provider: str) -> IntegrationError:
    """Turn an HTTP failure into a message a user can act on."""
    hints = {
        401: "the credentials were rejected - check the API token and that it has not expired",
        403: "the credentials are valid but lack permission for this operation",
        404: "the referenced project, issue or endpoint does not exist",
        429: "rate limited - wait and retry",
    }
    hint = hints.get(response.status_code, "")
    body = response.text[:300].replace("\n", " ")
    return IntegrationError(
        f"{provider} returned HTTP {response.status_code}"
        + (f": {hint}" if hint else "")
        + (f" ({body})" if body else "")
    )
