"""CI/CD integrations: fetch and normalise build reports.

Four providers, one normalised shape. The parsers matter more than the API
clients - a JUnit XML from Jenkins and a Playwright JSON from GitHub Actions
describe the same reality in incompatible schemas, and triage, RCA and flake
scoring all need one shape to work against.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import httpx

from .base import Connection, IntegrationError, load_connection, safe_error


@dataclass(slots=True)
class NormalisedReport:
    provider: str
    reference: str
    status: str = "unknown"
    total: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    duration_ms: int = 0
    failures: list[dict] = field(default_factory=list)
    url: str = ""
    commit: str = ""
    branch: str = ""

    def as_dict(self) -> dict:
        return {
            "provider": self.provider, "reference": self.reference, "status": self.status,
            "totals": {"total": self.total, "passed": self.passed,
                       "failed": self.failed, "skipped": self.skipped},
            "duration_ms": self.duration_ms, "failures": self.failures,
            "url": self.url, "commit": self.commit, "branch": self.branch,
        }


# --------------------------------------------------------------------------- #
# Parsers
# --------------------------------------------------------------------------- #
def parse_junit(xml_text: str) -> NormalisedReport:
    report = NormalisedReport(provider="junit", reference="")
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise IntegrationError(f"could not parse JUnit XML: {exc}") from exc

    suites = [root] if root.tag == "testsuite" else root.findall(".//testsuite")
    for suite in suites:
        for case in suite.findall("testcase"):
            report.total += 1
            name = f"{case.get('classname', '')}.{case.get('name', '')}".strip(".")
            report.duration_ms += int(float(case.get("time", 0) or 0) * 1000)

            failure = case.find("failure") if case.find("failure") is not None else case.find("error")
            if failure is not None:
                report.failed += 1
                report.failures.append({
                    "name": name,
                    "type": failure.get("type", ""),
                    "message": (failure.get("message") or "")[:1000],
                    "detail": (failure.text or "")[:3000],
                })
            elif case.find("skipped") is not None:
                report.skipped += 1
            else:
                report.passed += 1
    report.status = "failed" if report.failed else "passed"
    return report


def parse_playwright_json(payload: dict) -> NormalisedReport:
    report = NormalisedReport(provider="playwright", reference="")

    def walk(suite: dict, prefix: str = "") -> None:
        title = f"{prefix} › {suite.get('title', '')}".strip(" ›")
        for spec in suite.get("specs", []):
            report.total += 1
            spec_title = f"{title} › {spec.get('title', '')}".strip(" ›")
            tests = spec.get("tests", [])
            ok = spec.get("ok", False)
            results = [r for t in tests for r in t.get("results", [])]
            report.duration_ms += sum(r.get("duration", 0) for r in results)
            if ok:
                report.passed += 1
            elif any(r.get("status") == "skipped" for r in results):
                report.skipped += 1
            else:
                report.failed += 1
                error = next((r.get("error") or {} for r in results if r.get("error")), {})
                report.failures.append({
                    "name": spec_title,
                    "type": "playwright",
                    "message": (error.get("message") or "")[:1000],
                    "detail": (error.get("stack") or "")[:3000],
                })
        for child in suite.get("suites", []):
            walk(child, title)

    for suite in payload.get("suites", []):
        walk(suite)
    report.status = "failed" if report.failed else "passed"
    return report


def parse_report(content: str) -> NormalisedReport:
    """Detect the format rather than requiring the caller to declare it."""
    stripped = content.strip()
    if stripped.startswith("<"):
        return parse_junit(stripped)
    try:
        payload = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise IntegrationError(
            "unrecognised report format - expected JUnit XML or Playwright/Allure JSON"
        ) from exc
    if "suites" in payload:
        return parse_playwright_json(payload)
    if isinstance(payload, list) and payload and "status" in payload[0]:
        return _parse_allure(payload)
    raise IntegrationError("JSON report did not match a known schema")


def _parse_allure(items: list[dict]) -> NormalisedReport:
    report = NormalisedReport(provider="allure", reference="")
    for item in items:
        report.total += 1
        status = (item.get("status") or "").lower()
        report.duration_ms += int(item.get("time", {}).get("duration", 0))
        if status == "passed":
            report.passed += 1
        elif status in {"skipped", "unknown"}:
            report.skipped += 1
        else:
            report.failed += 1
            report.failures.append({
                "name": item.get("name", ""),
                "type": status,
                "message": (item.get("statusMessage") or "")[:1000],
                "detail": (item.get("statusTrace") or "")[:3000],
            })
    report.status = "failed" if report.failed else "passed"
    return report


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
async def fetch_report(db, *, project_id: str, provider: str, reference: str) -> dict:
    connection = load_connection(db, project_id=project_id, provider=provider)
    fetcher = {
        "jenkins": _jenkins,
        "github_actions": _github_actions,
        "gitlab_ci": _gitlab,
        "azure_devops": _azure,
    }.get(provider)
    if fetcher is None:
        raise IntegrationError(f"unsupported CI provider {provider!r}")

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
        report = await fetcher(client, connection, reference)
    return report.as_dict()


async def _jenkins(client: httpx.AsyncClient, connection: Connection, reference: str):
    base = connection.require("base_url").rstrip("/")
    job = connection.require("job")
    auth = (connection.config.get("username", ""), connection.secret("api_token"))

    url = f"{base}/job/{job}/{reference}/testReport/api/json"
    response = await client.get(url, auth=auth)
    if response.status_code >= 400:
        raise safe_error(response, provider="Jenkins")

    body = response.json()
    report = NormalisedReport(provider="jenkins", reference=reference, url=f"{base}/job/{job}/{reference}/")
    report.passed = body.get("passCount", 0)
    report.failed = body.get("failCount", 0)
    report.skipped = body.get("skipCount", 0)
    report.total = report.passed + report.failed + report.skipped
    report.duration_ms = int(float(body.get("duration", 0)) * 1000)
    for suite in body.get("suites", []):
        for case in suite.get("cases", []):
            if case.get("status") in {"FAILED", "REGRESSION"}:
                report.failures.append({
                    "name": f"{case.get('className', '')}.{case.get('name', '')}",
                    "type": case.get("status", ""),
                    "message": (case.get("errorDetails") or "")[:1000],
                    "detail": (case.get("errorStackTrace") or "")[:3000],
                })
    report.status = "failed" if report.failed else "passed"
    return report


async def _github_actions(client: httpx.AsyncClient, connection: Connection, reference: str):
    repo = connection.require("repo")  # owner/name
    token = connection.secret("token")
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28"}

    response = await client.get(
        f"https://api.github.com/repos/{repo}/actions/runs/{reference}", headers=headers
    )
    if response.status_code >= 400:
        raise safe_error(response, provider="GitHub Actions")
    run = response.json()

    report = NormalisedReport(
        provider="github_actions", reference=reference,
        status="passed" if run.get("conclusion") == "success" else "failed",
        url=run.get("html_url", ""), commit=run.get("head_sha", ""),
        branch=run.get("head_branch", ""),
    )

    jobs = await client.get(
        f"https://api.github.com/repos/{repo}/actions/runs/{reference}/jobs", headers=headers
    )
    if jobs.status_code < 400:
        for job in jobs.json().get("jobs", []):
            report.total += 1
            if job.get("conclusion") == "success":
                report.passed += 1
            elif job.get("conclusion") in {"skipped", "cancelled"}:
                report.skipped += 1
            else:
                report.failed += 1
                failed_step = next(
                    (s["name"] for s in job.get("steps", []) if s.get("conclusion") == "failure"),
                    "",
                )
                report.failures.append({
                    "name": job.get("name", ""),
                    "type": job.get("conclusion", ""),
                    "message": f"step '{failed_step}' failed" if failed_step else "job failed",
                    "detail": job.get("html_url", ""),
                })
    return report


async def _gitlab(client: httpx.AsyncClient, connection: Connection, reference: str):
    base = connection.config.get("base_url", "https://gitlab.com").rstrip("/")
    project = connection.require("project_id")
    headers = {"PRIVATE-TOKEN": connection.secret("token")}

    response = await client.get(
        f"{base}/api/v4/projects/{project}/pipelines/{reference}/test_report", headers=headers
    )
    if response.status_code >= 400:
        raise safe_error(response, provider="GitLab CI")
    body = response.json()

    report = NormalisedReport(
        provider="gitlab_ci", reference=reference,
        total=body.get("total_count", 0), passed=body.get("success_count", 0),
        failed=body.get("failed_count", 0), skipped=body.get("skipped_count", 0),
        duration_ms=int(float(body.get("total_time", 0)) * 1000),
    )
    for suite in body.get("test_suites", []):
        for case in suite.get("test_cases", []):
            if case.get("status") == "failed":
                report.failures.append({
                    "name": case.get("name", ""), "type": "failed",
                    "message": (case.get("system_output") or "")[:1000],
                    "detail": (case.get("stack_trace") or "")[:3000],
                })
    report.status = "failed" if report.failed else "passed"
    return report


async def _azure(client: httpx.AsyncClient, connection: Connection, reference: str):
    import base64

    org = connection.require("organization")
    project = connection.require("project")
    token = connection.secret("token")
    auth = base64.b64encode(f":{token}".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}

    response = await client.get(
        f"https://dev.azure.com/{org}/{project}/_apis/test/runs/{reference}/results",
        params={"api-version": "7.1"}, headers=headers,
    )
    if response.status_code >= 400:
        raise safe_error(response, provider="Azure DevOps")

    report = NormalisedReport(provider="azure_devops", reference=reference)
    for result in response.json().get("value", []):
        report.total += 1
        outcome = (result.get("outcome") or "").lower()
        report.duration_ms += int(result.get("durationInMs", 0))
        if outcome == "passed":
            report.passed += 1
        elif outcome in {"notexecuted", "skipped"}:
            report.skipped += 1
        else:
            report.failed += 1
            report.failures.append({
                "name": result.get("testCaseTitle", ""), "type": outcome,
                "message": (result.get("errorMessage") or "")[:1000],
                "detail": (result.get("stackTrace") or "")[:3000],
            })
    report.status = "failed" if report.failed else "passed"
    return report
