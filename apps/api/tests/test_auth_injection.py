"""Per-test auth injection: a page behind a login gets its matching credential,
or is skipped with a reason (never failed on a 401)."""

from __future__ import annotations

import json

from galeqea.core import vault
from galeqea.engine.plan import PlanCompiler
from galeqea.models import Run, StepAction, TestCase, TestCategory, TestStatus, TestStep


def _gp_test(db, project, key, unit, page, auth_kind):
    tc = TestCase(project_id=project.id, key=key, title=f"functional: {unit}",
                  status=TestStatus.APPROVED, category=TestCategory.AUTOMATED,
                  provenance={"type": "functional", "unit": unit, "page": page,
                              "auth_kind": auth_kind})
    db.add(tc)
    db.flush()
    db.add(TestStep(test_case_id=tc.id, index=0, action=StepAction.GOTO,
                    intent="open", value={"url": page}))
    db.commit()
    return tc


def _run(db, project, key, bundle):
    run = Run(project_id=project.id, number=1, base_url="https://x.com",
              selection={"keys": [key]}, ci_metadata={"auth": bundle} if bundle else {})
    db.add(run)
    db.flush()
    return run


def test_auth_gated_unit_without_a_credential_is_skipped_not_failed(db, project, tmp_path):
    tc = _gp_test(db, project, "P-GP-FUNCTIONAL-05", "/basic_auth",
                  "https://x.com/basic_auth", "basic")
    run = _run(db, project, tc.key, {"credentials": [], "requirements": {"/basic_auth": "basic"}})
    plan = PlanCompiler(db).compile_run(run, [tc], artifacts_dir=str(tmp_path))
    assert plan["tests"] == []                    # never run → never a 401 failure
    skip = plan["_skipped"][0]
    assert skip["auth_gated"] and skip["reason"] == "auth-gated (basic): add credentials"


def test_basic_credential_is_injected_as_http_credentials(db, project, tmp_path):
    tc = _gp_test(db, project, "P-GP-FUNCTIONAL-06", "/basic_auth",
                  "https://x.com/basic_auth", "basic")
    sealed = vault.seal(json.dumps({"username": "admin", "password": "admin"}),
                        aad=f"creds:{project.id}")
    bundle = {"credentials": [{"kind": "basic", "scope": "", "creds_sealed": sealed}],
              "requirements": {"/basic_auth": "basic"}}
    plan = PlanCompiler(db).compile_run(_run(db, project, tc.key, bundle), [tc],
                                        artifacts_dir=str(tmp_path))
    assert plan["tests"][0]["httpCredentials"] == {"username": "admin", "password": "admin"}
    assert "storageState" not in plan["tests"][0]


def test_form_credential_injects_the_sealed_storage_state(db, project, tmp_path):
    tc = _gp_test(db, project, "P-GP-FUNCTIONAL-07", "/secure", "https://x.com/secure", "form")
    session = {"cookies": [{"name": "sess", "value": "x"}]}
    sealed = vault.seal(json.dumps(session), aad=f"auth:{project.id}")
    bundle = {"credentials": [{"kind": "form", "scope": "", "session_sealed": sealed}],
              "requirements": {"/secure": "form"}}
    plan = PlanCompiler(db).compile_run(_run(db, project, tc.key, bundle), [tc],
                                        artifacts_dir=str(tmp_path))
    assert plan["tests"][0]["storageState"] == session


def test_a_form_credential_without_a_session_asks_to_sign_in(db, project, tmp_path):
    tc = _gp_test(db, project, "P-GP-FUNCTIONAL-08", "/secure", "https://x.com/secure", "form")
    sealed = vault.seal(json.dumps({"username": "u", "password": "p"}), aad=f"creds:{project.id}")
    bundle = {"credentials": [{"kind": "form", "scope": "", "creds_sealed": sealed}],
              "requirements": {"/secure": "form"}}
    plan = PlanCompiler(db).compile_run(_run(db, project, tc.key, bundle), [tc],
                                        artifacts_dir=str(tmp_path))
    assert plan["tests"] == []
    assert "sign in first" in plan["_skipped"][0]["reason"]


def test_a_page_needing_no_auth_is_untouched(db, project, tmp_path):
    tc = _gp_test(db, project, "P-GP-FUNCTIONAL-09", "/", "https://x.com/", None)
    tc.provenance = {"type": "functional", "unit": "/", "page": "https://x.com/"}  # no auth_kind
    db.commit()
    plan = PlanCompiler(db).compile_run(_run(db, project, tc.key, None), [tc],
                                        artifacts_dir=str(tmp_path))
    assert len(plan["tests"]) == 1
    assert "httpCredentials" not in plan["tests"][0] and "storageState" not in plan["tests"][0]
