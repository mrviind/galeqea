"""The first-run on-ramp: point QE Agent at a URL and test it immediately.

A brand-new user has no requirement document, no recorded session and — in No-AI
mode — no model to author tests for them. Without an on-ramp their first minute is
a dead end. This module gives them a real one: they type a URL in the chat (or the
chat asks for it), and QE Agent drives a real browser to that URL, checks it loads
cleanly, and reports what it saw — using the same execution pipeline every other
run uses, with **no model and no manual authoring**.

The smoke check itself is a built-in probe, not AI-authored content, so it does not
pass through the human approval gate — the gate governs *proposed* changes to a
suite, and this is the product's own fixed diagnostic. It is created directly, the
way the demo project is seeded, and marked as a deterministic built-in in its
provenance so its origin is never ambiguous.
"""

from __future__ import annotations

import asyncio
import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (
    Project,
    RunTest,
    StepAction,
    TestCase,
    TestCategory,
    TestStatus,
    TestStep,
)
from ..models.testing import Run, RunStatus

#: The environment key the on-ramp writes the target URL into. Named so it never
#: collides with a user's own "local"/"staging" environments.
TARGET_ENV = "target"

# A full URL, or a bare host like "example.com" / "localhost:8765" / "127.0.0.1:3000",
# optionally with a path. Deliberately conservative: a bare token must contain a dot
# (or be localhost) so ordinary words and version numbers are not mistaken for hosts.
_URL_RE = re.compile(
    r"""(?xi)
    \b(
        https?://[^\s<>"']+                         # explicit scheme
      | (?:localhost|127\.0\.0\.1)(?::\d+)?(?:/[^\s<>"']*)?   # loopback
      | (?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+[a-z]{2,}       # domain.tld
        (?::\d+)?(?:/[^\s<>"']*)?
    )
    """,
)


def find_url(text: str) -> str | None:
    """Extract the first URL-like token from free text, or None."""
    match = _URL_RE.search(text or "")
    return match.group(1).rstrip(".,;)") if match else None


def normalize_url(raw: str) -> str:
    """Add a scheme if the user omitted one. Loopback defaults to http, else https."""
    raw = (raw or "").strip()
    if "://" in raw:
        return raw
    host = raw.split("/", 1)[0]
    scheme = "http" if host.startswith(("localhost", "127.0.0.1")) else "https"
    return f"{scheme}://{raw}"


def looks_like_url(text: str) -> bool:
    return find_url(text) is not None


# "test my site / app / url" with no URL present — the ask-for-URL trigger.
WANTS_TO_TEST = re.compile(
    r"\b(test|smoke|check|try|scan|probe)\b.{0,30}\b"
    r"(site|website|web ?app|app|application|url|page|link|something)\b",
    re.IGNORECASE,
)


def preview(text: str) -> dict | None:
    """The deterministic preview for the on-ramp, or None if this isn't one.

    Mirrors what the orchestrator will actually do, so the composer's command
    preview tells the truth: a URL (or "test my site") is handled with no model.
    """
    url = find_url(text)
    if url:
        target = normalize_url(url)
        return {
            "intent": "test a URL", "confidence": 1.0, "tool": "smoke_test_url",
            "arguments": {"url": target},
            "explanation": f"Open {target} in a real browser and check it loads — no model.",
            "path": "router",
        }
    if WANTS_TO_TEST.search(text or ""):
        return {
            "intent": "test a URL", "confidence": 1.0, "tool": "smoke_test_url",
            "arguments": {},
            "explanation": "I'll ask which URL to test, then check it loads — no model.",
            "path": "router",
        }
    return None


def ensure_smoke_test(db: Session, project: Project) -> TestCase:
    """Get-or-create the project's built-in smoke probe.

    The probe navigates to the target's root and asserts the page renders. The
    runner already fails a navigation on any 4xx/5xx and records console and 5xx
    network errors, so this tiny test surfaces "the site is down / erroring" with
    no further steps.
    """
    key = f"{project.key}-SMOKE"
    existing = db.execute(
        select(TestCase).where(TestCase.project_id == project.id, TestCase.key == key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    case = TestCase(
        project_id=project.id,
        key=key,
        title="Smoke — the site loads without errors",
        description="Built-in first-run check: navigate to the target URL and confirm it renders.",
        status=TestStatus.APPROVED,
        category=TestCategory.AUTOMATED,
        tags=["smoke", "builtin"],
        provenance={"origin": "builtin_smoke", "deterministic": True},
    )
    db.add(case)
    db.flush()
    db.add(TestStep(
        test_case_id=case.id, index=0, action=StepAction.GOTO,
        intent="Open the target URL", value={"url": "/"},
    ))
    db.add(TestStep(
        test_case_id=case.id, index=1, action=StepAction.EXPECT_VISIBLE,
        intent="The page renders",
        target={"ladder": [{"kind": "css", "value": "body"}]},
    ))
    db.commit()
    return case


def set_target(db: Session, project: Project, url: str) -> str:
    """Point the project's target environment at ``url`` and make it the default."""
    url = normalize_url(url)
    project.environments = {**(project.environments or {}), TARGET_ENV: url}
    project.default_environment = TARGET_ENV
    db.commit()
    return url


async def run_smoke(
    db: Session, *, project_id: str, url: str, triggered_by: str | None = None,
    timeout: float = 90.0,
) -> dict:
    """Set the target URL, run the built-in smoke, and return a health report."""
    from .runs import _tasks, start_run

    project = db.get(Project, project_id)
    if project is None:
        return {"ok": False, "error": "unknown project"}

    target = set_target(db, project, url)
    smoke = ensure_smoke_test(db, project)

    run = await start_run(
        db, project_id=project_id,
        selection={"keys": [smoke.key]},
        environment=TARGET_ENV,
        trigger="onramp",
        triggered_by=triggered_by,
        title=f"Smoke check — {target}",
    )

    # Wait for the background execution to finish so we can report inline.
    task = _tasks.get(run.id)
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            return {"ok": True, "run_id": run.id, "run_number": run.number,
                    "status": "running", "target": target, "timed_out": True}

    # Read the durable result from a fresh view of the row.
    db.expire_all()
    run = db.get(Run, run.id)
    report = _report(db, run, target)
    report["run_id"] = run.id
    report["run_number"] = run.number
    return report


def _report(db: Session, run: Run, target: str) -> dict:
    status = run.status
    rt = db.execute(
        select(RunTest).where(RunTest.run_id == run.id)
    ).scalars().first()

    console = list(rt.console_errors) if rt else []
    network = list(rt.network_failures) if rt else []
    ok = status in {RunStatus.PASSED, RunStatus.FLAKY}

    if ok:
        health = f"{target} is up and rendered."
        if console:
            health += f" {len(console)} console error(s) seen."
        if network:
            health += f" {len(network)} server (5xx) response(s)."
    else:
        reason = (rt.error_message if rt and rt.error_message else "the page did not load")
        health = f"{target} did not pass the smoke check: {reason}"

    return {
        "ok": ok,
        "status": status,
        "target": target,
        "console_errors": console[:10],
        "network_failures": network[:10],
        "summary": health,
    }
