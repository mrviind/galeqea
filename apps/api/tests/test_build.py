"""Build: the plan becomes durable, filed tests, one per (type × unit)."""

from __future__ import annotations

from sqlalchemy import select

from galeqea.models import StepAction, TestCase, TestStatus, TestSuite
from galeqea.services import journeys
from galeqea.services import test_plan as tp
from galeqea.services.build import build_tests_from_plan

_DISC = {"base": "https://x.com", "pages": ["https://x.com", "https://x.com/a"],
         "forms": 1, "discovered": 2, "apis": []}


def _built(db, project):
    typed = tp.build_typed_plan(_DISC)
    journey = journeys.start(db, project.id, "https://x.com")
    journey.plan = {"pages": _DISC["pages"], "typed": typed}
    journey.discovery = _DISC
    return typed, journey, build_tests_from_plan(db, project, journey)


def test_build_files_one_test_per_enabled_type_and_unit(db, project):
    typed, journey, result = _built(db, project)

    cases = list(db.execute(
        select(TestCase).where(TestCase.project_id == project.id)).scalars())
    assert len(cases) == result["filed"] > typed["totals"]["types_enabled"]  # per-unit, not per-type
    for c in cases:
        assert c.status == TestStatus.PROPOSED
        assert c.provenance["origin"] == "golden_path"
        assert c.provenance["journey_id"] == journey.id
        assert c.provenance["plan_version"] == typed["version"]
        assert c.provenance["unit"]  # every test names the unit it covers
        assert c.steps  # ...and has runnable steps


def test_page_types_file_one_test_per_page(db, project):
    _typed, _journey, _result = _built(db, project)
    a11y = [c for c in db.execute(
        select(TestCase).where(TestCase.project_id == project.id)).scalars()
        if c.provenance.get("type") == "a11y"]
    assert {c.provenance["unit"] for c in a11y} == {"/", "/a"}  # one per discovered page


def test_typed_tests_use_the_right_runner_steps(db, project):
    typed = tp.toggle(tp.build_typed_plan(_DISC), "visual", True)  # ensure visual is on
    journey = journeys.start(db, project.id, "https://x.com")
    journey.plan = {"pages": _DISC["pages"], "typed": typed}
    journey.discovery = _DISC
    build_tests_from_plan(db, project, journey)

    by_type: dict[str, TestCase] = {}
    for c in db.execute(select(TestCase).where(TestCase.project_id == project.id)).scalars():
        by_type.setdefault(c.provenance["type"], c)
    assert any(s.action == StepAction.ASSERT_A11Y for s in by_type["a11y"].steps)
    assert any(s.action == StepAction.ASSERT_PERF for s in by_type["perf"].steps)
    assert any(s.action == StepAction.SNAPSHOT for s in by_type["visual"].steps)


def test_smoke_subset_is_tagged_and_reuses_built_tests(db, project):
    _typed, _journey, result = _built(db, project)
    assert result["smoke_keys"], "there must be a smoke subset"
    assert set(result["smoke_keys"]) <= set(result["run_keys"])  # reuses, not a parallel set
    smoke_cases = list(db.execute(
        select(TestCase).where(TestCase.key.in_(result["smoke_keys"]))).scalars())
    for c in smoke_cases:
        assert "smoke" in (c.tags or [])
        assert c.provenance["page"] == _DISC["base"]  # the entry page only


def test_each_type_is_grouped_into_a_golden_path_suite(db, project):
    _typed, _journey, result = _built(db, project)
    suites = list(db.execute(
        select(TestSuite).where(TestSuite.project_id == project.id)).scalars())
    names = {s.name for s in suites}
    assert "Golden Path · Accessibility" in names
    a11y_suite = next(s for s in suites if s.name == "Golden Path · Accessibility")
    assert len(a11y_suite.members) == 2  # both pages
    assert a11y_suite.query == {"tags": ["golden-path", "a11y"]}


def test_rebuild_reuses_keys_not_duplicates(db, project):
    typed = tp.build_typed_plan(_DISC)
    journey = journeys.start(db, project.id, "https://x.com")
    journey.plan = {"pages": _DISC["pages"], "typed": typed}
    journey.discovery = _DISC
    first = build_tests_from_plan(db, project, journey)
    second = build_tests_from_plan(db, project, journey)  # same plan again
    assert first["run_keys"] == second["run_keys"]
    total = db.execute(
        select(TestCase).where(TestCase.project_id == project.id)).scalars().all()
    assert len(total) == first["filed"]  # reused, not doubled
    suites = db.execute(
        select(TestSuite).where(TestSuite.project_id == project.id)).scalars().all()
    a11y = next(s for s in suites if s.name == "Golden Path · Accessibility")
    assert len(a11y.members) == 2  # members reconciled, not duplicated
