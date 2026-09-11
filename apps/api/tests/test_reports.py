"""Reports in three formats: JSON (schema-valid), Markdown (LLM-friendly), JUnit.

The JSON is validated against its published schema in docs/schemas/, and the run's
JUnit XML against the bundled JUnit XSD, so "AI-readable" is a checked contract, not
a hope. Endpoints are exercised through the real app so the extension-selected URLs
return the right content types.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from galeqea.models import Run, RunTest
from galeqea.reports import runs as run_report

_SCHEMAS = Path(__file__).resolve().parents[3] / "docs" / "schemas"


def _load_schema(name: str) -> dict:
    return json.loads((_SCHEMAS / name).read_text())


@pytest.fixture()
def run_with_results(db, project):
    run = Run(project_id=project.id, number=1, title="Smoke", status="failed",
              environment="staging", base_url="https://staging.example.com",
              git_sha="abc1234def", duration_ms=2200,
              totals={"total": 3, "passed": 2, "failed": 1})
    db.add(run)
    db.flush()
    db.add_all([
        RunTest(run_id=run.id, test_case_id="", test_key="DEMO-T-01", title="home loads",
                status="passed", browser="chromium", duration_ms=400),
        RunTest(run_id=run.id, test_case_id="", test_key="DEMO-T-02", title="search works",
                status="passed", browser="chromium", duration_ms=600),
        RunTest(run_id=run.id, test_case_id="", test_key="DEMO-T-03", title="checkout",
                status="failed", browser="chromium", duration_ms=1200,
                error_message="Timeout waiting for #pay", error_type="TimeoutError"),
    ])
    db.commit()
    return run


# --------------------------------------------------------------------------- #
# Run report: JSON schema, JUnit XSD, Markdown
# --------------------------------------------------------------------------- #
def test_run_report_json_matches_schema(db, project, run_with_results):
    jsonschema = pytest.importorskip("jsonschema")
    from galeqea.reports import report_v2
    # v2 is the canonical report: the endpoints, CLI, MCP and share all render it.
    report = report_v2.build(db, project, run_with_results)
    jsonschema.validate(report, _load_schema("run-report.schema.json"))
    assert report["schema_version"] == "2.0"
    # The cost block proves the promise: a plain execution run spends no model, and
    # carries the step-cache hit rate (WO#8).
    c = report["cost"]
    assert c["model_calls"] == 0 and c["tokens_used"] == 0 and c["cost_estimate_usd"] == 0.0
    assert c["heals"] == 0 and c["cache_hits"] == 0 and c["cache_hit_rate"] is None
    assert report["readiness"]["verdict"] == "no_go"  # one test failed
    # The v2 sections are present…
    assert "exec_summary" in report and "since_last_run" in report and "coverage" in report
    # …and every executed result still carries the traversal fields.
    for item in report["results"]:
        if item["id"]:  # skipped rows have no id
            assert item["href"] and item["ui_href"]


def test_run_report_junit_validates_against_xsd(db, project, run_with_results):
    xmlschema = pytest.importorskip("xmlschema")
    report = run_report.build_run_report(db, project, run_with_results)
    xml = run_report.run_report_junit(report)
    schema = xmlschema.XMLSchema(str(_SCHEMAS / "junit.xsd"))
    schema.validate(xml)  # raises if invalid
    assert '<testsuites' in xml and '<failure' in xml


def test_run_report_markdown_has_summary_and_is_bounded(db, project, run_with_results):
    report = run_report.build_run_report(db, project, run_with_results)
    md = run_report.run_report_markdown(report)
    assert md.startswith("# Run #1")
    assert "Readiness" in md and "Model cost" in md
    assert len(md) // 4 <= 4200  # within the token budget (+ small slack)


def test_run_report_is_deterministic(db, project, run_with_results):
    a = run_report.build_run_report(db, project, run_with_results)
    b = run_report.build_run_report(db, project, run_with_results)
    assert a == b


# --------------------------------------------------------------------------- #
# The other five reports validate against their published schemas
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("kind, schema", [
    ("coverage", "coverage-report.schema.json"),
    ("traceability", "traceability-report.schema.json"),
    ("flaky", "flaky-report.schema.json"),
    ("heals", "heals-report.schema.json"),
])
def test_project_report_json_matches_schema(db, project, kind, schema):
    jsonschema = pytest.importorskip("jsonschema")
    from galeqea.reports import coverage, flaky, heals, traceability

    builders = {
        "coverage": coverage.build_coverage_report,
        "traceability": traceability.build_traceability_report,
        "flaky": flaky.build_flaky_report,
        "heals": heals.build_heals_report,
    }
    report = builders[kind](db, project)
    jsonschema.validate(report, _load_schema(schema))
    assert report["report"] == kind


def test_rca_report_json_matches_schema(db, project, run_with_results):
    jsonschema = pytest.importorskip("jsonschema")
    from galeqea.reports import rca

    report = rca.build_rca_report(db, project, run_with_results)
    jsonschema.validate(report, _load_schema("rca-report.schema.json"))
    assert report["available"] is False  # no RCA generated for this run


# --------------------------------------------------------------------------- #
# Endpoints: extension-selected content types, through the real app
# --------------------------------------------------------------------------- #
def test_report_endpoints_return_the_right_content_types(db, project, run_with_results):
    from fastapi.testclient import TestClient

    from galeqea.main import app

    client = TestClient(app)
    base = f"/api/projects/{project.id}"

    r = client.get(f"{base}/runs/{run_with_results.id}/report.json")
    assert r.status_code == 200 and r.json()["report"] == "run"

    r = client.get(f"{base}/runs/{run_with_results.id}/report.md")
    assert r.status_code == 200 and "text/markdown" in r.headers["content-type"]

    r = client.get(f"{base}/runs/{run_with_results.id}/report.junit.xml")
    assert r.status_code == 200 and "xml" in r.headers["content-type"]
    assert r.text.startswith("<?xml")

    for kind in ("coverage", "traceability", "flaky", "heals"):
        r = client.get(f"{base}/{kind}/report.json")
        assert r.status_code == 200, f"{kind} json"
        assert r.json()["report"] == kind
        r = client.get(f"{base}/{kind}/report.md")
        assert r.status_code == 200 and "text/markdown" in r.headers["content-type"]

    r = client.get(f"{base}/runs/{run_with_results.id}/rca/report.json")
    assert r.status_code == 200 and r.json()["report"] == "rca"


def test_unknown_api_path_404s_and_is_not_shadowed_by_the_spa():
    """An unknown /api/... path must 404, never the SPA index.html, which the
    caller would then try to parse as JSON."""
    from fastapi.testclient import TestClient

    from galeqea.main import app

    client = TestClient(app)
    r = client.get("/api/does/not/exist")
    assert r.status_code == 404
    assert "<!doctype html" not in r.text.lower() and "<html" not in r.text.lower()
    # A non-API unknown path still serves the SPA shell (client-side routing).
    spa = client.get("/some/client/route")
    assert spa.status_code == 200


# --------------------------------------------------------------------------- #
# Release report: JSON schema (the release view of a milestone)
# --------------------------------------------------------------------------- #
def test_release_report_json_matches_schema(db, project):
    jsonschema = pytest.importorskip("jsonschema")
    from galeqea.models import Milestone, MilestoneStatus
    from galeqea.reports import release_report

    m = Milestone(project_id=project.id, name="Release 1.4", version="1.4",
                  status=MilestoneStatus.ACTIVE,
                  exit_criteria=[{"metric": "pass_rate", "op": ">=", "value": 0.95},
                                 {"metric": "open_blockers", "op": "==", "value": 0}])
    db.add(m)
    db.commit()

    report = release_report.build(db, m)
    jsonschema.validate(report, _load_schema("release-report.schema.json"))
    assert report["schema_version"] == "1.0"
    assert report["readiness"]["verdict"] in ("go", "no_go")
    assert report["milestone"]["version"] == "1.4"


def test_release_report_html_is_a_styled_dashboard(db, project):
    """to_html() used to be a plain <pre> dump of the Markdown; it's now a real
    dashboard (Go/No-Go badge, a pass-rate doughnut, KPI cards) sharing the same
    self-contained-SVG technique as report_v2 and the traceability report."""
    from galeqea.models import Milestone, MilestoneStatus
    from galeqea.reports import release_report

    m = Milestone(project_id=project.id, name="Release 1.4", version="1.4",
                  status=MilestoneStatus.ACTIVE,
                  exit_criteria=[{"metric": "pass_rate", "op": ">=", "value": 0.95}])
    db.add(m)
    db.commit()

    doc = release_report.to_html(db, m)
    assert doc.startswith("<!doctype html>")
    # No runs yet against this milestone, so the doughnut correctly falls back to
    # a neutral "no data" ring (no stroke-dasharray segments) rather than a
    # misleading colored one. Assert the ring itself, not a segment that
    # shouldn't exist for zero tests.
    assert "<svg" in doc and 'stroke="#3a3f4b"' in doc
    assert "GO" in doc or "NO-GO" in doc
    assert "<pre" not in doc  # not the old plain-text dump
    assert "http://" not in doc.split("<style>")[0]


def test_release_report_html_donut_has_segments_when_there_are_results(db, project):
    """The non-empty case of the same doughnut: real pass/fail data produces real
    stroke-dasharray segments, not just the neutral ring."""
    from galeqea.models import Milestone, MilestoneStatus, Run, RunStatus, RunTest
    from galeqea.reports import release_report

    m = Milestone(project_id=project.id, name="Release 1.4", version="1.4",
                  status=MilestoneStatus.ACTIVE,
                  exit_criteria=[{"metric": "pass_rate", "op": ">=", "value": 0.95}])
    db.add(m)
    db.flush()  # m.id is assigned on flush; Run needs the real value, not None
    run = Run(project_id=project.id, number=1, title="r", status=RunStatus.FAILED,
              milestone_id=m.id)
    db.add(run)
    db.flush()
    db.add(RunTest(run_id=run.id, test_key="EX-01", title="t", status="passed",
                   test_case_id="", duration_ms=100))
    db.commit()

    doc = release_report.to_html(db, m)
    assert "stroke-dasharray" in doc
