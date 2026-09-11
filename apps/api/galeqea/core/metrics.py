"""Prometheus metrics.

Counters are advanced from the event bus (a sink alongside webhooks); gauges that
reflect current state (active runs, cumulative LLM tokens) are read from live
state on scrape. Exposed at ``/metrics`` in the standard text format.
"""

from __future__ import annotations

from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest

from .events import Ev

RUNS = Counter("galeqea_runs_total", "Runs finished, by terminal status.", ["status"])
TESTS = Counter("galeqea_tests_total", "Test executions finished, by status.", ["status"])
FAILURES = Counter("galeqea_failures_total", "Test executions that failed or errored.")
HEALS = Counter("galeqea_heals_total", "Self-heals applied during a run.")
APPROVALS = Counter("galeqea_approvals_total", "Approval decisions, by outcome.", ["decision"])

ACTIVE_RUNS = Gauge("galeqea_active_runs", "Runs executing right now (queue depth).")
LLM_TOKENS = Gauge("galeqea_llm_tokens_total", "Cumulative LLM tokens spent.", ["kind"])
QUEUE_DEPTH = Gauge("galeqea_queue_depth", "Background jobs waiting or running "
                    "(the job queue depth).", ["backend"])

# WO#8: "cheap by design" observability.
STATE_TOKENS = Histogram(
    "galeqea_state_tokens", "Trimmed page-state size (tokens) sent to the model per call.",
    buckets=(50, 100, 200, 300, 400, 500, 600, 800, 1200, 2000))
CACHE_HITS = Counter(
    "galeqea_cache_hits_total", "Step-cache lookups, by outcome (hit resolves with no model).",
    ["outcome"])


def observe_state_tokens(tokens: int | None) -> None:
    """Record the page-state token size of one model call. Best-effort."""
    try:
        if tokens is not None:
            STATE_TOKENS.observe(int(tokens))
    except Exception:  # noqa: BLE001 - metrics must never break the caller
        pass


def record_cache(outcome: str) -> None:
    """Count a step-cache lookup: 'hit' | 'miss' | 'stale'."""
    try:
        CACHE_HITS.labels(outcome=outcome).inc()
    except Exception:  # noqa: BLE001
        pass

_FAILED = {"failed", "error"}


async def metrics_sink(event) -> None:
    """Bus sink: advance counters. Best-effort; never breaks the caller."""
    try:
        payload = event.payload or {}
        if event.type == Ev.RUN_FINISHED:
            RUNS.labels(status=str(payload.get("status", "unknown"))).inc()
        elif event.type == Ev.RUN_TEST_FINISHED:
            status = str(payload.get("status", "unknown"))
            TESTS.labels(status=status).inc()
            if status in _FAILED:
                FAILURES.inc()
            if payload.get("healed"):
                HEALS.inc()
        elif event.type == Ev.APPROVAL_DECIDED:
            APPROVALS.labels(decision=str(payload.get("decision", "unknown"))).inc()
    except Exception:  # noqa: BLE001 - a metrics sink must never break event delivery
        pass


def _refresh_gauges() -> None:
    """Set point-in-time gauges from live state, just before a scrape."""
    try:
        from ..services.runs import active_runs
        ACTIVE_RUNS.set(len(active_runs()))
    except Exception:  # noqa: BLE001
        pass
    try:
        from ..jobs import get_queue
        queue = get_queue()
        QUEUE_DEPTH.labels(backend=queue.kind).set(queue.depth())
    except Exception:  # noqa: BLE001
        pass
    try:
        from sqlalchemy import func, select

        from ..db import session_scope
        from ..models import AgentTrace
        with session_scope() as db:
            got = db.execute(select(
                func.coalesce(func.sum(AgentTrace.input_tokens), 0),
                func.coalesce(func.sum(AgentTrace.output_tokens), 0),
            )).one()
            LLM_TOKENS.labels(kind="input").set(int(got[0] or 0))
            LLM_TOKENS.labels(kind="output").set(int(got[1] or 0))
    except Exception:  # noqa: BLE001 - metrics are best-effort, never fatal
        pass


def render() -> tuple[bytes, str]:
    """(body, content_type) for the /metrics endpoint."""
    _refresh_gauges()
    return generate_latest(), CONTENT_TYPE_LATEST
