"""The terminal surface: a token-cheap CLI for coding agents.

Every report and list is reachable from the shell; every command that produces a
document writes it to ``--out`` and prints only the path plus a one-line summary,
never the whole document. These tests drive the real Typer app through
``CliRunner``; the commands open their own session on the shared test DB, so the
seed below is committed before it is queried.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from galeqea.cli import app

runner = CliRunner()


@pytest.fixture()
def seeded_run(db, project):
    """A project with one run and a few RunTest rows, committed so the CLI's own
    session sees it. Returns the run id."""
    from galeqea.db import session_scope
    from galeqea.models import Run, RunTest

    with session_scope() as s:
        run = Run(project_id=project.id, number=1, title="Smoke", status="failed",
                  environment="staging", base_url="https://staging.example.com",
                  duration_ms=2200, totals={"total": 3, "passed": 2, "failed": 1})
        s.add(run)
        s.flush()
        s.add_all([
            RunTest(run_id=run.id, test_case_id="", test_key="DEMO-T-01",
                    title="home loads", status="passed", browser="chromium", duration_ms=400),
            RunTest(run_id=run.id, test_case_id="", test_key="DEMO-T-02",
                    title="search works", status="passed", browser="chromium", duration_ms=600),
            RunTest(run_id=run.id, test_case_id="", test_key="DEMO-T-03",
                    title="checkout", status="failed", browser="chromium", duration_ms=1200,
                    error_message="Timeout waiting for #pay", error_type="TimeoutError"),
        ])
        s.commit()
        run_id = run.id
    return run_id


def test_runs_report_json_writes_file_and_prints_only_the_path(tmp_path, project, seeded_run):
    out = tmp_path / "r.json"
    result = runner.invoke(app, ["runs", "report", seeded_run,
                                 "--project", project.id, "--format", "json", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.exists()
    assert str(out) in result.output           # the path is printed
    assert "run #1 report" in result.output    # a one-line summary, not the document
    data = json.loads(out.read_text())
    assert data["report"] == "run"


def test_runs_report_junit_writes_valid_looking_xml(tmp_path, project, seeded_run):
    out = tmp_path / "r.xml"
    result = runner.invoke(app, ["runs", "report", seeded_run,
                                 "--project", project.id, "--format", "junit", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text().startswith("<?xml")


def test_runs_report_rejects_an_unknown_format(project, seeded_run):
    result = runner.invoke(app, ["runs", "report", seeded_run,
                                 "--project", project.id, "--format", "xml"])
    assert result.exit_code == 2


def test_context_prints_a_markdown_h1(project, seeded_run):
    result = runner.invoke(app, ["context", "--project", project.id])
    assert result.exit_code == 0, result.output
    assert "# " in result.output


def test_context_out_prints_only_path_and_summary(tmp_path, project, seeded_run):
    out = tmp_path / "ctx.md"
    result = runner.invoke(app, ["context", "--project", project.id, "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert out.read_text().startswith("# ")
    assert "project briefing" in result.output
    assert "# " not in result.output  # the document itself is not echoed


def test_coverage_markdown(project, seeded_run):
    result = runner.invoke(app, ["coverage", "--project", project.id, "--format", "md"])
    assert result.exit_code == 0, result.output


def test_coverage_rejects_junit(project, seeded_run):
    result = runner.invoke(app, ["coverage", "--project", project.id, "--format", "junit"])
    assert result.exit_code == 2


def test_traceability_and_flaky_json(project, seeded_run):
    for cmd in ("traceability", "flaky"):
        result = runner.invoke(app, [cmd, "--project", project.id])
        assert result.exit_code == 0, result.output


def test_rca_by_run_number(project, seeded_run):
    result = runner.invoke(app, ["rca", "1", "--project", project.id])
    assert result.exit_code == 0, result.output


def test_runs_list_and_get(project, seeded_run):
    listed = runner.invoke(app, ["runs", "list", "--project", project.id])
    assert listed.exit_code == 0, listed.output
    assert "#1" in listed.output

    got = runner.invoke(app, ["runs", "get", "1", "--project", project.id])
    assert got.exit_code == 0, got.output
    assert "DEMO-T-03" in got.output  # the one failed test key


def test_tests_list(project, seeded_run):
    result = runner.invoke(app, ["tests", "list", "--project", project.id])
    assert result.exit_code == 0, result.output


def test_approvals_list(project, seeded_run):
    result = runner.invoke(app, ["approvals", "list", "--project", project.id])
    assert result.exit_code == 0, result.output


def test_schedule_list(project, seeded_run):
    result = runner.invoke(app, ["schedule", "list", "--project", project.id])
    assert result.exit_code == 0, result.output


def test_no_project_found_exits_2(db):
    """With no resolvable project, the command exits 2 with a clear message.

    A bare, non-empty identifier that matches nothing resolves to None even if
    other projects exist, so this holds regardless of test ordering."""
    result = runner.invoke(app, ["context", "--project", "NOPE-DOES-NOT-EXIST"])
    assert result.exit_code == 2
    assert "No project found" in result.output
