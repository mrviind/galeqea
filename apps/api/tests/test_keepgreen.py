"""Keep-green: schedule + CI workflow for an ongoing guarantee."""

from __future__ import annotations

from sqlalchemy import select

from galeqea.models import Schedule, TestCase, TestCategory, TestStatus
from galeqea.services import journeys, keepgreen


def _journey(db, project):
    j = journeys.start(db, project.id, "https://shop.example.com")
    j.test_ids = ["P-GP-A11Y-01"]
    db.add(TestCase(project_id=project.id, key="P-GP-A11Y-01", title="a11y",
                    status=TestStatus.APPROVED, category=TestCategory.AUTOMATED))
    db.flush()
    return j


def test_setup_schedule_creates_then_updates(db, project):
    j = _journey(db, project)
    first = keepgreen.setup_schedule(db, project, j, cron="0 8 * * 1-5")
    assert first["tests"] == 1 and "shop.example.com" in first["name"]
    rows = db.execute(select(Schedule).where(Schedule.project_id == project.id)).scalars().all()
    assert len(rows) == 1 and rows[0].selection == {"keys": ["P-GP-A11Y-01"]}
    # Re-running keep-green updates the existing schedule rather than duplicating.
    keepgreen.setup_schedule(db, project, j, cron="0 6 * * *")
    rows = db.execute(select(Schedule).where(Schedule.project_id == project.id)).scalars().all()
    assert len(rows) == 1 and rows[0].cron == "0 6 * * *"


def test_github_action_yaml_targets_the_url(db, project):
    j = _journey(db, project)
    yaml = keepgreen.github_action_yaml(project, j)
    assert "name: GaleQEA (shop.example.com)" in yaml
    assert "https://shop.example.com" in yaml
    assert "--fail-on no_go" in yaml           # the PR gate is the readiness verdict
    assert "cron:" in yaml and "pull_request" in yaml


def test_keep_green_bundles_schedule_and_workflow(db, project):
    j = _journey(db, project)
    result = keepgreen.keep_green(db, project, j)
    assert result["schedule"]["tests"] == 1
    assert "actions/checkout" in result["github_action"]
    assert isinstance(result["webhooks_active"], int)
