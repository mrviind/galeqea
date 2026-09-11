"""Report v2 + storage + share: the release report and its shareable artifacts."""

from __future__ import annotations

import pytest

from galeqea.models import Run, RunTest, TestCase, TestCategory, TestStatus
from galeqea.reports import report_v2
from galeqea.services import journeys, share
from galeqea.services.storage import LocalStorage, get_storage, reset_storage


def _journey_run(db, project, a11y_status="failed"):
    j = journeys.start(db, project.id, "https://x.com")
    j.plan = {"typed": {"totals": {"test_count": 3}}}
    j.test_ids = ["P-GP-A11Y-01", "P-GP-FUNCTIONAL-01"]
    j.discovery = {"base": "https://x.com",
                   "pages": ["https://x.com/", "https://x.com/login", "https://x.com/pricing"]}
    cases = {}
    for key, unit in [("P-GP-A11Y-01", "/login"), ("P-GP-FUNCTIONAL-01", "/")]:
        tc = TestCase(project_id=project.id, key=key, title=f"{key}: {unit}",
                      status=TestStatus.APPROVED, category=TestCategory.AUTOMATED,
                      provenance={"type": key.split("-GP-")[1].split("-")[0].lower(), "unit": unit})
        db.add(tc)
        db.flush()
        cases[key] = tc
    run = Run(project_id=project.id, number=2, title="Golden Path", status="failed",
              trigger="golden_path", environment="production", base_url="https://x.com",
              selection={"keys": j.test_ids}, totals={"passed": 1, "failed": 1})
    db.add(run)
    db.flush()
    db.add_all([
        RunTest(run_id=run.id, test_case_id=cases["P-GP-A11Y-01"].id, test_key="P-GP-A11Y-01",
                title="P-GP-A11Y-01: /login", status=a11y_status,
                error_message="2 accessibility violation(s): 1 critical (label), 1 serious (color-contrast)"
                if a11y_status == "failed" else ""),
        RunTest(run_id=run.id, test_case_id=cases["P-GP-FUNCTIONAL-01"].id,
                test_key="P-GP-FUNCTIONAL-01", title="P-GP-FUNCTIONAL-01: /", status="passed"),
    ])
    db.commit()
    return j, run


def test_report_v2_has_exec_summary_coverage_and_a11y_impact(db, project):
    j, run = _journey_run(db, project)
    rep = report_v2.build(db, project, run, j)
    assert rep["exec_summary"]["verdict"] == "no_go"
    assert rep["exec_summary"]["headline_risks"]          # at least the a11y group
    assert rep["coverage"]["untested_pages"] == ["/pricing"]  # discovered but never tested
    assert rep["accessibility_by_impact"]["by_impact"]["critical"] == 1
    assert rep["accessibility_by_impact"]["by_impact"]["serious"] == 1
    assert any("accessibility" in a.lower() for a in rep["next_actions"])


def test_report_v2_markdown_is_one_document_one_h1(db, project):
    j, run = _journey_run(db, project)
    rep = report_v2.build(db, project, run, j)
    md = report_v2.to_markdown(rep)
    assert "Headline risks" in md and "Readiness" in md
    assert md.count("\n# ") + md.startswith("# ") == 1  # exactly one H1
    assert "## Detailed results" in md  # the v1 detail is a section now
    doc = report_v2.to_html(rep)
    assert doc.startswith("<!doctype html>") and "NO-GO" in doc
    assert "http://" not in doc.split("<style>")[0]  # self-contained head, no external assets


def test_report_v2_html_has_an_inline_results_svg_donut_and_type_bars(db, project):
    """The share page's results breakdown is an inline SVG doughnut (no chart
    library, no external asset, since it must still open offline / in stakeholder
    mode) plus a proportional mini-bar per test type."""
    j, run = _journey_run(db, project)
    rep = report_v2.build(db, project, run, j)
    doc = report_v2.to_html(rep)
    assert "<svg" in doc and "stroke-dasharray" in doc
    assert 'class="tbar"' in doc
    # still self-contained: the SVG itself references nothing external
    assert "http://" not in doc and "https://" not in doc.split('class="donut-row"')[1].split("</svg>")[0]


def test_stakeholder_mode_redacts_links_across_formats(db, project):
    j, run = _journey_run(db, project)
    # a failure whose message carries the full URL, as a real nav failure does.
    from galeqea.models import RunTest
    db.add(RunTest(run_id=run.id, test_case_id="", test_key="P-GP-SEO-01",
                   title="SEO: https://x.com/pricing", status="failed",
                   error_message="navigation to https://x.com/pricing returned HTTP 401"))
    db.commit()
    rep = report_v2.build(db, project, run, j, stakeholder=True)
    assert all(it["ui_href"] == "" and it["href"] == "" for it in rep["results"])
    md = report_v2.to_markdown(rep)
    assert "https://x.com" not in md and "http://x.com" not in md  # links → path only
    assert "/pricing" in md  # ...but the path is kept
    import json as _json
    assert "https://x.com" not in _json.dumps(rep)  # JSON redacted too


def test_local_storage_roundtrip(db, project, tmp_path):
    s = LocalStorage(tmp_path / "store", "http://host")
    s.put("shares/t/report.html", b"<html>", content_type="text/html")
    assert s.exists("shares/t/report.html")
    assert s.get("shares/t/report.html") == b"<html>"
    assert s.url("shares/t/report.html") == "http://host/api/shared/shares/t/report.html"
    # No traversal outside the base.
    s.put("../evil", b"x")
    assert (tmp_path / "store" / "evil").exists()


async def test_publish_and_fetch_share(db, project):
    reset_storage()
    j, run = _journey_run(db, project)
    result = await share.publish(db, project, j, run, formats=["md", "html", "json"],
                                 public=True, expires_days=30)
    assert set(result["urls"]) == {"md", "html", "json"}
    data, ctype = share.fetch(result["token"], "report.html")
    assert data.startswith(b"<!doctype html>") and "text/html" in ctype
    # JUnit wasn't published → not fetchable.
    with pytest.raises(FileNotFoundError):
        share.fetch(result["token"], "report.junit.xml")


async def test_private_and_expired_shares_are_refused(db, project):
    reset_storage()
    j, run = _journey_run(db, project)
    private = await share.publish(db, project, j, run, formats=["html"], public=False)
    with pytest.raises(PermissionError):
        share.fetch(private["token"], "report.html")
    live = await share.publish(db, project, j, run, formats=["html"], public=True, expires_days=1)
    # Far in the future → expired.
    with pytest.raises(FileNotFoundError):
        share.fetch(live["token"], "report.html", now_ts=9_999_999_999)
    _ = get_storage()  # backend resolves without error
