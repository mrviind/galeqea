"""Compile stored test cases into a runner execution plan.

Compilation is where the App Model earns its keep: a step stores an *intent* and
an ``element_id``; the ladder of concrete locators is assembled here, at dispatch
time, from whatever the element currently knows. A heal approved five minutes ago
is therefore live in the very next run with no test edits at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AppElement, Project, Run, StepAction, TestCase, TestCategory, TestStep

#: Steps that only exist for a human reader; never dispatched to a browser.
NON_EXECUTABLE = {StepAction.NOTE}


@dataclass(slots=True)
class CompiledTest:
    id: str
    test_case_id: str
    key: str
    title: str
    steps: list[dict]
    attempt: int = 1
    #: Per-test auth, injected for a page behind a login (form → storageState,
    #: basic/digest → httpCredentials). Plaintext only in this transient plan.
    storage_state: dict | None = None
    http_credentials: dict | None = None

    def as_dict(self) -> dict:
        out = {
            "id": self.id,
            "testCaseId": self.test_case_id,
            "key": self.key,
            "title": self.title,
            "steps": self.steps,
            "attempt": self.attempt,
        }
        if self.storage_state is not None:
            out["storageState"] = self.storage_state
        if self.http_credentials is not None:
            out["httpCredentials"] = self.http_credentials
        return out


class PlanCompiler:
    def __init__(self, db: Session):
        self.db = db
        self._element_cache: dict[str, AppElement] = {}
        #: Seeds per-run-unique generated values. Set when a run is compiled.
        self.run_id: str = ""

    # ------------------------------------------------------------------ #
    def compile_run(
        self, run: Run, test_cases: list[TestCase], *, artifacts_dir: str
    ) -> dict[str, Any]:
        self.run_id = run.id
        project = self.db.get(Project, run.project_id)
        base_url = run.base_url or self._base_url(project, run.environment)

        auth = _auth_for_run(run)
        compiled: list[CompiledTest] = []
        skipped: list[dict] = []
        for tc in test_cases:
            if tc.category != TestCategory.AUTOMATED:
                skipped.append({"key": tc.key, "reason": f"category is {tc.category}"})
                continue
            steps = self.compile_steps(tc)
            if not steps:
                skipped.append({"key": tc.key, "reason": "no executable steps"})
                continue
            # A page behind a login needs its matching credential injected. If
            # none exists, the test is skipped with a reason (never failed).
            ss, http, skip = _resolve_test_auth(tc, auth, run.project_id)
            if skip:
                skipped.append({"key": tc.key, "reason": skip, "auth_gated": True})
                continue
            compiled.append(
                CompiledTest(
                    id=f"{run.id}:{tc.id}",
                    test_case_id=tc.id,
                    key=tc.key,
                    title=tc.title,
                    steps=steps,
                    storage_state=ss,
                    http_credentials=http,
                )
            )

        settings_blob = (project.settings if project else {}) or {}
        plan = {
            "runId": run.id,
            "baseUrl": base_url,
            "browsers": run.browsers or ["chromium"],
            "parallelism": settings_blob.get("parallelism", 2),
            "headless": (run.ci_metadata or {}).get("headless", settings_blob.get("headless", True)),
            "trace": settings_blob.get("trace", True),
            "video": settings_blob.get("video", False),
            "viewport": settings_blob.get("viewport", {"width": 1440, "height": 900}),
            "defaultTimeoutMs": settings_blob.get("default_timeout_ms", 30000),
            "artifactsDir": artifacts_dir,
            "tests": [c.as_dict() for c in compiled],
            "_skipped": skipped,
        }
        rate = (run.ci_metadata or {}).get("rate_limit_rps")
        if rate:
            plan["rateLimitRps"] = rate
        return plan

    # ------------------------------------------------------------------ #
    def compile_steps(self, test_case: TestCase) -> list[dict]:
        out: list[dict] = []
        for step in sorted(test_case.steps, key=lambda s: s.index):
            if step.action in NON_EXECUTABLE:
                continue
            if step.action == StepAction.SCRIPT and not (step.options or {}).get("approved"):
                # A script step whose body has not been approved is dropped
                # rather than silently executed. The run reports it as blocked.
                continue
            out.append(
                {
                    "id": step.id,
                    "action": step.action,
                    "intent": step.intent,
                    "expected": step.expected,
                    "element_id": step.element_id,
                    "target": self._compile_target(step),
                    "value": self._compile_value(step),
                    "options": step.options or {},
                    "timeout_ms": step.timeout_ms,
                    "optional": step.optional,
                    "continue_on_failure": step.continue_on_failure,
                }
            )
        return out

    def _compile_value(self, step: TestStep) -> dict:
        """Resolve any generator reference into a concrete value.

        A step may carry ``{"generate": {...}}`` instead of a literal - recorded
        credential fields do, and so do generated cases that want realistic data.
        Resolution happens here, at plan time, for two reasons: the runner stays
        a dumb executor with no data-generation logic in it, and the run record
        shows the exact value that was used, so a failure is reproducible.
        """
        value = dict(step.value or {})
        generator = value.get("generate")
        if not isinstance(generator, dict):
            return value

        from ..intelligence import testdata

        field = generator.get("field") or ""
        kind = generator.get("kind") or testdata.infer_kind(field)
        # Seeded on the step id unless the caller asked for per-run uniqueness,
        # so re-running a test twice sends the same value and a failure can be
        # reproduced exactly.
        seed = f"{self.run_id}/{step.id}" if generator.get("unique") else f"{field or kind}/{step.id}"
        resolved = testdata.value(
            kind, seed,
            locale=generator.get("locale", "en-US"),
            constraints=generator.get("constraints"),
        )
        return {**{k: v for k, v in value.items() if k != "generate"},
                "text": resolved,
                "_generated": {"kind": kind, "field": field}}

    def _compile_target(self, step: TestStep) -> dict:
        target = dict(step.target or {})
        ladder: list[dict] = list(target.get("ladder") or [])

        element = self._element(step.element_id) if step.element_id else None
        if element:
            # App Model rungs go first: they carry the approved healing history.
            model_rungs = [r for r in (element.locators or []) if r not in ladder]
            ladder = [*model_rungs, *ladder]
            target.setdefault("role", element.role)
            target.setdefault("accessible_name", element.accessible_name)
            target.setdefault("tag", element.tag)
            fp = element.fingerprint or {}
            target.setdefault("test_id", (fp.get("attrs") or {}).get("data-testid", ""))
            target.setdefault("fingerprint", fp)
            target.setdefault("bounding_box", element.bounding_box or {})

        # Derive a role rung from the intent when authoring left none - the most
        # common cause of an unrunnable generated test is a missing fallback.
        if not ladder and target.get("role") and target.get("accessible_name"):
            ladder = [{"kind": "role", "role": target["role"], "name": target["accessible_name"]}]

        target["ladder"] = _dedupe(ladder)[:6]
        return target

    def _element(self, element_id: str) -> AppElement | None:
        if element_id not in self._element_cache:
            found = self.db.get(AppElement, element_id)
            if found is None:
                return None
            self._element_cache[element_id] = found
        return self._element_cache[element_id]

    @staticmethod
    def _base_url(project: Project | None, environment: str) -> str:
        if not project:
            return ""
        envs = project.environments or {}
        return envs.get(environment) or envs.get(project.default_environment) or ""


def _dedupe(rungs: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for rung in rungs:
        key = f"{rung.get('kind')}:{rung.get('role', '')}:{rung.get('name', '')}:{rung.get('value', '')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(rung)
    return out


def select_tests(db: Session, project_id: str, selection: dict) -> list[TestCase]:
    """Resolve a selection descriptor into concrete test cases.

    Accepts anything the chat layer might produce: explicit ids, keys, tags, a
    category, a status filter, or a free-text match against titles.
    """
    from ..models import TestStatus

    stmt = select(TestCase).where(
        TestCase.project_id == project_id,
        TestCase.status == TestStatus.APPROVED,
    )

    if ids := selection.get("test_ids"):
        stmt = select(TestCase).where(TestCase.id.in_(ids))
    elif keys := selection.get("keys"):
        stmt = stmt.where(TestCase.key.in_(keys))

    rows = list(db.execute(stmt).scalars())

    if tags := selection.get("tags"):
        wanted = {t.lower() for t in tags}
        rows = [r for r in rows if wanted & {t.lower() for t in (r.tags or [])}]
    if category := selection.get("category"):
        rows = [r for r in rows if r.category == category]
    if priority := selection.get("priority"):
        rows = [r for r in rows if r.priority == priority]
    if text := selection.get("text"):
        needle = text.lower()
        rows = [
            r for r in rows
            if needle in r.title.lower()
            or needle in (r.description or "").lower()
            or any(needle in t.lower() for t in (r.tags or []))
        ]
    if selection.get("exclude_quarantined", True):
        rows = [r for r in rows if not r.quarantined]

    return rows


# --------------------------------------------------------------------------- #
# Per-test authentication
# --------------------------------------------------------------------------- #
def _auth_for_run(run: Run) -> dict | None:
    """The auth bundle attached to a run ({credentials, requirements}), or None.
    The login-handoff marker ({login_handoff:…}) is not a credential bundle."""
    auth = (run.ci_metadata or {}).get("auth")
    if isinstance(auth, dict) and auth.get("credentials"):
        return auth
    return None


def _match_credential(bundle: dict, kind: str, path: str) -> dict | None:
    best, best_len = None, -1
    for e in bundle.get("credentials", []):
        if e.get("kind") != kind:
            continue
        scope = e.get("scope", "") or ""
        if scope and not path.startswith(scope):
            continue
        if len(scope) > best_len:
            best, best_len = e, len(scope)
    return best


def _resolve_test_auth(tc, bundle: dict | None, project_id: str):
    """(storage_state, http_credentials, skip_reason) for a test. A page whose
    required auth kind has no matching credential returns a skip reason instead of
    being run (and later failing on a 401)."""
    import json
    from urllib.parse import urlparse

    prov = getattr(tc, "provenance", None) or {}
    page = prov.get("page", "")
    path = urlparse(page).path or "/"
    kind = prov.get("auth_kind") or (bundle or {}).get("requirements", {}).get(path)
    if not kind:
        return None, None, None
    entry = _match_credential(bundle or {"credentials": []}, kind, path)
    if entry is None:
        return None, None, f"auth-gated ({kind}): add credentials"
    from ..core import vault
    try:
        if kind == "form" and entry.get("session_sealed"):
            return json.loads(vault.unseal(entry["session_sealed"], aad=f"auth:{project_id}")), None, None
        if kind == "form":  # credential stored but not logged in yet
            return None, None, f"auth-gated (form): sign in first ({entry.get('username_hint', '')})"
        creds = json.loads(vault.unseal(entry["creds_sealed"], aad=f"creds:{project_id}"))
        return None, {"username": creds["username"], "password": creds["password"]}, None
    except Exception:  # noqa: BLE001 (a bad key must not fail the whole compile)
        return None, None, f"auth-gated ({kind}): credential could not be read"
