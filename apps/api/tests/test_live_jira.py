"""Opt-in live Jira smoke test: runs ONLY when GALEQEA_LIVE_JIRA=1.

It hits the real Atlassian site with credentials from the environment, so it never
runs in CI (the fixture-backed tests in test_story_import.py / test_defects.py cover
behaviour without a tenant). Never hard-code or log the token.

    GALEQEA_LIVE_JIRA=1 \
    GALEQEA_LIVE_JIRA_SITE=https://your.atlassian.net \
    GALEQEA_LIVE_JIRA_EMAIL=you@example.com \
    GALEQEA_LIVE_JIRA_TOKEN=*** \
    GALEQEA_LIVE_JIRA_PROJECT=XSP \
    pytest tests/test_live_jira.py -q
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("GALEQEA_LIVE_JIRA") != "1",
    reason="live Jira disabled (set GALEQEA_LIVE_JIRA=1 and the GALEQEA_LIVE_JIRA_* creds)")


def _connect(db, project):
    from galeqea.core.vault import seal
    from galeqea.models import IntegrationConnection, VaultSecret

    token = os.environ["GALEQEA_LIVE_JIRA_TOKEN"]
    secret = VaultSecret(project_id=project.id, name="jira.api_token", kind="jira")
    db.add(secret)
    db.flush()
    secret.ciphertext = seal(token, aad=f"{project.id}:jira.api_token")
    db.add(IntegrationConnection(
        project_id=project.id, provider="jira", enabled=True,
        config={"base_url": os.environ["GALEQEA_LIVE_JIRA_SITE"],
                "email": os.environ["GALEQEA_LIVE_JIRA_EMAIL"],
                "project_key": os.environ.get("GALEQEA_LIVE_JIRA_PROJECT", "")},
        secret_refs={"api_token": secret.id}))
    db.commit()


def test_live_verify_and_search(db, project):
    from galeqea.integrations import jira
    _connect(db, project)

    who = jira.verify(db, project_id=project.id)
    assert who["ok"] and who["account"]
    # projects list is best-effort; if present, our target should be visible
    keys = {p["key"] for p in who.get("projects", [])}
    target = os.environ.get("GALEQEA_LIVE_JIRA_PROJECT")
    if target and keys:
        assert target in keys, f"{target} not in accessible projects {sorted(keys)}"

    issues = jira.search_issues(
        db, project_id=project.id,
        jql=f'project = "{target}" ORDER BY created DESC', cap=5)
    assert isinstance(issues, list)  # may be empty, but the call must succeed
