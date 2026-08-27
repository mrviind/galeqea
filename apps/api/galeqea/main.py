"""QE Agent API application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import settings
from .core.approvals import registered_actions
from .db import init_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("galeqea")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _bootstrap()

    from .services import scheduler

    loaded = scheduler.load_all()
    scheduler.start()
    log.info(
        "QE Agent %s ready — mode=%s provider=%s db=%s schedules=%d",
        __version__, settings.ai_mode.value, settings.provider,
        settings.database_url.split("///")[-1], loaded,
    )
    log.info("Approval-gated actions: %s", ", ".join(registered_actions()))
    yield

    from .services import runs

    await runs.shutdown()
    scheduler.shutdown()


def _bootstrap() -> None:
    """Seed the local owner and a demo project on a fresh install."""
    from sqlalchemy import select

    from .db import session_scope
    from .models import Project, Role, User

    with session_scope() as db:
        if db.execute(select(User).limit(1)).scalar_one_or_none() is None:
            db.add(User(email="local@galeqea.dev", name="Local User", role=Role.OWNER))
            # A machine principal exists so agent-authored records have a real
            # actor id - and it can never satisfy an approval gate.
            db.add(User(
                email="agent@galeqea.local", name="QE Agent",
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


app = FastAPI(
    title="QE Agent",
    version=__version__,
    description=(
        "AI-first, local-first, open-source test automation. Every write passes a "
        "human approval gate; the AI can never approve its own output."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    return response


from .api.routes import (  # noqa: E402
    apispec,
    chat,
    governance,
    integrations,
    intelligence,
    library,
    projects,
    recordings,
    requirements,
    runs,
    stream,
    tests,
)

for router in (
    projects.router, requirements.router, apispec.router, recordings.router,
    tests.router, runs.router, chat.router,
    governance.router, governance.settings_router, intelligence.router,
    integrations.router, library.router, stream.router,
):
    app.include_router(router)


@app.get("/api/health")
async def health():
    from .services.runs import active_runs

    return {
        "status": "ok",
        "version": __version__,
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

    from .ai.providers.registry import describe_modes
    from .ai.toolset import tool_catalog

    runner_present = Path(settings.runner_entry).exists()
    return {
        "version": __version__,
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
        candidate = _web_dist / full_path
        if full_path and candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_web_dist / "index.html")
