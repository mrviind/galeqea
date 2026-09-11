"""P1-5: the Prometheus /metrics endpoint and the event-bus metrics sink."""

from __future__ import annotations

import asyncio
import json

from fastapi.testclient import TestClient

from galeqea.core.events import Ev, Event
from galeqea.core.metrics import FAILURES, RUNS, metrics_sink
from galeqea.main import app


def test_metrics_endpoint_exposes_prometheus_text():
    r = TestClient(app).get("/metrics")
    assert r.status_code == 200
    assert "text/plain" in r.headers["content-type"]
    body = r.text
    for name in ("galeqea_runs_total", "galeqea_active_runs",
                 "galeqea_llm_tokens_total", "galeqea_failures_total"):
        assert name in body


def _val(counter, **labels) -> float:
    return counter.labels(**labels)._value.get() if labels else counter._value.get()


def test_the_sink_advances_counters_off_the_bus():
    before_pass = _val(RUNS, status="passed")
    before_fail = _val(FAILURES)

    asyncio.run(metrics_sink(Event(type=Ev.RUN_FINISHED, project_id="p", payload={"status": "passed"})))
    asyncio.run(metrics_sink(Event(
        type=Ev.RUN_TEST_FINISHED, project_id="p",
        payload={"status": "failed", "healed": True})))

    assert _val(RUNS, status="passed") == before_pass + 1
    assert _val(FAILURES) == before_fail + 1


def test_grafana_dashboard_is_valid_json_referencing_the_metrics():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[3]
    dash = json.loads((root / "deploy/grafana/galeqea-dashboard.json").read_text())
    assert dash["uid"] == "galeqea"
    exprs = json.dumps(dash)
    assert "galeqea_active_runs" in exprs and "galeqea_runs_total" in exprs
