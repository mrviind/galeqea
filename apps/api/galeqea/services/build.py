"""Build: turn an approved plan into durable, runnable tests-as-data.

Each enabled test type in the plan is expanded into one ``TestCase`` per *target
unit* (a page, a form, an endpoint, a viewport×page, a fault×page), capped by the
plan row's promised count. So a later failure reads "a11y: /login has 2 serious
violations", not "accessibility failed", and the report / triage / readiness
stages have per-page and per-form results to work with.

Steps are in the runner's own vocabulary (assert_a11y, assert_perf, snapshot, …);
every test carries provenance back to the plan version, the type, and the unit.
Tests of one type are grouped into a Golden Path suite (``Golden Path ·
Accessibility`` …) so "run the a11y suite" and per-type Run-board progress work
naturally. The entry-page fast checks are tagged ``smoke`` so the smoke gate can
reuse them rather than filing a parallel set. Deterministic; no model needed.
"""

from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import select

from ..models import (
    StepAction,
    SuiteMember,
    TestCase,
    TestCategory,
    TestStatus,
    TestStep,
    TestSuite,
)

#: Short, human suite names per type, such as "Golden Path · Accessibility".
_SUITE_NAME = {
    "functional": "Functional", "forms": "Forms", "links": "Links", "api": "API",
    "a11y": "Accessibility", "perf": "Performance", "visual": "Visual",
    "responsive": "Responsive", "cross_browser": "Cross-browser",
    "security": "Security", "seo": "SEO", "resilience": "Resilience",
    "data_driven": "Data-driven", "exploratory": "Exploratory", "manual": "Manual",
}

#: Types whose entry-page unit belongs in the ≤3-min smoke subset, a fast "does
#: the front door open and is the console clean" gate the Smoke stage reuses.
_SMOKE_TYPES = {"functional", "seo"}

#: Page-scoped types: one test per discovered page (capped by the plan count, so
#: a count-1 type like security/links files only the entry page).
_PAGE_TYPES = {"functional", "a11y", "perf", "seo", "visual", "security",
               "links", "cross_browser"}

_VIEWPORTS = ["mobile", "tablet", "desktop"]
_FAULTS = ["slow-3g", "offline", "http-5xx"]


def _path(url: str) -> str:
    return urlparse(url).path or "/"


def _required_kind(journey, page_url: str) -> str | None:
    from .access import required_kind
    return required_kind(journey, page_url)


def _endpoint_id(endpoint) -> str:
    if isinstance(endpoint, dict):
        method = endpoint.get("method", "GET")
        where = endpoint.get("path") or endpoint.get("url") or ""
        return f"{method} {where}".strip()
    return str(endpoint)


def _endpoint_url(endpoint, base: str) -> str:
    if isinstance(endpoint, dict):
        return endpoint.get("url") or endpoint.get("path") or base
    return str(endpoint)


def _units_for(type_key: str, disc: dict, count: int) -> list[dict]:
    """The units a type expands into, capped at ``count`` (the plan's promised
    number, used only as an upper bound). A unit is what one filed test covers."""
    base = disc.get("base") or ""
    pages = [p for p in (disc.get("pages") or []) if p] or ([base] if base else [])
    entry = pages[0] if pages else base
    forms = max(0, int(disc.get("forms") or 0))
    apis = [a for a in (disc.get("apis") or []) if a]

    if type_key in _PAGE_TYPES:
        units = [{"id": _path(p), "label": _path(p), "page": p, "kind": "page"}
                 for p in pages]
    elif type_key in {"forms", "data_driven"}:
        units = [{"id": f"form-{i + 1}", "label": f"form #{i + 1} on {_path(entry)}",
                  "page": entry, "kind": "form"} for i in range(forms)]
    elif type_key == "api":
        units = [{"id": _endpoint_id(e), "label": _endpoint_id(e),
                  "page": _endpoint_url(e, base), "kind": "endpoint"} for e in apis]
    elif type_key == "responsive":
        units = [{"id": f"{_path(p)}@{v}", "label": f"{_path(p)} · {v}", "page": p,
                  "kind": "viewport", "viewport": v} for p in pages for v in _VIEWPORTS]
    elif type_key == "resilience":
        units = [{"id": f, "label": f"{f} on {_path(entry)}", "page": entry,
                  "kind": "fault", "fault": f} for f in _FAULTS]
    elif type_key in {"exploratory", "manual"}:
        units = [{"id": f"charter-{i + 1}", "label": f"charter #{i + 1}",
                  "page": entry, "kind": "charter"} for i in range(max(1, count))]
    else:
        units = [{"id": _path(entry), "label": _path(entry), "page": entry, "kind": "page"}]

    if not units and entry:
        units = [{"id": _path(entry), "label": _path(entry), "page": entry, "kind": "page"}]
    return units[:count] if count and count > 0 else units


def _steps_for(type_key: str, unit: dict) -> list[dict]:
    """Runner steps for one type on one unit."""
    url = unit["page"]
    goto = {"action": StepAction.GOTO, "intent": f"Open {url}", "value": {"url": url}}
    body = {"action": StepAction.EXPECT_VISIBLE, "intent": "The page renders",
            "target": {"ladder": [{"kind": "css", "value": "body"}]}}
    match type_key:
        case "a11y":
            return [goto, {"action": StepAction.ASSERT_A11Y,
                           "intent": f"No serious/critical axe violations on {unit['label']}",
                           "value": {"tags": ["wcag2a", "wcag2aa", "wcag21aa", "wcag22aa"],
                                     "fail_on": ["serious", "critical"]}}]
        case "perf":
            return [goto, {"action": StepAction.ASSERT_PERF,
                           "intent": "Core Web Vitals within budget",
                           "value": {"lcp_ms": 2500, "cls": 0.1}}]
        case "visual":
            return [goto, {"action": StepAction.SNAPSHOT, "intent": "Visual baseline",
                           "value": {"name": _snap_name(unit), "viewports": ["desktop", "mobile"]}}]
        case "responsive":
            return [goto, {"action": StepAction.SNAPSHOT,
                           "intent": f"Renders at {unit.get('viewport', 'desktop')}",
                           "value": {"name": _snap_name(unit),
                                     "viewports": [unit.get("viewport", "desktop")]}}]
        case "resilience":
            return [{"action": StepAction.NETWORK_CONDITION,
                     "intent": f"Inject {unit.get('fault', 'slow-3g')}",
                     "value": {"profile": unit.get("fault", "slow-3g")}}, goto, body]
        case "api" | "data_driven":
            return [{"action": StepAction.API_REQUEST,
                     "intent": f"{unit['label']} returns a healthy status",
                     "value": {"url": url, "expect_status_lt": 400}}]
        case "forms":
            return [goto,
                    {"action": StepAction.EXPECT_VISIBLE, "intent": "The form is present",
                     "target": {"ladder": [{"kind": "css", "value": "form"}]}},
                    {"action": StepAction.NOTE,
                     "intent": "Submit empty / invalid / boundary values and expect field "
                               "validation, not a crash or a silent accept"}]
        case "exploratory" | "manual":
            return [goto, {"action": StepAction.NOTE,
                           "intent": "Charter: explore this area for issues the scripted tests miss"}]
        case _:
            return [goto, body]


def _snap_name(unit: dict) -> str:
    stem = unit["id"].strip("/").replace("/", "-") or "home"
    return stem[:60]


def _category_for(type_key: str) -> str:
    if type_key == "exploratory":
        return TestCategory.EXPLORATORY
    if type_key == "manual":
        return TestCategory.MANUAL
    return TestCategory.AUTOMATED


def build_tests_from_plan(db, project, journey) -> dict:
    """File one proposed test per enabled type × unit, grouped into per-type Golden
    Path suites, with provenance. Returns the filed tests, the suites, the keys to
    run, and the smoke subset."""
    typed = (journey.plan or {}).get("typed") or {}
    disc = dict(journey.discovery or {})
    if not disc.get("pages") and (journey.plan or {}).get("pages"):
        disc["pages"] = journey.plan["pages"]
    base = disc.get("base") or journey.target
    disc["base"] = base
    pages = [p for p in (disc.get("pages") or []) if p] or [base]
    disc["pages"] = pages
    entry = pages[0] if pages else base
    plan_version = typed.get("version", journey.plan_version or 1)

    filed: list[dict] = []
    suites: list[dict] = []
    for row in typed.get("types", []):
        if not row.get("enabled"):
            continue
        key = row["key"]
        units = _units_for(key, disc, int(row.get("count", 0)))
        deterministic = int(row.get("est_tokens", 0)) == 0
        category = _category_for(key)
        risk = row.get("risk", "medium")
        type_cases: list[TestCase] = []
        for i, unit in enumerate(units, start=1):
            tkey = f"{project.key}-GP-{key.upper().replace('_', '')}-{i:02d}"
            title = f"{row['label']}: {unit['label']}"
            is_smoke = (key in _SMOKE_TYPES and unit.get("kind") == "page"
                        and unit["page"] == entry)
            tags = ["golden-path", key] + (["smoke"] if is_smoke else [])
            provenance = {
                "origin": "golden_path", "type": key, "unit": unit["id"],
                "unit_kind": unit.get("kind", "page"), "page": unit["page"],
                "journey_id": journey.id, "plan_version": plan_version,
                "deterministic": deterministic,
            }
            # Tag the credential kind this page requires, so the run injects the
            # matching credential, or skips the test with a reason when none exists.
            auth_kind = _required_kind(journey, unit["page"])
            if auth_kind:
                provenance["auth_kind"] = auth_kind
            tc = _file_test(db, project.id, tkey, title, tags, provenance,
                            _steps_for(key, unit), category, risk)
            type_cases.append(tc)
            filed.append({"key": tkey, "type": key, "label": title,
                          "unit": unit["id"], "smoke": is_smoke})
        if type_cases:
            suite = _upsert_suite(db, project.id, key,
                                  _SUITE_NAME.get(key, row["label"]), type_cases)
            suites.append({"key": key, "name": suite.name,
                           "count": len(type_cases), "risk": risk})
    db.commit()
    smoke_keys = [f["key"] for f in filed if f["smoke"]]
    return {
        "ok": True,
        "filed": len(filed),
        "tests": filed,
        "suites": suites,
        "run_keys": [f["key"] for f in filed],
        "smoke_keys": smoke_keys,
        "target": journey.target,
    }


def _file_test(db, project_id, key, title, tags, provenance, steps, category, risk):
    existing = db.execute(
        select(TestCase).where(TestCase.project_id == project_id, TestCase.key == key)
    ).scalar_one_or_none()
    if existing is not None:
        existing.title = title
        existing.tags = tags
        existing.provenance = provenance
        existing.category = category
        existing.risk = risk
        # A rebuild is a fresh proposal for the current plan, so it must not inherit
        # a prior run's triage state (a quarantine or expected-failure mark from an
        # earlier journey), or the rebuilt test would be silently excluded from the
        # next run. Reset transient run-state; keep only the durable identity.
        existing.status = TestStatus.PROPOSED
        existing.quarantined = False
        data = dict(existing.test_data or {})
        data.pop("quarantine", None)
        data.pop("expected_failure", None)
        existing.test_data = data
        for s in list(existing.steps):
            db.delete(s)
        db.flush()
        tc = existing
    else:
        tc = TestCase(
            project_id=project_id, key=key, title=title,
            status=TestStatus.PROPOSED, category=category,
            tags=tags, provenance=provenance, risk=risk,
        )
        db.add(tc)
        db.flush()
    for i, step in enumerate(steps):
        db.add(TestStep(test_case_id=tc.id, index=i, action=step["action"],
                        intent=step.get("intent", ""), value=step.get("value") or {},
                        target=step.get("target") or {}))
    return tc


def _upsert_suite(db, project_id: str, type_key: str, short: str,
                  cases: list[TestCase]) -> TestSuite:
    """One Golden Path suite per type, reused across rebuilds. Dynamic (a saved
    tag query) so it stays correct, with static members for an explicit run."""
    name = f"Golden Path · {short}"
    suite = db.execute(
        select(TestSuite).where(TestSuite.project_id == project_id, TestSuite.name == name)
    ).scalar_one_or_none()
    if suite is None:
        suite = TestSuite(
            project_id=project_id, name=name, kind="dynamic",
            query={"tags": ["golden-path", type_key]},
            description=f"Golden Path {short} tests",
        )
        db.add(suite)
        db.flush()
    else:
        for m in list(suite.members):
            db.delete(m)
        db.flush()
    for pos, tc in enumerate(cases):
        db.add(SuiteMember(suite_id=suite.id, test_case_id=tc.id, position=pos))
    db.flush()
    return suite
