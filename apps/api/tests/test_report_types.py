"""The run report groups by golden-path *type* (not category), names each row by
key · unit · label, shows unrun tests as skipped, and blocks on critical types."""

from __future__ import annotations

from galeqea.models import Run, RunTest, TestCase, TestCategory, TestStatus
from galeqea.reports.runs import build_run_report, run_report_junit, run_report_markdown
from galeqea.services.test_plan import golden_path_type


def _gp_case(db, project, key, type_key, unit):
    tc = TestCase(project_id=project.id, key=key, title=f"{type_key}: {unit}",
                  status=TestStatus.APPROVED, category=TestCategory.AUTOMATED,
                  tags=["golden-path", type_key],
                  provenance={"origin": "golden_path", "type": type_key, "unit": unit})
    db.add(tc)
    db.flush()
    return tc


def test_golden_path_type_resolves_provenance_then_tag_then_key():
    class C:
        provenance = {"type": "a11y"}
        tags = ["golden-path", "a11y"]
        key = "P-GP-A11Y-01"
        category = "automated"
    assert golden_path_type(C()) == "a11y"
    assert golden_path_type(None, "P-GP-SECURITY-01") == "security"
    assert golden_path_type(None, "P-GP-CROSSBROWSER-02") == "cross_browser"


def _run_with_gp_results(db, project):
    a1 = _gp_case(db, project, "P-GP-A11Y-01", "a11y", "/login")
    f1 = _gp_case(db, project, "P-GP-FUNCTIONAL-01", "functional", "/")
    quarantined = _gp_case(db, project, "P-GP-A11Y-02", "a11y", "/abtest")
    quarantined.quarantined = True
    run = Run(project_id=project.id, number=1, title="Golden Path", status="failed",
              environment="production", base_url="https://x.com",
              selection={"keys": ["P-GP-A11Y-01", "P-GP-FUNCTIONAL-01", "P-GP-A11Y-02"]},
              totals={"total": 2, "passed": 1, "failed": 1})
    db.add(run)
    db.flush()
    db.add_all([
        RunTest(run_id=run.id, test_case_id=a1.id, test_key="P-GP-A11Y-01", title=a1.title,
                status="failed", error_type="A11yError", error_message="3 violations, allowed 0"),
        RunTest(run_id=run.id, test_case_id=f1.id, test_key="P-GP-FUNCTIONAL-01", title=f1.title,
                status="passed", duration_ms=200),
    ])
    db.commit()
    return run


def test_report_groups_by_type_not_category(db, project):
    run = _run_with_gp_results(db, project)
    report = build_run_report(db, project, run)
    assert set(report["by_test_type"]) == {"a11y", "functional"}  # not one "functional" bucket
    assert report["by_test_type"]["a11y"]["failed"] == 1
    assert report["by_test_type"]["functional"]["passed"] == 1


def test_report_rows_name_key_unit_label_and_show_skipped(db, project):
    run = _run_with_gp_results(db, project)
    report = build_run_report(db, project, run)
    md = run_report_markdown(report)
    assert "P-GP-A11Y-01 · /login · Accessibility" in md
    # The quarantined, unrun test is a visible skipped row, not silently missing.
    skipped = [it for it in report["results"] if it["status"] == "skipped"]
    assert any(it["key"] == "P-GP-A11Y-02" and it["skip_reason"] == "quarantined"
               for it in skipped)
    assert report["summary"]["skipped"] == 1
    assert report["summary"]["total"] == 3  # 2 ran + 1 skipped, all counted


def test_report_blocks_release_on_a_critical_type(db, project):
    run = _run_with_gp_results(db, project)
    report = build_run_report(db, project, run)
    # An accessibility failure is a hard block, not a soft "review".
    assert report["readiness"]["verdict"] == "not_ready"
    assert "accessibility" in report["readiness"]["reason"]
    assert "a11y" in report["readiness"].get("critical_types", [])


def test_junit_classname_is_the_type(db, project):
    run = _run_with_gp_results(db, project)
    report = build_run_report(db, project, run)
    xml = run_report_junit(report)
    assert 'classname="a11y"' in xml
    assert 'classname="functional"' in xml
    assert 'name="P-GP-A11Y-01 · /login · Accessibility"' in xml
