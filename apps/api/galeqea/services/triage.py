"""Triage: turn a run's failures into a board where every one gets a disposition.

Failures are grouped by their root-cause *signature* (the same fingerprint the
intelligence layer uses for known-vs-new), each group carrying a verdict, an RCA
class, the tests it hit, and a suggested disposition. The stage is not done until
every failing group has a disposition recorded on the run: Heal, Rerun (×3),
Mark-expected, File-bug (gated), or Quarantine (with an expiry). That recording is
deterministic; the heavier actions reuse the existing rerun and approval gates.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from ..models import Run, RunTest, TestCase
from ..models.base import utcnow
from ..models.testing import RunStatus

#: The intelligence layer's history-based calls we trust outright.
_TRUSTED_CLASS = {"flaky": "flaky", "env": "env", "data": "data"}

#: Signals in the error that mean the environment/network failed us, not the app:
#: a navigation timeout, a connection reset, a 5xx or a rate-limit. Classing these
#: as app bugs (as the old code did) cries wolf; classing them env sends them to a
#: rerun instead of a bug report.
_ENV_MARKERS = ("net::err", "err_", "econnrefused", "econnreset", "econnaborted",
                "socket hang up", "navigation timeout", "timeout 3", "timeout exceeded",
                "page.goto", " 429", " 502", " 503", " 504", "too many requests",
                "service unavailable", "bad gateway", "gateway timeout")

#: The disposition the board proposes for each RCA class.
_SUGGEST = {"flaky": "rerun", "test-bug": "heal", "app-bug": "file_bug",
            "env": "rerun", "data": "mark_expected"}

#: What every group may be dispositioned to. Order is the button order.
DISPOSITIONS = ["heal", "rerun", "mark_expected", "file_bug", "quarantine"]

#: How much to trust the RCA class: a clear signal (a stale locator, an env/data
#: cause) is high; a fresh app-bug guess is medium; a flake, low.
_CONFIDENCE = {"test-bug": "high", "env": "high", "data": "high",
               "app-bug": "medium", "flaky": "low"}

_FAILING = (RunStatus.FAILED, RunStatus.ERROR, RunStatus.BLOCKED)


def _unit_of(rt: RunTest) -> str:
    """The unit a run test covers, read from its "label: unit" title."""
    return rt.title.split(": ", 1)[1].strip() if ": " in rt.title else ""


def _cause(rt: RunTest, type_key: str) -> str:
    """A human root cause for the group label: 'which barrier', not a hash."""
    import re

    from .test_plan import type_label
    msg = (rt.error_message or "").strip()
    if type_key == "a11y":
        # Join the rule lists across impact tiers (each in parens), skipping the
        # "(s)" plural, as in "accessibility: label, color-contrast".
        groups = [g for g in re.findall(r"\(([^)]*)\)", msg) if g != "s"]
        rules = ", ".join(dict.fromkeys(", ".join(groups).split(", ")))
        return f"accessibility: {rules}" if rules else "accessibility violations"
    if type_key == "perf":
        return "performance budget exceeded"
    if _healable(rt):
        return "element not found (stale locator)"
    if type_key == "security":
        return "security header / hygiene"
    first = msg.splitlines()[0] if msg else f"{type_label(type_key)} failure"
    return first[:60]


def _which_disposition_reasons(group: dict) -> dict:
    """Why each disposition is or isn't sensible for a group, so the card can grey
    the unavailable ones with a reason rather than hiding them. Rerun is always on
    (confirming a flake is the tester's call)."""
    rca = group["rca"]
    healable = group["healable"]
    has_baseline = group["type"] in {"visual", "responsive"}
    return {
        "rerun": {"enabled": True},
        "heal": {"enabled": healable,
                 "reason": None if healable else "no stale-locator signal, not a test bug"},
        "mark_expected": {"enabled": has_baseline or rca in {"data", "env"},
                          "reason": None if (has_baseline or rca in {"data", "env"})
                          else "only for a baseline/data/env failure, not an app bug"},
        "file_bug": {"enabled": rca in {"app-bug"},
                     "reason": None if rca == "app-bug" else "not classed as an app bug"},
        "quarantine": {"enabled": True},
    }


def _verdict(status: str) -> str:
    if status in (RunStatus.PASSED,):
        return "verified"
    if status == RunStatus.FLAKY:
        return "partial"
    if status == RunStatus.BLOCKED:
        return "blocked"
    return "failed"


def _healable(rt: RunTest) -> bool:
    """A failure that is really a stale/absent *locator*: heal territory, not an
    app bug. Deliberately does NOT match a bare "not found" (a content assertion
    like "text not found" is an app bug, not a locator problem)."""
    blob = f"{rt.error_type} {rt.error_message}".lower()
    return any(w in blob for w in ("locator", "selector", "no element",
                                   "element not found", "waiting for",
                                   "not visible", "not attached"))


def _rca(rt: RunTest) -> str:
    """The root-cause class, from the error itself:

    - a stale/absent locator → **test-bug** (heal it)
    - a navigation timeout / net error / 5xx / 429 → **env** (rerun, don't file a bug)
    - everything else that failed (a content assertion, an a11y/perf/security
      violation, a 404 on an internal link) → **app-bug**

    The intelligence layer's flaky/env/data calls (which use run history) win when
    present, since they know more than one error line can.
    """
    trusted = _TRUSTED_CLASS.get((rt.classification or "").lower())
    if trusted:
        return trusted
    if _healable(rt):
        return "test-bug"
    blob = f" {rt.error_type} {rt.error_message} ".lower()
    if any(m in blob for m in _ENV_MARKERS):
        return "env"
    return "app-bug"


def _signature(rt: RunTest) -> str:
    if rt.failure_signature:
        return rt.failure_signature
    first_line = (rt.error_message or "").strip().splitlines()[0] if rt.error_message else ""
    return (rt.error_type or "failure") + (f": {first_line[:80]}" if first_line else "")


def triage_board(db, run: Run, journey=None) -> dict:
    """Group the run's failures by signature and attach a disposition to each."""
    results = list(db.execute(select(RunTest).where(RunTest.run_id == run.id)).scalars())
    failing = [r for r in results if r.status in _FAILING]
    partial = [r for r in results if r.status == RunStatus.FLAKY]
    recorded = (run.triage or {}).get("dispositions", {})

    from .test_plan import type_from_key
    groups: dict[str, dict] = {}
    for rt in failing + partial:
        sig = _signature(rt)
        type_key = type_from_key(rt.test_key) or "functional"
        g = groups.setdefault(sig, {
            "signature": sig, "rca": _rca(rt), "verdict": _verdict(rt.status),
            "type": type_key, "count": 0, "tests": [], "units": [], "keys": [],
            "cause": _cause(rt, type_key), "suggested": "", "disposition": None,
            "healable": False,
        })
        g["count"] += 1
        g["healable"] = g["healable"] or _healable(rt)
        unit = _unit_of(rt)
        if unit and unit not in g["units"]:
            g["units"].append(unit)
        g["keys"].append(rt.test_key)
        g["tests"].append({"key": rt.test_key, "title": rt.title, "unit": unit,
                           "status": rt.status, "detail": (rt.error_message or "")[:200],
                           "evidence": f"/runs/{run.id}"})
    for sig, g in groups.items():
        g["suggested"] = _SUGGEST.get(g["rca"], "file_bug")
        g["disposition"] = recorded.get(sig)
        g["actions"] = DISPOSITIONS
        g["confidence"] = _CONFIDENCE.get(g["rca"], "medium")
        # A human root-cause label; the hash rides along as data only.
        units = ", ".join(g["units"][:2]) + ("…" if len(g["units"]) > 2 else "")
        g["label"] = f"{g['cause']}" + (f" · {units}" if units else "")
        g["disposition_reasons"] = _which_disposition_reasons(g)

    board = sorted(groups.values(),
                   key=lambda g: (g["disposition"] is not None, g["verdict"] != "failed"))
    open_failures = [g for g in board if g["verdict"] in ("failed", "blocked")
                     and not g["disposition"]]
    return {
        "run_id": run.id, "run_number": run.number, "status": run.status,
        "target": journey.target if journey else run.base_url,
        "groups": board,
        "failure_count": len(failing),
        "group_count": len(board),
        "open_count": len(open_failures),
        # The stage's exit criterion: no failing group is left without a call.
        "resolved": len(open_failures) == 0,
    }


def set_disposition(db, run: Run, signature: str, disposition: str, *,
                    actor: str | None = None, expiry_days: int = 7) -> dict:
    """Record a disposition for a failure group and apply its deterministic effect.

    Recording is always durable on the run. Quarantine and mark-expected act on the
    tests here; rerun and file-bug are handed to the existing gates by the caller.
    """
    if disposition not in DISPOSITIONS:
        return {"ok": False, "error": f"unknown disposition {disposition!r}"}

    triage = dict(run.triage or {})
    dispositions = dict(triage.get("dispositions", {}))
    dispositions[signature] = {"disposition": disposition, "by": actor,
                               "at": utcnow().isoformat()}
    triage["dispositions"] = dispositions

    affected = [r for r in db.execute(
        select(RunTest).where(RunTest.run_id == run.id)).scalars()
        if _signature(r) == signature]
    keys = [r.test_key for r in affected]

    if disposition in ("quarantine", "mark_expected"):
        for tc in db.execute(
            select(TestCase).where(TestCase.project_id == run.project_id,
                                   TestCase.key.in_(keys))).scalars():
            data = dict(tc.test_data or {})
            if disposition == "quarantine":
                tc.quarantined = True
                until = (utcnow() + timedelta(days=expiry_days)).isoformat()
                data["quarantine"] = {"until": until, "reason": signature, "by": actor}
            else:  # mark_expected: a known, accepted failure, not a regression
                data["expected_failure"] = {"reason": signature, "by": actor,
                                            "at": utcnow().isoformat()}
            tc.test_data = data
    run.triage = triage
    db.commit()
    return {"ok": True, "disposition": disposition, "signature": signature,
            "keys": keys, "board": triage_board(db, run)}
