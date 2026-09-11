"""Access: establish and reuse authenticated sessions for a target.

A target can sit behind more than one wall (a form login on ``/secure``, HTTP
basic on ``/basic_auth``), so credentials are a **list** of entries, each keyed by
its kind (form / basic / digest) and an optional scope path prefix. Discovery
records which kind guards which page (``auth_requirements``); the compiler injects
the matching credential per test (a sealed storageState for a form login, or
httpCredentials for basic/digest), and a page whose required kind has no matching
credential is *skipped with a reason*, never failed, so the readiness verdict is
never manufactured by a missing credential.

Credentials and any captured session are sealed in the vault; the plaintext lives
only inside the transient runner plan, never in the transcript, the audit ledger,
a webhook, a report, or a stored test.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

from ..core import vault
from ..models.base import utcnow


def _bundle(journey) -> dict:
    """The auth bundle on the journey, migrating a legacy single-dict to a list."""
    auth = (journey.guardrails or {}).get("auth") or {}
    if not auth:
        return {"credentials": []}
    if "credentials" in auth:
        return {"credentials": list(auth["credentials"])}
    # Legacy single-credential dict → one entry.
    if auth.get("creds_sealed"):
        entry = {k: auth[k] for k in auth if k != "kind"} | {"kind": auth.get("kind", "form"),
                                                              "scope": ""}
        return {"credentials": [entry]}
    return {"credentials": []}


def _set_bundle(db, journey, bundle: dict) -> None:
    guardrails = dict(journey.guardrails or {})
    guardrails["auth"] = bundle
    journey.guardrails = guardrails
    db.add(journey)
    db.flush()


def _credentials(journey) -> list[dict]:
    return _bundle(journey)["credentials"]


def _mask(username: str) -> str:
    if not username:
        return ""
    return f"{username[0]}***{username[-1]}" if len(username) > 2 else "***"


def detected_kinds(journey) -> list[str]:
    """The auth kinds discovery saw behind the wall (basic / digest / form), most
    common first, so the Access card offers the right prompt(s)."""
    kinds: list[str] = []
    for f in (journey.discovery or {}).get("findings") or []:
        if f.get("kind") == "auth_gated" and f.get("auth_kind") and f["auth_kind"] not in kinds:
            kinds.append(f["auth_kind"])
    return kinds


def auth_requirements(journey) -> dict[str, str]:
    """Map of path → required auth kind, from discovery. Survives promotion so the
    compiler still knows a page needs basic even after it's in the tested set."""
    disc = journey.discovery or {}
    reqs = dict(disc.get("auth_requirements") or {})
    for f in disc.get("findings") or []:
        if f.get("kind") == "auth_gated" and f.get("url") and f.get("auth_kind"):
            reqs.setdefault(urlparse(f["url"]).path or "/", f["auth_kind"])
    return reqs


def required_kind(journey, page_url: str) -> str | None:
    return auth_requirements(journey).get(urlparse(page_url).path or "/")


def store_credentials(db, journey, *, username: str, password: str,
                      kind: str | None = None, scope: str = "",
                      login_url: str | None = None, success_text: str | None = None) -> dict:
    """Seal a credential onto the journey's credential list, upserting by
    (kind, scope). Only a masked username hint is kept in the clear."""
    if not kind:
        kind = (detected_kinds(journey) or ["form"])[0]
    sealed = vault.seal(json.dumps({"username": username, "password": password}),
                        aad=f"creds:{journey.project_id}")
    entry = {"kind": kind, "scope": scope, "creds_sealed": sealed,
             "username_hint": _mask(username), "login_url": login_url or journey.target,
             "success_text": success_text, "stored_at": utcnow().isoformat()}
    bundle = _bundle(journey)
    bundle["credentials"] = [e for e in bundle["credentials"]
                             if not (e.get("kind") == kind and e.get("scope", "") == scope)]
    bundle["credentials"].append(entry)
    _set_bundle(db, journey, bundle)
    db.commit()
    return {"kind": kind, "scope": scope, "username_hint": entry["username_hint"],
            "login_url": entry["login_url"], "credentials": len(bundle["credentials"])}


def has_credentials(journey) -> bool:
    return bool(_credentials(journey))


def covered_kinds(journey) -> set[str]:
    return {e["kind"] for e in _credentials(journey)}


def credential_for(journey, kind: str, path: str = "") -> dict | None:
    """The best credential for a page: same kind, longest scope prefix of the path
    (an empty scope matches any path)."""
    best, best_len = None, -1
    for e in _credentials(journey):
        if e.get("kind") != kind:
            continue
        scope = e.get("scope", "") or ""
        if scope and not path.startswith(scope):
            continue
        if len(scope) > best_len:
            best, best_len = e, len(scope)
    return best


def _creds_of(entry: dict, project_id: str) -> dict:
    return json.loads(vault.unseal(entry["creds_sealed"], aad=f"creds:{project_id}"))


def forget(db, journey) -> bool:
    """Wipe all stored credentials and sessions for the target."""
    guardrails = dict(journey.guardrails or {})
    had = bool(guardrails.get("auth"))
    guardrails.pop("auth", None)
    journey.guardrails = guardrails
    db.add(journey)
    db.commit()
    return had


def run_auth(journey) -> dict | None:
    """The sealed auth bundle to attach to a run: every stored credential (its
    sealed session for form, sealed creds for basic/digest) plus the per-path kind
    requirements, so the compiler can inject per test. None when nothing is stored."""
    creds = _credentials(journey)
    if not creds:
        return None
    return {"credentials": creds, "requirements": auth_requirements(journey)}


def _next_run_number(db, project_id: str) -> int:
    from sqlalchemy import func, select

    from ..models import Run
    return (db.execute(
        select(func.coalesce(func.max(Run.number), 0)).where(Run.project_id == project_id)
    ).scalar_one()) + 1


async def perform_login(db, *, project, journey, triggered_by: str | None = None,
                        headless: bool = True) -> dict:
    """Sign in each stored **form** credential that has no session yet, sealing the
    captured session onto its entry. Basic/digest need no login; their creds are
    replayed per run. Returns whether at least one form login succeeded."""
    from ..engine.supervisor import RunSupervisor
    from ..models import Run, RunStatus

    bundle = _bundle(journey)
    forms = [e for e in bundle["credentials"] if e["kind"] == "form" and not e.get("session_sealed")]
    if not bundle["credentials"]:
        return {"ok": False, "error": "no credentials stored for this target"}
    if not forms:
        # Only basic/digest: nothing to log into; the creds are the session.
        return {"ok": True, "kind": "basic", "no_login_needed": True}

    ok_any, last_run, landing = False, None, None
    for entry in forms:
        creds = _creds_of(entry, project.id)
        run = Run(project_id=project.id, number=_next_run_number(db, project.id),
                  title=f"Login: {journey.target}", trigger="login", triggered_by=triggered_by,
                  environment=journey.environment, base_url=journey.target,
                  status=RunStatus.RUNNING, started_at=utcnow(), selection={}, totals={"total": 0})
        db.add(run)
        db.commit()
        login = {"kind": "form", "url": entry.get("login_url") or journey.target,
                 "username": creds["username"], "password": creds["password"],
                 "successText": entry.get("success_text")}
        result = await RunSupervisor().login(run_id=run.id, project_id=project.id,
                                             login=login, headless=headless)
        db.expire_all()
        run = db.get(Run, run.id)
        ok = bool(result.get("ok"))
        run.status = RunStatus.PASSED if ok else RunStatus.FAILED
        run.finished_at = utcnow()
        db.commit()
        last_run = run
        if ok and result.get("sealed"):
            ok_any = True
            entry["session_sealed"] = result["sealed"]
            entry["session_at"] = utcnow().isoformat()
            _set_bundle(db, journey, bundle)
            landing = landing or _add_landing_page(db, journey, result.get("landing_url"))
            db.commit()
    return {"ok": ok_any, "kind": "form", "run_id": last_run.id if last_run else None,
            "run_number": last_run.number if last_run else None, "landing": landing,
            "error": None if ok_any else "the sign-in did not complete"}


def _add_landing_page(db, journey, landing_url: str | None) -> str | None:
    """The page a form login lands on (e.g. /secure) is often not linked before you
    sign in, so it was never discovered. Add it to the tested set now."""
    if not landing_url:
        return None
    disc = dict(journey.discovery or {})
    base_host = urlparse(disc.get("base") or journey.target).netloc
    if urlparse(landing_url).netloc != base_host:
        return None  # never follow the login off-site
    pages = list(disc.get("pages") or [])
    if landing_url in pages or landing_url.rstrip("/") in [p.rstrip("/") for p in pages]:
        return None
    disc["pages"] = pages + [landing_url]
    disc["discovered"] = len(disc["pages"])
    journey.discovery = disc
    db.add(journey)
    db.flush()
    return landing_url


async def begin_login_handoff(db, *, project_id, journey, login_url, triggered_by=None):
    """Log-in-for-me: file a login test whose one step parks a headed browser for a
    human to sign into, and start it. The run pauses at the handoff and the runner
    seals the resulting session on resume (see supervisor handoff_session)."""
    from ..models import Project, StepAction, TestCase, TestCategory, TestStatus, TestStep
    from .runs import start_run

    project = db.get(Project, project_id)
    key = f"{project.key}-GP-LOGIN"
    tc = db.execute(_by_key(project_id, key)).scalar_one_or_none()
    if tc is None:
        tc = TestCase(project_id=project_id, key=key, title=f"Sign in: {journey.target}",
                      status=TestStatus.APPROVED, category=TestCategory.AUTOMATED,
                      tags=["golden-path", "login"],
                      provenance={"origin": "golden_path", "type": "login", "journey_id": journey.id})
        db.add(tc)
        db.flush()
    else:
        tc.status = TestStatus.APPROVED
        for s in list(tc.steps):
            db.delete(s)
        db.flush()
    db.add(TestStep(test_case_id=tc.id, index=0, action=StepAction.GOTO,
                    intent=f"Open {login_url}", value={"url": login_url}))
    db.add(TestStep(test_case_id=tc.id, index=1, action=StepAction.HANDOFF,
                    intent="Sign in in the browser window, then resume",
                    value={"instructions": "Complete the login, then click Resume."}))
    db.commit()
    run = await start_run(
        db, project_id=project_id, selection={"keys": [key]},
        environment=journey.environment, trigger="login", triggered_by=triggered_by,
        title=f"Log-in-for-me: {journey.target}", command="log in for me",
        headless=False, auth={"login_handoff": True, "journey_id": journey.id},
    )
    return {"run_id": run.id, "run_number": run.number, "handoff_key": None}


def seal_handoff_session(db, journey, run) -> bool:
    """Seal a session captured during a log-in-for-me handoff onto a form credential
    (creating a whole-site form entry if none exists yet)."""
    sealed = (run.ci_metadata or {}).get("auth_sealed")
    if not sealed:
        return False
    bundle = _bundle(journey)
    entry = next((e for e in bundle["credentials"] if e["kind"] == "form"), None)
    if entry is None:
        entry = {"kind": "form", "scope": "", "login_url": journey.target,
                 "username_hint": "(interactive)"}
        bundle["credentials"].append(entry)
    entry["session_sealed"] = sealed
    entry["session_at"] = utcnow().isoformat()
    _set_bundle(db, journey, bundle)
    db.commit()
    return True


def _by_key(project_id: str, key: str):
    from sqlalchemy import select

    from ..models import TestCase
    return select(TestCase).where(TestCase.project_id == project_id, TestCase.key == key)


def promote_gated(db, journey) -> list[str]:
    """Move the pages behind the login into the tested set, but *keep* each page's
    required auth kind in ``auth_requirements`` so the compiler still knows what it
    needs. Returns the promoted URLs."""
    disc = dict(journey.discovery or {})
    findings = disc.get("findings") or []
    reqs = dict(disc.get("auth_requirements") or {})
    gated = []
    for f in findings:
        if f.get("kind") == "auth_gated" and f.get("url"):
            gated.append(f["url"])
            reqs[urlparse(f["url"]).path or "/"] = f.get("auth_kind", "form")
    pages = list(disc.get("pages") or [])
    added = [u for u in gated if u not in pages]
    disc["auth_requirements"] = reqs
    if added:
        disc["pages"] = pages + added
        disc["discovered"] = len(disc["pages"])
    disc["findings"] = [f for f in findings if f.get("kind") != "auth_gated"]
    disc["skipped"] = {**(disc.get("skipped") or {}), "auth": 0}
    journey.discovery = disc
    db.add(journey)
    db.commit()
    return added
