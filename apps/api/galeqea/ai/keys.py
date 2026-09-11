"""Bring-your-own-key storage and resolution.

The first version of this set the key on an in-memory settings object, which
meant it survived exactly until the next restart and applied to every project at
once. This module makes it real:

* **Persisted** in the same envelope-encrypted vault as every other credential.
* **Scoped** per project, with a global entry as the fallback, so one workspace
  can use a local model while another uses a hosted one.
* **Validated before it is stored.** A key is probed against the provider first;
  if the probe fails the key is rejected with the provider's own message rather
  than being saved and failing silently at 2am during a scheduled run.
* **Never returned.** Reads give a hint (`sk-ant-…4f2a`) so a human can confirm
  which key is wired up without it entering a response body or a log.
* **Budgeted.** An optional monthly spend cap, enforced from the usage ledger
  before a request is made, not reconciled afterwards when the money is gone.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..core.vault import hint_for, seal, unseal
from ..models import UsageLedger, VaultSecret
from ..models.base import utcnow

#: One vault entry per (scope, provider). "global" is the fallback scope.
GLOBAL_SCOPE = "__global__"


def _secret_name(provider: str) -> str:
    return f"model.{provider}.api_key"


@dataclass(slots=True)
class ModelCredential:
    provider: str
    scope: str                 # a project id, or GLOBAL_SCOPE
    hint: str
    model: str = ""
    base_url: str = ""
    monthly_budget_usd: float = 0.0
    spend_this_month: float = 0.0
    last_used_at: datetime | None = None
    verified_at: datetime | None = None

    @property
    def over_budget(self) -> bool:
        return bool(self.monthly_budget_usd) and self.spend_this_month >= self.monthly_budget_usd

    def as_dict(self) -> dict:
        return {
            "provider": self.provider,
            "scope": "global" if self.scope == GLOBAL_SCOPE else self.scope,
            # The value never leaves the vault. Only enough to recognise it.
            "hint": self.hint,
            "model": self.model,
            "base_url": self.base_url,
            "monthly_budget_usd": self.monthly_budget_usd,
            "spend_this_month": round(self.spend_this_month, 4),
            "over_budget": self.over_budget,
            "last_used_at": self.last_used_at.isoformat() if self.last_used_at else None,
            "verified_at": self.verified_at.isoformat() if self.verified_at else None,
        }


class KeyError_(RuntimeError):
    """Raised with a message safe to show a user."""


class RoleCeilingExceeded(KeyError_):
    """A single model call in a role would run past its per-call token ceiling.

    Raised *before* the call, not reconciled after it. The whole point of a
    ceiling is to stop and ask rather than discover the spend in the bill. Carries
    the role, the estimate and the cap so the caller can degrade gracefully (fall
    back to the deterministic tier) and tell the user exactly what tripped it.
    """

    def __init__(self, role: str, est_tokens: int, ceiling: int):
        self.role, self.est_tokens, self.ceiling = role, est_tokens, ceiling
        super().__init__(
            f"a single {role} call would use about {est_tokens} tokens, over its "
            f"per-call ceiling of {ceiling}. Stopping rather than spending. Raise "
            f"the ceiling in Settings → Model (per-role) or trim the page state. "
            "Everything that does not need a model still works."
        )


# --------------------------------------------------------------------------- #
async def verify(provider: str, *, api_key: str, model: str = "", base_url: str = "") -> dict:
    """Probe a key against its provider before storing it.

    Storing an unverified key trades a five-second wait now for a failure in the
    middle of an unattended run later, with the provider's real error message
    long gone.
    """
    from .providers.base import ProviderError
    from .providers.registry import build_provider

    try:
        instance = build_provider(
            provider=provider, model=model or None, api_key=api_key, base_url=base_url or None
        )
    except ProviderError as exc:
        return {"ok": False, "error": str(exc)}

    try:
        health = await instance.health()
    except Exception as exc:  # noqa: BLE001 - providers raise their own types
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    finally:
        await instance.aclose()

    ok = health.get("status") in {"ready", "no_ai_mode"}
    return {
        "ok": ok,
        "error": "" if ok else (health.get("detail") or health.get("status", "unknown")),
        "health": health,
    }


def store(
    db: Session,
    *,
    provider: str,
    api_key: str,
    scope: str,
    model: str = "",
    base_url: str = "",
    monthly_budget_usd: float = 0.0,
    created_by: str | None = None,
) -> ModelCredential:
    """Seal a verified key into the vault. Callers must verify first."""
    if not api_key:
        raise KeyError_("no key was supplied")

    name = _secret_name(provider)
    project_id = None if scope == GLOBAL_SCOPE else scope

    secret = db.execute(
        select(VaultSecret).where(
            VaultSecret.name == name,
            VaultSecret.project_id.is_(None) if project_id is None
            else VaultSecret.project_id == project_id,
        )
    ).scalar_one_or_none()

    if secret is None:
        secret = VaultSecret(project_id=project_id, name=name, kind="model_api_key",
                             created_by=created_by)
        db.add(secret)

    secret.ciphertext = seal(api_key, aad=f"{scope}:{name}")
    secret.hint = hint_for(api_key)
    secret.rotated_at = utcnow()
    # Non-secret configuration rides alongside the key so a project's whole
    # model setup moves as one unit.
    secret.meta = {
        "model": model,
        "base_url": base_url,
        "monthly_budget_usd": float(monthly_budget_usd or 0.0),
        "verified_at": utcnow().isoformat(),
    }
    db.flush()
    return _to_credential(db, secret, provider)


def resolve(db: Session, *, provider: str, project_id: str | None) -> str | None:
    """Return the plaintext key for a provider, project first then global."""
    name = _secret_name(provider)

    for scope, clause in (
        (project_id, VaultSecret.project_id == project_id),
        (GLOBAL_SCOPE, VaultSecret.project_id.is_(None)),
    ):
        if scope is None:
            continue
        secret = db.execute(
            select(VaultSecret).where(VaultSecret.name == name, clause)
        ).scalar_one_or_none()
        if secret is None:
            continue
        secret.last_used_at = utcnow()
        try:
            return unseal(secret.ciphertext, aad=f"{scope}:{name}")
        except Exception as exc:  # noqa: BLE001
            raise KeyError_(
                f"the stored {provider} key could not be decrypted ({exc}). "
                "If the vault key changed, re-enter it in Settings → Model."
            ) from exc
    return None


def config_for(db: Session, *, provider: str, project_id: str | None) -> dict:
    """Model and endpoint stored alongside the key, project first."""
    name = _secret_name(provider)
    for clause in (VaultSecret.project_id == project_id, VaultSecret.project_id.is_(None)):
        if clause is None:
            continue
        secret = db.execute(
            select(VaultSecret).where(VaultSecret.name == name, clause)
        ).scalar_one_or_none()
        if secret is not None:
            return dict(secret.meta or {})
    return {}


def set_role_models(db: Session, *, provider: str, project_id: str | None,
                    role_models: dict) -> dict:
    """Merge per-role model overrides (planner/grounder/judge) into a provider's
    stored config, without touching its key. Updates the config the resolver reads
    (project first, then global); creates a project-scoped meta entry if none exists
    (fine for a local/no-key provider). Returns the merged role_models."""
    name = _secret_name(provider)
    secret = None
    for clause in (VaultSecret.project_id == project_id, VaultSecret.project_id.is_(None)):
        if clause is None:
            continue
        secret = db.execute(select(VaultSecret).where(
            VaultSecret.name == name, clause)).scalar_one_or_none()
        if secret is not None:
            break
    if secret is None:
        secret = VaultSecret(project_id=project_id, name=name, kind="model_api_key")
        db.add(secret)
    meta = dict(secret.meta or {})
    merged = {**(meta.get("role_models") or {}), **{k: v for k, v in role_models.items() if v}}
    meta["role_models"] = merged
    secret.meta = meta
    db.flush()
    return merged


def set_role_ceilings(db: Session, *, provider: str, project_id: str | None,
                      role_ceilings: dict) -> dict:
    """Merge per-role per-call token ceilings (planner/grounder/judge) into a
    provider's stored config. A value of 0 (or falsy) clears that role's ceiling.
    Returns the merged mapping."""
    name = _secret_name(provider)
    secret = None
    for clause in (VaultSecret.project_id == project_id, VaultSecret.project_id.is_(None)):
        if clause is None:
            continue
        secret = db.execute(select(VaultSecret).where(
            VaultSecret.name == name, clause)).scalar_one_or_none()
        if secret is not None:
            break
    if secret is None:
        secret = VaultSecret(project_id=project_id, name=name, kind="model_api_key")
        db.add(secret)
    meta = dict(secret.meta or {})
    current = dict(meta.get("role_ceilings") or {})
    for role, cap in role_ceilings.items():
        try:
            cap = int(cap)
        except (TypeError, ValueError):
            continue
        if cap > 0:
            current[role] = cap
        else:
            current.pop(role, None)
    meta["role_ceilings"] = current
    secret.meta = meta
    db.flush()
    return current


def role_ceiling(db: Session, *, provider: str, project_id: str | None, bucket: str) -> int | None:
    """The per-call token ceiling configured for a role bucket, if any."""
    if provider in (None, "", "none"):
        return None
    meta = config_for(db, provider=provider, project_id=project_id)
    cap = (meta.get("role_ceilings") or {}).get(bucket)
    try:
        cap = int(cap)
    except (TypeError, ValueError):
        return None
    return cap if cap > 0 else None


def check_role_ceiling(db: Session, *, provider: str, project_id: str | None,
                       bucket: str, est_tokens: int) -> None:
    """Refuse a call whose estimated tokens run past the role's per-call ceiling.

    A no-op when no ceiling is configured for the bucket. Estimate is the trimmed
    page state (WO#8-A ``state_tokens``) plus a fixed prompt-scaffold allowance.
    """
    cap = role_ceiling(db, provider=provider, project_id=project_id, bucket=bucket)
    if cap is None:
        return
    if int(est_tokens or 0) > cap:
        raise RoleCeilingExceeded(bucket, int(est_tokens or 0), cap)


def listing(db: Session, project_id: str | None = None) -> list[ModelCredential]:
    rows = db.execute(
        select(VaultSecret).where(VaultSecret.kind == "model_api_key")
    ).scalars()
    out: list[ModelCredential] = []
    for secret in rows:
        if project_id and secret.project_id not in (None, project_id):
            continue
        provider = secret.name.removeprefix("model.").removesuffix(".api_key")
        out.append(_to_credential(db, secret, provider))
    out.sort(key=lambda c: (c.scope != GLOBAL_SCOPE, c.provider))
    return out


def revoke(db: Session, *, provider: str, scope: str) -> bool:
    name = _secret_name(provider)
    project_id = None if scope == GLOBAL_SCOPE else scope
    secret = db.execute(
        select(VaultSecret).where(
            VaultSecret.name == name,
            VaultSecret.project_id.is_(None) if project_id is None
            else VaultSecret.project_id == project_id,
        )
    ).scalar_one_or_none()
    if secret is None:
        return False
    db.delete(secret)
    db.flush()
    return True


# --------------------------------------------------------------------------- #
def spend_this_month(db: Session, *, provider: str, project_id: str | None) -> float:
    """Cost attributed to this provider since the start of the month."""
    start = datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    stmt = select(UsageLedger).where(
        UsageLedger.provider == provider, UsageLedger.created_at >= start
    )
    if project_id:
        stmt = stmt.where(UsageLedger.project_id == project_id)
    return sum(row.cost_usd or 0.0 for row in db.execute(stmt).scalars())


def check_budget(db: Session, *, provider: str, project_id: str | None) -> None:
    """Refuse a request that would run past the cap.

    Checked before the call, not reconciled after it: a budget you discover you
    have exceeded is a bill, not a budget.
    """
    meta = config_for(db, provider=provider, project_id=project_id)
    budget = float(meta.get("monthly_budget_usd") or 0.0)
    if not budget:
        return
    spent = spend_this_month(db, provider=provider, project_id=project_id)
    if spent >= budget:
        raise KeyError_(
            f"the monthly budget for {provider} (${budget:.2f}) is spent "
            f"(${spent:.2f} so far). Raise it in Settings → Model, or wait for "
            "the month to roll over. Everything that does not need a model still works."
        )


def _to_credential(db: Session, secret: VaultSecret, provider: str) -> ModelCredential:
    meta = secret.meta or {}
    scope = GLOBAL_SCOPE if secret.project_id is None else secret.project_id
    verified = meta.get("verified_at")
    return ModelCredential(
        provider=provider,
        scope=scope,
        hint=secret.hint,
        model=meta.get("model", ""),
        base_url=meta.get("base_url", ""),
        monthly_budget_usd=float(meta.get("monthly_budget_usd") or 0.0),
        spend_this_month=spend_this_month(
            db, provider=provider, project_id=secret.project_id
        ),
        last_used_at=secret.last_used_at,
        verified_at=datetime.fromisoformat(verified) if verified else None,
    )
