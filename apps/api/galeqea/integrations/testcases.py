"""Push test cases to external test management systems.

Four targets, four completely different data models. The shared shape below is
what they agree on — and it is deliberately the IEEE 829 / ISTQB test case
structure, because every one of these tools is an implementation of it:

    id · title · preconditions · ordered (action, data, expected) steps ·
    priority · requirement links · labels

The adapters translate that into each system's own idea of a test case:

* **Xray Cloud** — a Jira *issue* of type Test, created through the GraphQL
  ``createTest`` mutation with native `steps { action data result }`.
* **Zephyr Scale** — not a Jira issue: its own object linked to Jira. Created in
  two calls, because the API takes the case and its steps separately.
* **Azure DevOps** — a *work item* of type "Test Case" whose steps live in a
  custom XML blob in ``Microsoft.VSTS.TCM.Steps``.
* **TestRail** — a case inside a section, steps as ``custom_steps_separated``.

Every one of these leaves the building, so every one is behind the approval gate.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

from sqlalchemy.orm import Session

from .base import Connection, IntegrationError, http_client, load_connection, safe_error

TARGETS = ("xray", "zephyr_scale", "azure_devops", "testrail")


@dataclass(slots=True)
class Step:
    action: str
    expected: str = ""
    data: str = ""


@dataclass(slots=True)
class PortableTestCase:
    """The IEEE 829 shape every one of these tools implements."""

    key: str
    title: str
    objective: str = ""
    preconditions: list[str] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    priority: str = "medium"
    labels: list[str] = field(default_factory=list)
    requirement_refs: list[str] = field(default_factory=list)
    folder: str = ""

    @classmethod
    def from_model(cls, case) -> PortableTestCase:
        return cls(
            key=case.key,
            title=case.title,
            objective=case.rationale or case.description,
            preconditions=list(case.preconditions or []),
            steps=[
                Step(
                    action=step.intent or step.action,
                    expected=step.expected or "",
                    data=json.dumps(step.value) if step.value else "",
                )
                for step in sorted(case.steps, key=lambda s: s.index)
            ],
            priority=case.priority,
            labels=list(case.tags or []),
            requirement_refs=list(case.requirement_refs or []),
            folder=case.category,
        )


# --------------------------------------------------------------------------- #
# Xray Cloud — GraphQL createTest
# --------------------------------------------------------------------------- #
CREATE_TEST = """
mutation CreateTest($testType: UpdateTestTypeInput!, $steps: [CreateStepInput], $jira: JSON!) {
  createTest(testType: $testType, steps: $steps, jira: $jira) {
    test { issueId jira(fields: ["key"]) }
    warnings
  }
}
"""


def push_xray(db: Session, connection: Connection, cases: list[PortableTestCase]) -> dict:
    from .xray import BASE_URL, authenticate

    token = authenticate(db, connection)
    project_key = connection.config.get("project_key") or connection.require("project_key")

    created, warnings = [], []
    with http_client() as client:
        for case in cases:
            variables = {
                "testType": {"name": "Manual"},
                "steps": [
                    {"action": s.action[:1000], "data": s.data[:1000], "result": s.expected[:1000]}
                    for s in case.steps
                ] or None,
                "jira": {
                    "fields": {
                        "summary": f"{case.key} {case.title}"[:250],
                        "project": {"key": project_key},
                        "description": _plain_description(case),
                        "labels": [_label(tag) for tag in case.labels][:10],
                    }
                },
            }
            response = client.post(
                f"{BASE_URL}/graphql",
                json={"query": CREATE_TEST, "variables": variables},
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            )
            if response.status_code >= 400:
                raise safe_error(response, provider="Xray")

            body = response.json()
            # GraphQL answers 200 with an `errors` array; treating that as
            # success is the classic way to "successfully" push nothing.
            if body.get("errors"):
                raise IntegrationError(
                    f"Xray rejected {case.key}: {body['errors'][0].get('message', 'unknown error')}"
                )
            payload = (body.get("data") or {}).get("createTest") or {}
            issue = (payload.get("test") or {}).get("jira") or {}
            created.append({"key": case.key, "remote_key": issue.get("key"),
                            "id": (payload.get("test") or {}).get("issueId")})
            warnings.extend(payload.get("warnings") or [])

    return {"ok": True, "provider": "xray", "created": created, "warnings": warnings}


# --------------------------------------------------------------------------- #
# Zephyr Scale — its own object model, linked to Jira
# --------------------------------------------------------------------------- #
def push_zephyr(db: Session, connection: Connection, cases: list[PortableTestCase]) -> dict:
    base = connection.config.get("base_url", "https://api.zephyrscale.smartbear.com/v2").rstrip("/")
    project_key = connection.require("project_key")
    headers = {"Authorization": f"Bearer {connection.secret('api_token')}",
               "Content-Type": "application/json"}

    created = []
    with http_client() as client:
        for case in cases:
            response = client.post(f"{base}/testcases", headers=headers, json={
                "projectKey": project_key,
                "name": f"{case.key} {case.title}"[:255],
                "objective": case.objective[:2000],
                "precondition": "\n".join(case.preconditions)[:2000] or None,
                "priorityName": _zephyr_priority(case.priority),
                "labels": [_label(tag) for tag in case.labels][:10],
            })
            if response.status_code >= 400:
                raise safe_error(response, provider="Zephyr Scale")
            key = response.json().get("key")

            # Steps are a second call: the case endpoint does not accept them.
            if case.steps and key:
                steps = client.post(f"{base}/testcases/{key}/teststeps", headers=headers, json={
                    "mode": "OVERWRITE",
                    "items": [
                        {"inline": {
                            "description": s.action[:2000],
                            "testData": s.data[:2000],
                            "expectedResult": s.expected[:2000],
                        }}
                        for s in case.steps
                    ],
                })
                if steps.status_code >= 400:
                    raise safe_error(steps, provider="Zephyr Scale (steps)")
            created.append({"key": case.key, "remote_key": key})

    return {"ok": True, "provider": "zephyr_scale", "created": created}


def _zephyr_priority(priority: str) -> str:
    return {"critical": "High", "high": "High", "medium": "Normal", "low": "Low"}.get(
        priority, "Normal"
    )


# --------------------------------------------------------------------------- #
# Azure DevOps — a work item whose steps are a custom XML blob
# --------------------------------------------------------------------------- #
def steps_to_azure_xml(steps: list[Step]) -> str:
    """Render steps into ``Microsoft.VSTS.TCM.Steps``.

    The format is strict and unforgiving: every ``<step>`` needs exactly two
    ``<parameterizedString>`` children, the inner HTML must be escaped *twice*
    (once as HTML inside the string, once as XML), and a stray ampersand fails
    validation rather than degrading. ``ValidateStep`` is used when the step has
    an expected result, ``ActionStep`` when it does not.
    """
    parts = [f'<steps id="0" last="{len(steps)}">']
    for index, step in enumerate(steps, start=1):
        step_type = "ValidateStep" if step.expected else "ActionStep"
        action = _azure_cell(step.action + (f"\n[data] {step.data}" if step.data else ""))
        expected = _azure_cell(step.expected)
        parts.append(
            f'<step id="{index}" type="{step_type}">'
            f'<parameterizedString isformatted="true">{action}</parameterizedString>'
            f'<parameterizedString isformatted="true">{expected}</parameterizedString>'
            f"<description/></step>"
        )
    parts.append("</steps>")
    return "".join(parts)


def _azure_cell(text: str) -> str:
    """Escape once for the HTML body, again for the XML attribute-like slot."""
    body = escape(text or "").replace("\n", "<br/>")
    return escape(f"<DIV><P>{body}</P></DIV>")


def push_azure(db: Session, connection: Connection, cases: list[PortableTestCase]) -> dict:
    import base64

    organization = connection.require("organization")
    project = connection.require("project")
    token = connection.secret("token")
    auth = base64.b64encode(f":{token}".encode()).decode()
    headers = {
        "Authorization": f"Basic {auth}",
        # Creating a work item requires this exact content type, not application/json.
        "Content-Type": "application/json-patch+json",
    }
    url = (
        f"https://dev.azure.com/{organization}/{project}"
        "/_apis/wit/workitems/$Test%20Case?api-version=7.1"
    )

    created = []
    with http_client() as client:
        for case in cases:
            patch = [
                {"op": "add", "path": "/fields/System.Title",
                 "value": f"{case.key} {case.title}"[:255]},
                {"op": "add", "path": "/fields/Microsoft.VSTS.TCM.Steps",
                 "value": steps_to_azure_xml(case.steps)},
                {"op": "add", "path": "/fields/Microsoft.VSTS.Common.Priority",
                 "value": _azure_priority(case.priority)},
                {"op": "add", "path": "/fields/System.Description",
                 "value": _html_description(case)},
            ]
            if case.labels:
                patch.append({"op": "add", "path": "/fields/System.Tags",
                              "value": "; ".join(_label(tag) for tag in case.labels)})

            response = client.post(url, headers=headers, json=patch)
            if response.status_code >= 400:
                raise safe_error(response, provider="Azure DevOps")
            body = response.json()
            created.append({
                "key": case.key,
                "remote_key": str(body.get("id")),
                "url": (body.get("_links", {}).get("html", {}) or {}).get("href", ""),
            })

    return {"ok": True, "provider": "azure_devops", "created": created}


def _azure_priority(priority: str) -> int:
    return {"critical": 1, "high": 2, "medium": 3, "low": 4}.get(priority, 3)


# --------------------------------------------------------------------------- #
# TestRail — a case inside a section
# --------------------------------------------------------------------------- #
def push_testrail(db: Session, connection: Connection, cases: list[PortableTestCase]) -> dict:
    base = connection.require("base_url").rstrip("/")
    section_id = connection.require("section_id")
    auth = (connection.require("username"), connection.secret("api_key"))

    created = []
    with http_client() as client:
        for case in cases:
            response = client.post(
                f"{base}/index.php?/api/v2/add_case/{section_id}",
                auth=auth,
                headers={"Content-Type": "application/json"},
                json={
                    "title": f"{case.key} {case.title}"[:250],
                    "template_id": 2,          # "Test Case (Steps)"
                    "priority_id": _testrail_priority(case.priority),
                    "custom_preconds": "\n".join(case.preconditions),
                    "custom_steps_separated": [
                        {"content": s.action[:5000], "expected": s.expected[:5000]}
                        for s in case.steps
                    ],
                    "refs": ", ".join(case.requirement_refs)[:250],
                },
            )
            if response.status_code >= 400:
                raise safe_error(response, provider="TestRail")
            body = response.json()
            created.append({"key": case.key, "remote_key": str(body.get("id"))})

    return {"ok": True, "provider": "testrail", "created": created}


def _testrail_priority(priority: str) -> int:
    return {"critical": 4, "high": 3, "medium": 2, "low": 1}.get(priority, 2)


# --------------------------------------------------------------------------- #
def push(db: Session, *, project_id: str, target: str, cases: list[PortableTestCase]) -> dict:
    if target not in TARGETS:
        raise IntegrationError(
            f"unsupported target {target!r}; available: {', '.join(TARGETS)}"
        )
    if not cases:
        raise IntegrationError("no test cases were selected")

    connection = load_connection(db, project_id=project_id, provider=target)
    handler = {
        "xray": push_xray,
        "zephyr_scale": push_zephyr,
        "azure_devops": push_azure,
        "testrail": push_testrail,
    }[target]
    result = handler(db, connection, cases)
    result["count"] = len(result.get("created", []))
    return result


def _plain_description(case: PortableTestCase) -> str:
    lines = [case.objective] if case.objective else []
    if case.preconditions:
        lines.append("\nPreconditions:")
        lines += [f"- {p}" for p in case.preconditions]
    if case.requirement_refs:
        lines.append(f"\nRequirements: {', '.join(case.requirement_refs)}")
    lines.append("\nExported from QE Agent. Steps are held in the test's own step list.")
    return "\n".join(lines)[:30000]


def _html_description(case: PortableTestCase) -> str:
    parts = [f"<p>{escape(case.objective)}</p>"] if case.objective else []
    if case.preconditions:
        items = "".join(f"<li>{escape(p)}</li>" for p in case.preconditions)
        parts.append(f"<p><b>Preconditions</b></p><ul>{items}</ul>")
    if case.requirement_refs:
        parts.append(f"<p><b>Requirements:</b> {escape(', '.join(case.requirement_refs))}</p>")
    return "".join(parts)[:30000]


def _label(value: str) -> str:
    """Jira and Azure both reject labels containing whitespace."""
    return "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in value)[:50]
