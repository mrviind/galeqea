"""GaleQEA API application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import settings
from .core.approvals import registered_actions
from .core.logsetup import configure_logging  # noqa: E402
from .db import run_migrations

configure_logging()
log = logging.getLogger("galeqea")


@asynccontextmanager
async def lifespan(app: FastAPI):
    run_migrations()  # bring the schema to head (fresh, legacy, or already-managed)
    _bootstrap()

    from .services import notify, scheduler, webhooks

    loaded = scheduler.load_all()
    scheduler.start()
    webhooks.register()  # outbound webhook delivery, wired into the event bus
    notify.register()    # Slack / Teams notifications, same bus

    from .core.events import bus
    from .core.metrics import metrics_sink
    bus.add_sink(metrics_sink)  # advance Prometheus counters off the same bus

    from .core import otel
    otel.setup(app)  # optional OpenTelemetry tracing (off unless configured)

    # On the Postgres `full` profile, runs execute in `galeqea worker` processes;
    # bridge their events back so SSE / webhooks / metrics stay live (WO#4 P2-1).
    _bridge_stop = None
    _bridge_task = None
    if settings.queue_kind == "procrastinate":
        import asyncio

        from .core import bus_bridge
        _bridge_stop = asyncio.Event()
        _bridge_task = asyncio.create_task(bus_bridge.run_listener(_bridge_stop))
    log.info(
        "GaleQEA %s ready. mode=%s provider=%s db=%s schedules=%d",
        __version__, settings.ai_mode.value, settings.provider,
        settings.database_url.split("///")[-1], loaded,
    )
    log.info("Approval-gated actions: %s", ", ".join(registered_actions()))
    yield

    from .services import runs

    if _bridge_stop is not None:
        _bridge_stop.set()
    if _bridge_task is not None:
        _bridge_task.cancel()
        import contextlib
        with contextlib.suppress(Exception):
            await _bridge_task
    await runs.shutdown()
    scheduler.shutdown()


def _bootstrap() -> None:
    """Seed the first user and a demo project on a fresh install.

    On a shared (multi-user) deployment the first user is the admin: set
    ``GALEQEA_ADMIN_EMAIL`` to name them, otherwise fall back to the local owner
    and log a note so an operator knows to create one.
    """
    import os

    from sqlalchemy import select

    from .core.security import hash_password
    from .db import session_scope
    from .models import Project, Role, User

    admin_email = os.environ.get("GALEQEA_ADMIN_EMAIL", "").strip()
    admin_password = os.environ.get("GALEQEA_ADMIN_PASSWORD", "")
    with session_scope() as db:
        if db.execute(select(User).limit(1)).scalar_one_or_none() is None:
            owner_email = admin_email or "local@galeqea.dev"
            owner = User(email=owner_email,
                         name="Admin" if admin_email else "Local User", role=Role.OWNER)
            # A shared deployment must be loginable on first boot: take the password
            # from the env, or mint a one-time setup URL and print it. Never leave a
            # multi-user admin with no way in (the old behaviour).
            if admin_password:
                owner.password_hash = hash_password(admin_password)
                log.info("Admin %s bootstrapped from GALEQEA_ADMIN_PASSWORD.", owner_email)
            elif not settings.single_user_mode:
                _print_setup_link(owner_email)
            db.add(owner)
            # A machine principal exists so agent-authored records have a real
            # actor id - and it can never satisfy an approval gate.
            db.add(User(
                email="agent@galeqea.local", name="GaleQEA Agent",
                role=Role.AGENT, is_machine=True,
            ))
        if db.execute(select(Project).limit(1)).scalar_one_or_none() is None:
            db.add(Project(
                key="DEMO", name="Demo Project",
                description="A starter project. Upload a requirement document to begin.",
                environments={"local": "http://localhost:3000",
                              "staging": "https://staging.example.com"},
                default_environment="local",
            ))


def _setup_token_file() -> Path:
    return Path(settings.home) / ".setup_token"


def _print_setup_link(email: str) -> None:
    """Mint a one-time setup token so a passwordless multi-user admin can set a
    password on first boot. Only the token's hash is stored on disk."""
    import hashlib
    import secrets

    token = secrets.token_urlsafe(24)
    _setup_token_file().write_text(hashlib.sha256(token.encode()).hexdigest())
    base = settings.base_url or f"http://localhost:{settings.port}"
    log.warning(
        "Multi-user mode and no GALEQEA_ADMIN_PASSWORD set. Set the admin (%s) password "
        "at:  %s/setup?token=%s   (one-time; the link stops working once used).",
        email, base, token)


app = FastAPI(
    title="GaleQEA",
    version=__version__,
    description=(
        "AI-first, open-source test automation agent that runs on any model. Every write passes a "
        "human approval gate; the agent can never approve its own output. "
        "Every report is available as JSON, Markdown and (for runs) JUnit XML. See /api/ai/context."
    ),
    # Serve the schema under /api so the whole machine surface lives beneath one
    # prefix an agent can discover from /api/ai/context.
    openapi_url="/api/openapi.json",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Authlib stores the OIDC state/nonce in a short-lived signed cookie during the
# redirect dance; only added (and only ever set) when SSO is configured.
if settings.oidc_enabled:
    from starlette.middleware.sessions import SessionMiddleware  # noqa: E402

    app.add_middleware(
        SessionMiddleware, secret_key=settings.secret_key, session_cookie="galeqea_oidc",
        same_site="lax", https_only=settings.session_cookie_secure, max_age=600,
    )


# --- rate limiting (slowapi): the login route opts in; nothing else is limited ---
from slowapi.errors import RateLimitExceeded  # noqa: E402

from .core.ratelimit import limiter  # noqa: E402

app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
async def _rate_limited(request: Request, exc: RateLimitExceeded):
    return JSONResponse({"detail": "rate limit exceeded, slow down"}, status_code=429)


# --- Content-Security-Policy -------------------------------------------------
# script-src is strict ('self' + hashes of the few inline scripts the built UI
# ships, so no 'unsafe-inline'); style-src keeps 'unsafe-inline' for React's
# style attributes (style injection can't execute code) and allows Google Fonts.
def _inline_script_hashes() -> list[str]:
    import base64
    import hashlib
    import re

    index = _WEB_DIST / "index.html"
    if not index.exists():
        return []
    html = index.read_text()
    hashes = []
    for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.DOTALL):
        digest = hashlib.sha256(m.group(1).encode()).digest()
        hashes.append(f"'sha256-{base64.b64encode(digest).decode()}'")
    return hashes


_WEB_DIST = Path(__file__).resolve().parents[2] / "web" / "dist"
_CSP = "; ".join([
    "default-src 'self'",
    "base-uri 'self'",
    "object-src 'none'",
    "frame-ancestors 'none'",
    "form-action 'self'",
    "img-src 'self' data: blob:",
    "font-src 'self' https://fonts.gstatic.com data:",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "script-src 'self' " + " ".join(_inline_script_hashes()),
    "connect-src 'self'",
])

_MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
# /api/* paths reachable without a session: health, the first-run setup, the login
# and its public config, the agent surface map, public share links, and the
# read-only capability/doc endpoints the login screen needs to bootstrap.
_PUBLIC_API = (
    "/api/health", "/api/setup", "/api/auth/login", "/api/auth/config",
    "/api/auth/oidc",  # SSO login + callback are pre-session by definition
    "/api/ai/context", "/api/shared/", "/api/capabilities",
    "/api/openapi.json", "/api/docs", "/api/redoc",
)


def _has_bearer(request: Request) -> bool:
    auth = request.headers.get("authorization", "")
    return auth.lower().startswith("bearer ")


def _gate(request: Request) -> JSONResponse | None:
    """The coarse auth/CSRF gate: a JSONResponse to short-circuit, or None to pass.
    Per-route dependencies still do the real user/role/scope checks."""
    from .core.security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE

    path = request.url.path
    if not (path.startswith("/api/") and not path.startswith(_PUBLIC_API)):
        return None
    # Single-user desktop is fully trusted and login-free; the gate (401 / CSRF /
    # viewer) does not apply, and a stale session cookie must never lock it.
    if settings.single_user_mode:
        return None
    has_session = bool(request.cookies.get(SESSION_COOKIE))
    if not settings.single_user_mode and not has_session and not _has_bearer(request):
        return JSONResponse({"detail": "authentication required"}, status_code=401)
    # CSRF double-submit: only for a cookie-authenticated mutation (a bearer token
    # is immune, since a browser never sends it automatically).
    if request.method in _MUTATING and has_session and not _has_bearer(request):
        import secrets

        cookie_tok = request.cookies.get(CSRF_COOKIE) or ""
        header_tok = request.headers.get(CSRF_HEADER) or ""
        if not cookie_tok or not secrets.compare_digest(cookie_tok, header_tok):
            return JSONResponse({"detail": "CSRF token missing or invalid"}, status_code=403)
        # Viewers are read-only (broad guard from the session role claim; privileged
        # routes still do their own live check).
        from .core.security import decode_access_token

        try:
            claims = decode_access_token(request.cookies[SESSION_COOKIE])
            if claims.get("role") == "viewer":
                return JSONResponse({"detail": "viewers have read-only access"}, status_code=403)
        except Exception:  # noqa: BLE001 - a bad token is handled by the route deps
            pass
    return None


@app.middleware("http")
async def api_guard(request: Request, call_next):
    import uuid

    from .core.logsetup import bind_request_id, clear_request_context

    # Every request gets an id (honouring an upstream X-Request-ID), bound to the log
    # context so all of a request's log lines share it, and echoed on the response.
    request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
    request.state.request_id = request_id
    bind_request_id(request_id)
    try:
        blocked = _gate(request)
        response = blocked if blocked is not None else await call_next(request)
    finally:
        clear_request_context()
    response.headers["x-request-id"] = request_id
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Content-Security-Policy", _CSP)
    return response


from .api.routes import (  # noqa: E402
    ai,
    apispec,
    auth,
    chat,
    defects,
    gdpr,
    governance,
    integrations,
    intelligence,
    journeys,
    library,
    projects,
    recordings,
    releases,
    reports,
    requirements,
    runs,
    shared,
    stream,
    tests,
    tokens,
    webhooks,
)

for router in (
    projects.router, requirements.router, apispec.router, recordings.router,
    tests.router, runs.router, chat.router,
    governance.router, governance.settings_router, intelligence.router,
    integrations.router, library.router, stream.router, reports.router, ai.router,
    webhooks.router, journeys.router, shared.router,
    auth.router, tokens.router, gdpr.router, releases.router,
    defects.router,
):
    app.include_router(router)


@app.get("/api/health")
async def health():
    from . import buildinfo
    from .services.runs import active_runs

    return {
        "status": "ok",
        # Provenance of the process actually answering this request: the guard
        # against a stale, reload-less server silently serving old code.
        **buildinfo.as_dict(),
        "ai_mode": settings.ai_mode.value,
        "ai": {
            "mode": settings.ai_mode.value,
            "provider": settings.provider,
            "enabled": settings.ai_enabled,
        },
        "approval_mode": settings.approval_mode.value,
        "ai_self_approval": "structurally prohibited",
        "active_runs": active_runs(),
        "offline_capable": True,
    }


@app.get("/api/capabilities")
def capabilities():
    """What this installation can do right now - drives the UI's empty states."""
    import shutil

    from . import buildinfo
    from .ai.providers.registry import describe_modes
    from .ai.toolset import tool_catalog

    runner_present = Path(settings.runner_entry).exists()
    return {
        "version": __version__,
        "build": buildinfo.as_dict(),
        "ai_modes": describe_modes(),
        "tools": tool_catalog(),
        "approval_actions": registered_actions(),
        "execution": {
            "runner_installed": runner_present,
            "node_present": bool(shutil.which(settings.runner_command)),
            "hint": "" if runner_present else
                    "Run `npm install` in apps/runner and `npx playwright install chromium`.",
        },
        "export_targets": ["playwright", "playwright_python", "pytest", "robot", "cucumber"],
    }


@app.post("/api/setup")
def api_setup(payload: dict):
    """One-time admin setup: exchange the printed token for an admin password. Works
    only while the token file exists (i.e. the admin has no password yet)."""
    import hashlib
    import hmac

    from sqlalchemy import select

    from .core.security import hash_password
    from .db import session_scope
    from .models import Role, User

    token = (payload or {}).get("token", "")
    password = (payload or {}).get("password", "")
    if not token or len(password) < 8:
        raise HTTPException(400, "a token and a password of at least 8 characters are required")
    token_file = _setup_token_file()
    if not token_file.exists():
        raise HTTPException(410, "setup already completed. This link is spent")
    if not hmac.compare_digest(token_file.read_text().strip(),
                               hashlib.sha256(token.encode()).hexdigest()):
        raise HTTPException(403, "invalid setup token")
    with session_scope() as db:
        owner = db.execute(
            select(User).where(User.role == Role.OWNER, User.password_hash == "")
            .order_by(User.created_at)
        ).scalars().first()
        if owner is None:
            raise HTTPException(404, "no admin account to set up")
        owner.password_hash = hash_password(password)
        email = owner.email
    token_file.unlink(missing_ok=True)
    return {"ok": True, "email": email}


@app.get("/setup", include_in_schema=False)
async def setup_page():
    """A minimal, self-contained page to set the admin password from the setup link."""
    from fastapi.responses import HTMLResponse
    if not _setup_token_file().exists():
        return HTMLResponse("<p>Setup is already complete. <a href='/'>Open GaleQEA</a>.</p>")
    return HTMLResponse(_SETUP_HTML)


_SETUP_HTML = """<!doctype html><meta charset=utf-8><title>GaleQEA: set admin password</title>
<style>body{font:15px system-ui;max-width:420px;margin:12vh auto;padding:0 20px;background:#0e1116;color:#e6edf3}
input{width:100%;padding:9px;margin:6px 0 14px;border:1px solid #2a2f3a;border-radius:8px;background:#161b22;color:#e6edf3}
button{padding:9px 16px;border:0;border-radius:8px;background:#d4a017;color:#111;font-weight:600;cursor:pointer}
.msg{margin-top:12px}</style>
<h2>Set the admin password</h2>
<p style=color:#9aa4b2>One-time setup from your invite link.</p>
<input id=pw type=password placeholder="New password (min 8 chars)" autofocus>
<button onclick=go()>Set password</button><div class=msg id=m></div>
<script>
const token=new URLSearchParams(location.search).get('token');
async function go(){const pw=document.getElementById('pw').value;const m=document.getElementById('m');
 const r=await fetch('/api/setup',{method:'POST',headers:{'content-type':'application/json'},
  body:JSON.stringify({token,password:pw})});
 if(r.ok){m.style.color='#3fb950';m.textContent='Done. You can now sign in.';setTimeout(()=>location.href='/',1200);}
 else{const e=await r.json().catch(()=>({}));m.style.color='#f85149';m.textContent=e.detail||'Failed.';}}
</script>"""


@app.get("/healthz", include_in_schema=False)
async def healthz():
    """Liveness: the process is up and serving. Cheap; no dependencies touched."""
    return {"status": "alive"}


@app.get("/metrics", include_in_schema=False)
async def metrics():
    """Prometheus scrape endpoint (standard text format)."""
    from fastapi.responses import Response

    from .core.metrics import render

    body, content_type = render()
    return Response(content=body, media_type=content_type)


@app.get("/readyz", include_in_schema=False)
async def readyz():
    """Readiness. Safe to send traffic: DB reachable, runner installed, schema at head."""
    from sqlalchemy import text

    from .db import _alembic_cfg, engine

    checks: dict[str, bool] = {}
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:  # noqa: BLE001
        checks["database"] = False
    checks["runner"] = Path(settings.runner_entry).exists()
    try:
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        with engine.connect() as conn:
            current = MigrationContext.configure(conn).get_current_revision()
        head = ScriptDirectory.from_config(_alembic_cfg()).get_current_head()
        checks["migrations"] = current == head
    except Exception:  # noqa: BLE001
        checks["migrations"] = False

    from . import buildinfo
    ready = all(checks.values())
    return JSONResponse(
        {"status": "ready" if ready else "not_ready", "checks": checks,
         # Reported, not fatal: a stale API (source newer than the process) is a
         # deploy/dev mistake to surface, not a reason to 503 a healthy prod box.
         "stale_api": buildinfo.is_stale()},
        status_code=200 if ready else 503,
    )


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "detail": f"{type(exc).__name__}: {exc}",
            "path": request.url.path,
            "hint": "This is a bug. The full traceback is in the server log.",
        },
    )


# --------------------------------------------------------------------------- #
# Static frontend (single-command deploy serves the built UI from the API)
# --------------------------------------------------------------------------- #
_web_dist = Path(__file__).resolve().parents[2] / "web" / "dist"
if _web_dist.exists():
    app.mount("/assets", StaticFiles(directory=_web_dist / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        # The SPA catch-all must never shadow the API: an unknown /api/... path is
        # a 404, not the index.html the browser would then try to parse as JSON.
        if full_path == "api" or full_path.startswith("api/"):
            raise HTTPException(404, "not found")
        candidate = _web_dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_web_dist / "index.html")
