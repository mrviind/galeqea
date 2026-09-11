"""The share route publishes a PDF end to end (with a stubbed renderer) and the
public endpoint serves it, so the PDF is never silently dropped."""

from __future__ import annotations

from galeqea.models import Run, RunTest, TestCase, TestCategory, TestStatus
from galeqea.services import journeys
from galeqea.services.storage import reset_storage


def _setup(db, project):
    j = journeys.start(db, project.id, "https://x.com")
    j.plan = {"typed": {"totals": {"test_count": 1}}}
    j.test_ids = ["P-GP-FUNCTIONAL-01"]
    j.discovery = {"base": "https://x.com", "pages": ["https://x.com/"]}
    db.add(TestCase(project_id=project.id, key="P-GP-FUNCTIONAL-01", title="functional: /",
                    status=TestStatus.APPROVED, category=TestCategory.AUTOMATED,
                    provenance={"type": "functional", "unit": "/"}))
    run = Run(project_id=project.id, number=1, title="Golden Path", status="passed",
              trigger="golden_path", environment="production", base_url="https://x.com",
              selection={"keys": ["P-GP-FUNCTIONAL-01"]}, totals={"passed": 1})
    db.add(run)
    db.flush()
    db.add(RunTest(run_id=run.id, test_case_id="", test_key="P-GP-FUNCTIONAL-01",
                   title="functional: /", status="passed"))
    db.commit()
    return run


def test_share_route_publishes_and_serves_a_pdf(db, project, monkeypatch):
    from fastapi.testclient import TestClient

    from galeqea.main import app

    run = _setup(db, project)
    reset_storage()

    # Stub the runner-backed renderer so the test needs no browser.
    async def fake_render_pdf(self, html):
        return b"%PDF-1.4 stub", None
    monkeypatch.setattr("galeqea.engine.supervisor.RunSupervisor.render_pdf", fake_render_pdf)

    client = TestClient(app)
    r = client.post(f"/api/projects/{project.id}/runs/{run.id}/share",
                    json={"public": True, "stakeholder": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "pdf" in body["formats"]
    assert body["pdf_status"] == "ready"

    token = body["token"]
    got = client.get(f"/api/shared/shares/{token}/report.pdf")
    assert got.status_code == 200
    assert got.headers["content-type"].startswith("application/pdf")
    assert got.content == b"%PDF-1.4 stub"


def test_share_route_reports_pdf_failure_never_silently(db, project, monkeypatch):
    from fastapi.testclient import TestClient

    from galeqea.main import app

    run = _setup(db, project)
    reset_storage()

    async def failing_render_pdf(self, html):
        return None, "chromium crashed"
    monkeypatch.setattr("galeqea.engine.supervisor.RunSupervisor.render_pdf", failing_render_pdf)

    client = TestClient(app)
    body = client.post(f"/api/projects/{project.id}/runs/{run.id}/share",
                       json={"public": True, "stakeholder": True}).json()
    # The PDF is omitted, but the reason is surfaced, never a silent drop.
    assert "pdf" not in body["formats"]
    assert body["pdf_status"] == "failed (chromium crashed)"
    # ...and the other formats still published.
    assert {"md", "html", "json", "junit"} <= set(body["formats"])
