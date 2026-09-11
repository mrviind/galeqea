"""WO#5 slice 5-A: the test-manager object model persists and links up."""

from __future__ import annotations

from galeqea.models import (
    Cycle,
    CycleStatus,
    DefectLink,
    Environment,
    Milestone,
    MilestoneStatus,
    SharedStep,
    TestPlan,
)
from galeqea.models.base import utcnow


def test_the_release_chain_persists(db, project):
    m = Milestone(project_id=project.id, name="Release 1.4", version="1.4",
                  target_date=utcnow(), status=MilestoneStatus.ACTIVE,
                  exit_criteria=[{"metric": "pass_rate", "op": ">=", "value": 0.95}])
    env = Environment(project_id=project.id, name="staging",
                      base_url="https://staging.example.com", tags=["preprod"])
    db.add_all([m, env]); db.commit()

    plan = TestPlan(project_id=project.id, milestone_id=m.id, name="Regression 1.4",
                    selection={"query": {"tags": ["golden-path"]}},
                    configurations=[{"browser": "chromium", "viewport": "desktop", "env": env.id}])
    db.add(plan); db.commit()

    cycle = Cycle(project_id=project.id, plan_id=plan.id, milestone_id=m.id,
                  environment_id=env.id, name="Regression 1.4 · chromium/desktop",
                  configuration={"browser": "chromium", "viewport": "desktop"},
                  status=CycleStatus.ACTIVE,
                  pinned_cases=[{"test_case_id": "tc1", "key": "P-1", "version": 3}],
                  counters={"planned": 1, "executed": 0})
    db.add(cycle); db.commit()

    defect = DefectLink(project_id=project.id, result_id="rt1", tracker="github",
                        key="123", url="https://github.com/x/y/issues/123",
                        status_cached="open")
    shared = SharedStep(project_id=project.id, name="Log in",
                        steps=[{"action": "goto", "value": {"url": "/login"}}])
    db.add_all([defect, shared]); db.commit()

    # Round-trip + the chain links.
    got = db.get(Cycle, cycle.id)
    assert got.plan_id == plan.id and got.milestone_id == m.id and got.environment_id == env.id
    assert got.pinned_cases[0]["version"] == 3
    assert db.get(TestPlan, plan.id).milestone_id == m.id
    assert db.get(Milestone, m.id).exit_criteria[0]["metric"] == "pass_rate"
    assert db.get(DefectLink, defect.id).tracker == "github"
    assert db.get(SharedStep, shared.id).steps[0]["action"] == "goto"


def test_testcase_gains_shared_steps_and_parameters(db, project):
    from galeqea.models import TestCase
    tc = TestCase(project_id=project.id, key="P-PARAM-1", title="parametrised",
                  shared_step_ids=["ss1"], parameters={"user": "alice", "plan": "pro"})
    db.add(tc); db.commit()
    got = db.get(TestCase, tc.id)
    assert got.shared_step_ids == ["ss1"] and got.parameters["user"] == "alice"
