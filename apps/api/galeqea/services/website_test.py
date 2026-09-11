"""Agentic 'test a website' flow: explore → plan → (human approves) → test → report.

The on-ramp's instant smoke answers "is it up?". This answers the real question a
tester asks before spending a browser on it: *what should we test, and is that
plan any good?*

Hybrid by design. The **crawl** is deterministic (fetch the page, read its
same-origin links) so it works with no model at all. The **plan** is a deterministic
template of functional and non-functional checks; when a model is configured it is
handed the discovered structure and asked to sharpen the split and priorities. The
plan is never executed until a human approves it in the chat, the same gate every
write goes through.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import httpx

from ..config import settings
from .onramp import normalize_url

PLAN_KEY = "pending_website_plan"

# Discovery caps (the manager's contract): breadth-first, bounded.
CRAWL_MAX_PAGES = 40
CRAWL_MAX_DEPTH = 3

_HREF_RE = re.compile(r"""href\s*=\s*["']([^"'#?]+)""", re.IGNORECASE)
_SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".svg", ".css", ".js", ".ico",
             ".pdf", ".zip", ".woff", ".woff2", ".mp4", ".webm", ".xml", ".json")
#: Tracking params dropped when canonicalising a URL, so `?utm_source=x` and
#: `?gclid=y` don't masquerade as distinct pages.
_TRACKING = {"gclid", "fbclid", "msclkid", "mc_eid", "mc_cid", "igshid",
             "ref", "ref_src", "_hsenc", "_hsmi"}


def _same_site(a: str, b: str) -> bool:
    """apex and www count as one site."""
    a, b = a.lower().removeprefix("www."), b.lower().removeprefix("www.")
    return a == b


def normalize_page_url(url: str) -> str:
    """Clean a page URL for display and *testing*: drop the fragment and tracking
    params (utm_*, gclid, …). The trailing slash is deliberately PRESERVED because
    some servers treat ``/a`` and ``/a/`` as different pages (200 vs 404), so the
    URL we hand the runner must be the one actually discovered. Dedup, which must
    treat the two as one page, is a separate concern; see ``dedupe_key``."""
    p = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in _TRACKING]
    rebuilt = f"{p.scheme}://{p.netloc}{p.path}"
    if query:
        rebuilt += "?" + urlencode(sorted(query))
    return rebuilt


def dedupe_key(url: str) -> str:
    """Same-site *identity* of a page: host without www + path (trailing slash
    normalized away) + surviving query. Two URLs with the same key are the same
    page, but the URL that gets tested keeps its real slash (``normalize_page_url``),
    because collapsing it there would 404 slash-significant routes."""
    p = urlparse(normalize_page_url(url))
    host = p.netloc.lower().removeprefix("www.")
    path = p.path
    if len(path) > 1:
        path = path.rstrip("/") or "/"
    return f"{host}{path}?{p.query}" if p.query else f"{host}{path}"


def _discover_via_runner(base: str, *, max_pages: int, max_depth: int, timeout: float) -> dict | None:
    """Browser-driven discovery via the Playwright runner: renders each page, so
    client-rendered navigation is visible, and classifies auth walls, errors and
    off-site redirects. Returns the parsed result, or None when the runner is
    unavailable or fails; the caller then falls back to the HTTP crawl."""
    runner = Path(settings.runner_entry)
    node = shutil.which(settings.runner_command)
    if not node or not runner.exists():
        return None
    try:
        proc = subprocess.run(
            [node, str(runner), "--discover", base,
             "--max-pages", str(max_pages), "--max-depth", str(max_depth)],
            capture_output=True, text=True, timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    if proc.returncode != 0 or not proc.stdout.strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return data if data.get("ok") else None


def _http_discover(url: str, limit: int, timeout: float) -> dict:
    """HTTP-only crawl: the fallback when the runner is absent. Reads the raw
    HTML the server sends (misses client-rendered nav) but needs no browser."""
    base = normalize_url(url)
    origin = urlparse(base)
    try:
        resp = httpx.get(base, follow_redirects=True, timeout=timeout,
                         headers={"User-Agent": "GaleQEA/1.0 (+website-test)"})
    except httpx.HTTPError as exc:
        return {"ok": False, "error": f"could not reach {base}: {type(exc).__name__}", "base": base}

    final = str(resp.url)
    pages = [normalize_page_url(final)]
    seen = {dedupe_key(final)}
    forms = len(re.findall(r"<form\b", resp.text, re.IGNORECASE))
    for href in _HREF_RE.findall(resp.text):
        full = urljoin(final, href)
        p = urlparse(full)
        if p.scheme not in ("http", "https") or not _same_site(p.netloc, origin.netloc):
            continue
        if p.path.lower().endswith(_SKIP_EXT):
            continue
        key = dedupe_key(full)
        if key in seen:
            continue
        seen.add(key)
        pages.append(normalize_page_url(f"{p.scheme}://{p.netloc}{p.path}"))
        if len(pages) >= limit:
            break
    return {"ok": True, "base": normalize_page_url(final), "pages": pages, "forms": forms,
            "status": resp.status_code, "method": "http", "findings": [],
            "skipped": {"auth": 0, "error": 0, "offsite": 0},
            "truncated": len(pages) >= limit, "discovered": len(pages)}


def discover_pages(url: str, limit: int = 8, timeout: float = 90.0) -> dict:
    """Discover a site's distinct same-origin pages and classify what is found.

    Browser-driven when the runner is available (sees client-rendered nav,
    detects auth walls, HTTP errors and off-site redirects); falls back to an
    HTTP-only crawl otherwise. Deterministic and model-free either way. Pages
    that can't be tested (auth-gated, 4xx/5xx, off-site) are reported as findings
    rather than silently dropped, and a truncated crawl says so.
    """
    base = normalize_url(url)
    browser = _discover_via_runner(base, max_pages=CRAWL_MAX_PAGES,
                                   max_depth=CRAWL_MAX_DEPTH, timeout=timeout)
    if browser is None:
        return _http_discover(url, limit, min(timeout, 15.0))

    records = browser.get("pages", [])
    testable: list[str] = []
    seen: set[str] = set()
    skipped = {"auth": 0, "error": 0, "offsite": 0}
    for r in records:
        if r.get("crossOrigin"):
            skipped["offsite"] += 1
            continue
        if r.get("authGated"):
            skipped["auth"] += 1
            continue
        status = r.get("status")
        if r.get("error") or (status is not None and status >= 400):
            skipped["error"] += 1
            continue
        key = dedupe_key(r["url"])
        if key in seen:
            continue
        seen.add(key)
        testable.append(normalize_page_url(r["url"]))

    truncated = bool(browser.get("truncated")) or len(testable) > limit
    pages = testable[:limit] or [normalize_page_url(base)]
    return {
        "ok": True,
        "base": normalize_page_url(browser.get("start") or base),
        "pages": pages,
        "forms": browser.get("forms", 0),
        "status": (records[0].get("status") if records else None),
        "method": "browser",
        "findings": browser.get("findings", []),
        "skipped": skipped,
        "truncated": truncated,
        "discovered": browser.get("discovered", len(records)),
    }


def build_plan(discovery: dict) -> dict:
    """A test plan from the discovered structure. Deterministic; a model may enrich.

    Surfaces what the crawl couldn't test (auth walls, errors, off-site links) and
    whether it truncated as ``notes``, since a silent cap reads as "we covered
    everything" when we didn't.
    """
    pages = discovery["pages"]
    forms = discovery.get("forms", 0)
    skipped = discovery.get("skipped", {})
    functional = [
        f"Each of the {len(pages)} page(s) loads and renders (no 4xx/5xx)",
        "Primary navigation links resolve",
    ]
    if forms:
        functional.append(f"The {forms} form(s) on the entry page accept input and submit")
    non_functional = [
        "No uncaught JavaScript / console errors",
        "No 5xx responses while loading",
        "Pages render within a reasonable budget",
    ]
    notes: list[str] = []
    if skipped.get("auth"):
        notes.append(f"{skipped['auth']} auth-gated page(s) skipped (login required)")
    if skipped.get("error"):
        notes.append(f"{skipped['error']} page(s) returned an error and were skipped")
    if skipped.get("offsite"):
        notes.append(f"{skipped['offsite']} link(s) redirect off-site and were skipped")
    if discovery.get("truncated"):
        notes.append(f"more pages were found than tested, covering the first {len(pages)}")
    return {
        "target": discovery["base"],
        "pages": pages,
        "functional": functional,
        "non_functional": non_functional,
        "test_count": len(pages),
        "notes": notes,
        "method": discovery.get("method", "http"),
    }


async def enrich_plan_with_model(plan: dict, provider) -> dict:
    """If a model is configured, let it sharpen the functional/non-functional split
    and priorities. Falls back silently to the deterministic plan on any error."""
    if provider is None:
        return plan
    try:
        from ..ai.providers.base import Message, Role
        prompt = (
            "You are a principal SDET. Given a website and its pages, produce a concise "
            "test plan. Return JSON with keys 'functional' (list of strings) and "
            "'non_functional' (list of strings), each a specific, testable check. "
            f"Site: {plan['target']}\nPages:\n" + "\n".join(f"- {p}" for p in plan["pages"])
        )
        reply = await provider.complete([Message(role=Role.USER, content=prompt)])
        import json
        text = reply.text if hasattr(reply, "text") else str(reply)
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            data = json.loads(match.group(0))
            if isinstance(data.get("functional"), list) and data["functional"]:
                plan = {**plan, "functional": [str(x) for x in data["functional"]][:6]}
            if isinstance(data.get("non_functional"), list) and data["non_functional"]:
                plan = {**plan, "non_functional": [str(x) for x in data["non_functional"]][:6]}
            plan = {**plan, "enriched": True}
    except Exception:  # noqa: BLE001 (the deterministic plan is always a valid fallback)
        pass
    return plan


# --- pending plan on the conversation (approve-in-chat gate) ---------------- #
def stash_plan(session, plan: dict) -> None:
    context = dict(session.context or {})
    context[PLAN_KEY] = plan
    session.context = context


def pending_plan(session) -> dict | None:
    return (session.context or {}).get(PLAN_KEY)


def clear_plan(session) -> None:
    if session.context and PLAN_KEY in session.context:
        context = dict(session.context)
        context.pop(PLAN_KEY, None)
        session.context = context


# --- execute the approved plan --------------------------------------------- #
async def run_website_test(
    db, *, project_id: str, plan: dict, triggered_by: str | None = None, timeout: float = 240.0,
) -> dict:
    """Turn the approved plan into one test per page and run them together."""
    import asyncio
    from urllib.parse import urlparse

    from sqlalchemy import select

    from ..models import (
        Project,
        Run,
        StepAction,
        TestCase,
        TestCategory,
        TestStatus,
        TestStep,
    )
    from .onramp import TARGET_ENV, set_target
    from .runs import run_task, start_run

    project = db.get(Project, project_id)
    if project is None:
        return {"ok": False, "error": "unknown project"}
    target = set_target(db, project, plan["target"])

    keys: list[str] = []
    for i, page_url in enumerate(plan["pages"]):
        key = f"{project.key}-WEB-{i:02d}"
        path = urlparse(page_url).path or "/"
        existing = db.execute(
            select(TestCase).where(TestCase.project_id == project_id, TestCase.key == key)
        ).scalar_one_or_none()
        if existing is not None:
            # keep it pointed at the current page and reuse it
            existing.title = f"Page loads: {path}"
            for s in list(existing.steps):
                db.delete(s)
            db.flush()
            tc = existing
        else:
            tc = TestCase(
                project_id=project_id, key=key, title=f"Page loads: {path}",
                status=TestStatus.APPROVED, category=TestCategory.AUTOMATED,
                tags=["website", "builtin"],
                provenance={"origin": "website_test", "deterministic": True},
            )
            db.add(tc)
            db.flush()
        db.add(TestStep(test_case_id=tc.id, index=0, action=StepAction.GOTO,
                        intent=f"Open {path}", value={"url": page_url}))
        db.add(TestStep(test_case_id=tc.id, index=1, action=StepAction.EXPECT_VISIBLE,
                        intent="The page renders", target={"ladder": [{"kind": "css", "value": "body"}]}))
        keys.append(key)
    db.commit()

    run = await start_run(
        db, project_id=project_id, selection={"keys": keys}, environment=TARGET_ENV,
        trigger="website_test", triggered_by=triggered_by, title=f"Website test: {target}",
    )
    task = run_task(run.id)
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            return {"ok": True, "status": "running", "target": target,
                    "run_id": run.id, "run_number": run.number, "timed_out": True}

    db.expire_all()
    run = db.get(Run, run.id)
    return _report_run(db, run, target)


async def run_approved_keys(
    db, *, project_id: str, keys: list[str], target: str,
    triggered_by: str | None = None, timeout: float = 240.0,
) -> dict:
    """Run an already-approved set of tests (the built Golden Path) and report: the
    execution half of the chat 'approve' flow, after the plan gate has been decided."""
    import asyncio

    from ..models import Project, Run
    from .onramp import TARGET_ENV, set_target
    from .runs import run_task, start_run

    project = db.get(Project, project_id)
    if project is not None:
        set_target(db, project, target)
    run = await start_run(
        db, project_id=project_id, selection={"keys": keys}, environment=TARGET_ENV,
        trigger="golden_path", triggered_by=triggered_by, title=f"Golden Path: {target}",
    )
    task = run_task(run.id)
    if task is not None:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            return {"ok": True, "status": "running", "target": target,
                    "run_id": run.id, "run_number": run.number, "timed_out": True}
    db.expire_all()
    run = db.get(Run, run.id)
    return _report_run(db, run, target)


def _report_run(db, run, target: str) -> dict:
    from sqlalchemy import select

    from ..models import RunTest
    from ..models.testing import RunStatus

    results = list(db.execute(select(RunTest).where(RunTest.run_id == run.id)).scalars())
    totals = run.totals or {}
    passed = totals.get("passed", 0)
    failed = totals.get("failed", 0) + totals.get("error", 0)
    ok = run.status in {RunStatus.PASSED, RunStatus.FLAKY}
    pages = [{"title": r.title, "status": r.status,
              "error": (r.error_message or "")[:160]} for r in results]
    if ok:
        summary = f"Tested {len(results)} page(s) on {target}: all passed."
    else:
        summary = (f"Tested {len(results)} page(s) on {target}: {passed} passed, "
                   f"{failed} failed.")
    return {"ok": ok, "status": run.status, "target": target,
            "passed": passed, "failed": failed, "pages": pages,
            "run_id": run.id, "run_number": run.number, "summary": summary}
