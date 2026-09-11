"""CI surface: the generated workflow's `galeqea` commands must actually exist,
and the CI gate commands must behave."""

from __future__ import annotations

import re

from typer.testing import CliRunner

from galeqea.cli import app
from galeqea.services import journeys, keepgreen

runner = CliRunner()


def _yaml_commands(yaml: str) -> set[tuple[str, ...]]:
    """The `galeqea <path>` command paths a workflow line invokes (stop at a flag,
    a variable, or a redirect)."""
    paths = set()
    for line in yaml.splitlines():
        for m in re.finditer(r"\bgaleqea ((?:[a-z][\w-]*\s+)*[a-z][\w-]*)", line):
            words = []
            for w in m.group(1).split():
                if w.startswith(("-", "$", '"')):
                    break
                words.append(w)
            if words:
                paths.add(tuple(words))
    return paths


def test_every_command_in_the_generated_yaml_exists(db, project):
    j = journeys.start(db, project.id, "https://x.com")
    yaml = keepgreen.github_action_yaml(project, j)
    paths = _yaml_commands(yaml)
    assert paths, "the YAML invokes galeqea"
    for path in paths:
        # `--help` on the exact command path succeeds only if it's a real command.
        result = runner.invoke(app, [*path, "--help"])
        assert result.exit_code == 0, f"`galeqea {' '.join(path)}` is not a CLI command"


def _approved_plan_for(db, project, target: str):
    """A journey for `target` with two approved, automated, journey-scoped tests."""
    from urllib.parse import urljoin

    from galeqea.models import TestCase, TestCategory, TestStatus
    j = journeys.start(db, project.id, target)
    keys = []
    for i, unit in enumerate(("/", "/pricing"), 1):
        key = f"{project.key}-GP-FUNCTIONAL-{i:02d}"
        db.add(TestCase(project_id=project.id, key=key, title=f"functional: {unit}",
                        status=TestStatus.APPROVED, category=TestCategory.AUTOMATED,
                        provenance={"type": "functional", "unit": unit,
                                    "page": urljoin(target + "/", unit.lstrip("/")),
                                    "journey_id": j.id}))
        keys.append(key)
    j.test_ids = keys
    db.commit()
    return j, keys


def test_test_command_refuses_without_an_approved_plan(db, project):
    # No journey / approved plan for this URL → exit 3, never auto-approve.
    result = runner.invoke(app, ["test", "https://never-approved.example.com",
                                 "--project", project.id, "--no-wait"])
    assert result.exit_code == 3
    assert "approved plan" in result.output.lower()


def test_approved_plan_for_A_does_not_make_B_runnable(db, project):
    from sqlalchemy import func, select

    from galeqea.cli import _ci_plan_keys
    from galeqea.models import Run
    _approved_plan_for(db, project, "https://a.example.com")

    # The selection helper is strictly target-scoped.
    _jb, keys_b = _ci_plan_keys(db, project, "https://b.example.com")
    assert keys_b == []
    _ja, keys_a = _ci_plan_keys(db, project, "https://a.example.com")
    assert len(keys_a) == 2  # only A's plan

    # And the CLI refuses B with exit 3 and creates ZERO runs.
    before = db.execute(select(func.count(Run.id)).where(Run.project_id == project.id)).scalar_one()
    result = runner.invoke(app, ["test", "https://b.example.com", "--project", project.id, "--no-wait"])
    assert result.exit_code == 3 and "no approved plan" in result.output.lower()
    after = db.execute(select(func.count(Run.id)).where(Run.project_id == project.id)).scalar_one()
    assert after == before  # no run created for a target with no approved plan


def test_test_selects_only_that_targets_same_origin_tests(db, project):
    from urllib.parse import urlparse

    from galeqea.cli import _ci_plan_keys
    from galeqea.models import TestCase
    _approved_plan_for(db, project, "https://a.example.com")
    _j, keys = _ci_plan_keys(db, project, "https://a.example.com")
    from sqlalchemy import select
    cases = {c.key: c for c in db.execute(
        select(TestCase).where(TestCase.key.in_(keys))).scalars()}
    for k in keys:
        page = (cases[k].provenance or {}).get("page", "")
        assert urlparse(page).netloc == "a.example.com"  # every test is same-origin with A


def test_ci_run_is_titled_and_based_on_the_target(db, project, monkeypatch):
    from galeqea.models import Run
    captured = {}
    _approved_plan_for(db, project, "https://a.example.com")

    async def fake_start_run(db, **kw):
        captured.update(kw)
        run = Run(project_id=kw["project_id"], number=99, title=kw.get("title", ""),
                  base_url=kw.get("base_url", ""), trigger=kw.get("trigger", ""),
                  environment=kw.get("environment", ""), totals={})
        db.add(run)
        db.flush()
        return run
    monkeypatch.setattr("galeqea.services.runs.start_run", fake_start_run)

    runner.invoke(app, ["test", "https://a.example.com", "--project", project.id, "--no-wait"])
    assert captured["base_url"] == "https://a.example.com"
    assert captured["title"] == "Golden Path: https://a.example.com (CI)"
    assert captured["trigger"] == "ci"


def test_readiness_command_gates_on_no_go(db, project):
    from galeqea.models import Run, RunTest
    run = Run(project_id=project.id, number=1, trigger="golden_path", base_url="https://x.com",
              environment="production", status="failed", totals={})
    db.add(run)
    db.flush()
    db.add(RunTest(run_id=run.id, test_case_id="", test_key="P-GP-A11Y-01",
                   title="a11y", status="failed", error_message="boom"))
    db.commit()

    ok = runner.invoke(app, ["readiness", "latest", "--project", project.id])
    assert ok.exit_code == 0 and "NO-GO" in ok.output
    gated = runner.invoke(app, ["readiness", "latest", "--project", project.id, "--fail-on", "no_go"])
    assert gated.exit_code == 1  # the CI gate fires
