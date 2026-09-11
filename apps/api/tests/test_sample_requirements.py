"""The bundled sample requirements template: download it, or use it to drive
the whole floor (ingest -> generate -> plan -> run -> report) with one click,
so a new workspace can see the full pipeline work before writing a spec."""

from __future__ import annotations

from fastapi.testclient import TestClient

from galeqea.config import settings
from galeqea.main import app

# --- the template document itself ------------------------------------------ #

def test_template_asset_parses_into_clean_requirements():
    from galeqea.api.routes.requirements import _TEMPLATE_PATH
    from galeqea.engine import ingest

    assert _TEMPLATE_PATH.exists()
    data = _TEMPLATE_PATH.read_bytes()
    extracted = ingest.extract(data, _TEMPLATE_PATH.name, "text/markdown")
    reqs = ingest.split_requirements(extracted.text)

    refs = {r.ref for r in reqs}
    for expect in ("REQ-101", "REQ-110", "NFR-201", "NFR-205", "BR-301", "BR-302"):
        assert expect in refs

    # Regression guard: a "**REQ-101 - Title.**" heading must not leave a
    # dangling dash/space once the ref is stripped (engine/ingest.py _title_of).
    for r in reqs:
        assert not r.title.startswith(("-", "–", "—", " ")), (r.ref, r.title)

    explicit = [r for r in reqs if not r.inferred_ref]
    assert len(explicit) >= 17  # 10 REQ + 5 NFR + 2 BR, at minimum
    assert any(r.acceptance_criteria for r in explicit)
    assert any(r.open_questions for r in explicit)      # ambiguous wording flagged


# --- the routes -------------------------------------------------------------- #

def test_download_template_route(project, monkeypatch):
    monkeypatch.setattr(settings, "single_user_mode", True)
    c = TestClient(app)
    from galeqea.api.routes.requirements import _TEMPLATE_PATH

    r = c.get(f"/api/projects/{project.id}/requirements/template")
    assert r.status_code == 200
    assert "text/markdown" in r.headers["content-type"]
    assert "attachment" in r.headers["content-disposition"]
    assert r.content == _TEMPLATE_PATH.read_bytes()


def test_use_sample_without_run_ingests_and_generates(project, monkeypatch):
    monkeypatch.setattr(settings, "single_user_mode", True)
    c = TestClient(app)

    r = c.post(f"/api/projects/{project.id}/requirements/sample", json={"run": False})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["doc"]["title"].startswith("Sample Requirements")
    assert len(body["requirements"]) >= 17
    assert body["generated"]["created"] > 0   # deterministic scaffold needs no model


async def test_use_sample_with_run_drives_the_full_floor(project, monkeypatch):
    monkeypatch.setattr(settings, "single_user_mode", True)

    def fake_discover(url, limit=8, timeout=90.0):
        from galeqea.services.onramp import normalize_url
        base = normalize_url(url)
        return {"ok": True, "base": base,
                "pages": [base, base + "/about", base + "/contact"],
                "forms": 1, "status": 200, "method": "http"}
    monkeypatch.setattr("galeqea.services.website_test.discover_pages", fake_discover)

    async def fake_start_run(db, *, project_id, selection, **kw):
        from galeqea.models import Run, RunStatus
        run = Run(project_id=project_id, number=1, title=kw.get("title", ""),
                  base_url=kw.get("base_url", ""), trigger=kw.get("trigger", ""),
                  status=RunStatus.PASSED, totals={})
        db.add(run)
        db.flush()
        return run

    async def fake_wait_for_run(run_id, *, timeout=120.0):
        return None

    monkeypatch.setattr("galeqea.services.runs.start_run", fake_start_run)
    monkeypatch.setattr("galeqea.services.runs.wait_for_run", fake_wait_for_run)

    c = TestClient(app)
    r = c.post(f"/api/projects/{project.id}/requirements/sample",
              json={"run": True, "target": "https://a.example.com"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["requirements"]["cases_generated"] > 0
    assert body["requirements"]["doc_title"].startswith("Sample Requirements")
    for key in ("docx", "xlsx", "html_rtm", "xlsx_rtm", "json", "test_plan_docx"):
        assert key in body["reports"]


# --- discoverable from chat -------------------------------------------------- #

def test_page_chat_points_to_the_sample_on_the_requirements_page(db, project, humans):
    from galeqea.services import page_chat

    out = page_chat.try_handle(db, project, "is there a sample requirements doc?",
                               humans["author"], "/requirements")
    assert out is not None
    text, blocks = out
    assert "sample" in text.lower() and "template" in text.lower()
    assert blocks[0]["action"] == "sample_template"

    # Scoped to the requirements page - the same phrase elsewhere is not intercepted.
    assert page_chat.try_handle(db, project, "is there a sample requirements doc?",
                                humans["author"], "/tests") is None


def test_template_route_404s_when_the_asset_is_missing(project, monkeypatch):
    import galeqea.api.routes.requirements as route_mod
    monkeypatch.setattr(settings, "single_user_mode", True)
    monkeypatch.setattr(route_mod, "_TEMPLATE_PATH", route_mod._TEMPLATE_PATH.with_name("nope.md"))
    c = TestClient(app)
    r = c.get(f"/api/projects/{project.id}/requirements/template")
    assert r.status_code == 404
