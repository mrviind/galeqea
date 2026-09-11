"""WO#7 7-B-2: the run cost ticker. A run's model spend is only its in-run
healing/RCA calls; execution uses no model, so a healthy or No-AI run is $0, which
is the "pay to build, not to re-run" promise made visible.
"""

from __future__ import annotations

from datetime import timedelta

from galeqea.models import AgentTrace, Run, RunTest
from galeqea.models.base import utcnow


def _run(db, project, *, started_minutes_ago=5):
    run = Run(project_id=project.id, number=1, title="Smoke", status="passed",
              started_at=utcnow() - timedelta(minutes=started_minutes_ago), finished_at=utcnow())
    db.add(run)
    db.flush()
    db.add(RunTest(run_id=run.id, test_case_id="", test_key="A", status="passed"))
    db.commit()
    return run


def _client():
    from fastapi.testclient import TestClient

    from galeqea.main import app
    return TestClient(app)


def test_no_ai_run_costs_zero(db, project):
    run = _run(db, project)
    u = _client().get(f"/api/projects/{project.id}/runs/{run.id}").json()["model_usage"]
    assert u["calls"] == 0 and u["tokens"] == 0 and u["cost_usd"] == 0.0
    assert u["cache_hits"] == 0 and u["heals"] == 0


def test_in_run_healing_is_attributed(db, project):
    # a run whose window is still open (finished a minute from now), so the healing
    # traces recorded during it fall inside [started_at, finished_at].
    run = Run(project_id=project.id, number=1, title="Smoke", status="passed",
              started_at=utcnow() - timedelta(minutes=5), finished_at=utcnow() + timedelta(minutes=1))
    db.add(run)
    db.commit()
    # a healer and an RCA call happened during the run window
    db.add_all([
        AgentTrace(project_id=project.id, agent_role="healer",
                   input_tokens=400, output_tokens=120, cost_usd=0.0031),
        AgentTrace(project_id=project.id, agent_role="rca_analyst",
                   input_tokens=1000, output_tokens=300, cost_usd=0.0075),
        # an unrelated test-design call is NOT a run cost
        AgentTrace(project_id=project.id, agent_role="test_designer",
                   input_tokens=9999, output_tokens=9999, cost_usd=1.0),
    ])
    db.commit()
    r = _client().get(f"/api/projects/{project.id}/runs/{run.id}").json()
    u = r["model_usage"]
    assert u["calls"] == 2                       # healer + rca, not the designer
    assert u["tokens"] == 400 + 120 + 1000 + 300
    assert u["cost_usd"] == 0.0106


def test_cache_hits_are_counted_from_heals(db, project):
    from galeqea.models import HealEvent
    run = _run(db, project)
    rt = db.execute(
        __import__("sqlalchemy").select(RunTest).where(RunTest.run_id == run.id)).scalars().first()
    db.add_all([
        HealEvent(project_id=project.id, run_test_id=rt.id, strategy="cache"),
        HealEvent(project_id=project.id, run_test_id=rt.id, strategy="cache"),
        HealEvent(project_id=project.id, run_test_id=rt.id, strategy="fingerprint"),
    ])
    db.commit()
    u = _client().get(f"/api/projects/{project.id}/runs/{run.id}").json()["model_usage"]
    assert u["heals"] == 3 and u["cache_hits"] == 2   # 2 of 3 heals were zero-token cache hits


def test_usage_outside_the_window_is_ignored(db, project):
    # a run that finished before the trace was recorded
    run = Run(project_id=project.id, number=2, title="old", status="passed",
              started_at=utcnow() - timedelta(hours=2),
              finished_at=utcnow() - timedelta(hours=1))
    db.add(run)
    db.flush()
    db.add(AgentTrace(project_id=project.id, agent_role="healer",
                      input_tokens=500, output_tokens=100, cost_usd=0.01))  # created now
    db.commit()
    r = _client().get(f"/api/projects/{project.id}/runs/{run.id}").json()
    assert r["model_usage"]["calls"] == 0
