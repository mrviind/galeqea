"""Agent-facing context documents, shared by the HTTP and MCP surfaces.

``surface_context_markdown`` is the llms.txt-style map of the whole product (safe
without a project); ``project_context_markdown`` is the one-read briefing on a
single project's live state. Both are Markdown so an agent can read them directly,
and both are served two ways: over HTTP (`/api/ai/context`,
`/api/projects/{id}/ai/context`) and over MCP (`galeqea://{id}/context`).
"""

from __future__ import annotations

from sqlalchemy import func, select

from ..models import (
    ApprovalRequest,
    ApprovalStatus,
    Project,
    RequirementItem,
    Run,
    TestCase,
    TestStat,
)

SURFACE_CONTEXT = (
    "# GaleQEA: agent context\n\n"
    "GaleQEA is an open-source, AI-first test-automation agent. It explores a web app, "
    "plans and builds tests, runs them in real browsers, heals what breaks, and reports, "
    "on whatever LLM you configure, or none. Every state-changing action passes a human "
    "**approval gate**, and the agent can never approve its own work (`SelfApprovalError`).\n\n"
    "## How to talk to it\n"
    "- **HTTP API**: OpenAPI schema at `/api/openapi.json`; interactive docs at `/api/docs`. "
    "All resources live under `/api/projects/{project_id}/...`.\n"
    "- **MCP**: `galeqea mcp` speaks the Model Context Protocol over stdio: tools, resources "
    "and prompts mirror the API. Config snippet: `galeqea mcp-config`. Read "
    "`galeqea://{project_id}/context` first.\n"
    "- **CLI**: `galeqea run`, `galeqea export`, `galeqea reset`, `galeqea doctor`.\n\n"
    "## Reports (every one in three shapes)\n"
    "For a run: `/api/projects/{id}/runs/{run_id}/report.json | .md | .junit.xml`.\n"
    "Project-level: `/coverage`, `/traceability`, `/flaky`, `/heals`, and per run "
    "`/runs/{run_id}/rca`, each as `report.json` (stable `schema_version`, every item "
    "carries `id`/`href`/`ui_href`) and `report.md` (LLM-friendly, budgeted).\n"
    "Schemas: `docs/schemas/*.schema.json`; JUnit conforms to `docs/schemas/junit.xsd`.\n\n"
    "## Release management\n"
    "The object model is a single chain: Requirement → TestCase (versioned) → TestPlan → "
    "Cycle × Configuration → Result → DefectLink, rolled up to a Milestone/Release. "
    "Milestones carry exit criteria evaluated against live metrics into a Go/No-Go "
    "**readiness** verdict; a human approver signs off (immutable: an AI principal can "
    "never sign). Under `/api/projects/{id}/`: `milestones`, `environments`, "
    "`milestones/{id}/plans`, `plans/{id}/cycles`, `milestones/{id}/readiness`, "
    "`milestones/{id}/signoff`, and `milestones/{id}/metrics.json|.md`. Release report: "
    "`galeqea release report <version>` / `galeqea://{id}/releases/{version}/report`.\n\n"
    "## Defects\n"
    "File a tracked bug from a failing result with `POST /projects/{id}/results/{result_id}/defect` "
    "(gated), the `file_defect` tool, `galeqea defect file`, or chat \"file a bug for run #N <KEY>\". "
    "Jira Cloud is the primary tracker (ADF body, real attachments); GitHub/GitLab issues are the "
    "secondary path. Filing is idempotent by failure fingerprint, so a recurrence comments on the "
    "existing issue instead of duplicating. `GET /projects/{id}/defects` lists them; release "
    "readiness stops counting a failure as an open blocker once its linked defect is resolved.\n\n"
    "## Jira → tests\n"
    "Connect Jira (Settings → Integrations, or \"connect jira <site>\"; credentials sealed in the "
    "vault, never in chat). Import stories with `POST /projects/{id}/integrations/jira/import` "
    "(`{selector}` is a sprint, fixVersion, or JQL), the `import_jira_stories` tool, "
    "`galeqea jira import`, or \"import stories from open sprint\". Each story becomes a requirement "
    "keyed by its Jira key (source-anchored, idempotent); a changed description marks the linked "
    "tests stale (`/integrations/jira/stale`). Approved tests' coverage is written back to the "
    "story with `write_back_jira_coverage`.\n\n"
    "## Results → test management\n"
    "Push a finished run's results to Xray, Zephyr Scale or TestRail with "
    "`POST /projects/{id}/runs/{run_id}/push` (gated), the `push_results` tool, "
    "`galeqea results push`, or \"push run #N to xray plan <KEY>\". Results match each "
    "system's own identity (Xray key or stable definition, Zephyr PROJ-T key, TestRail "
    "case id) and the push is idempotent per run+target (`run_exports`), so re-pushing "
    "returns the stored execution key instead of duplicating.\n\n"
    "## Publish to Confluence\n"
    "Publish a release report as a Confluence page (tables + Go/No-Go status macros, "
    "labelled `test-report`) with `POST /projects/{id}/milestones/{mid}/publish` (gated), "
    "the `publish_release_report` tool, `galeqea release publish`, or \"publish release "
    "report 1.4 to confluence space QA\". Re-publishing updates the same page in place "
    "(version+1) so the link is stable.\n\n"
    "## Notifications\n"
    "Connect Slack (Incoming Webhook) or Microsoft Teams (connector) and GaleQEA posts a "
    "compact message when a subscribed event fires: a finished/failed run, a proposed "
    "heal, a queued approval, a signed-off milestone, a finished cycle. Tune it in "
    "Settings → Integrations or with \"notify slack on failures\" / \"on everything\".\n\n"
    "## Import from other tools\n"
    "Bring test assets in from another tool with `POST /projects/{id}/tests/import` "
    "(author+) or `galeqea import file <path>`: Gherkin `.feature` and TestRail/Xray/Zephyr "
    "**CSV** become PROPOSED test cases (a CSV first returns a suggested column mapping to "
    "confirm); **JUnit** XML becomes a historical run. Imported cases carry their origin as "
    "provenance and go through the normal review board.\n\n"
    "## Tester ergonomics\n"
    "Every failing result has a one-click **evidence bundle**. `GET "
    "/runs/{id}/results/{rid}/evidence.zip` (or `galeqea evidence <result>`) zips its "
    "screenshot/video/trace/console/HAR plus a metadata.json. The manual runner exposes "
    "`GET /runs/{id}/next-untested` and accepts a pasted attachment on a result. A tester "
    "can run a timeboxed **exploratory (SBTM) session** from chat: \"start exploratory "
    "session on checkout, 30 min\", then \"note: …\" / \"bug: …\" as they go, then \"end "
    "session\" for the report.\n\n"
    "## The rule\n"
    "Reads are free. Every write (create/update a test, apply a heal, push to Jira/Xray, "
    "open a PR, schedule a run, write memory, file a defect) returns a **proposal** that a human "
    "must approve. Propose freely; a person lets it out.\n"
)


def surface_context_markdown() -> str:
    return SURFACE_CONTEXT


def project_context_markdown(db, project: Project) -> str:
    """A single-read briefing on one project: counts, latest run, top gaps, flaky,
    pending approvals, and the tools available. Deterministic; kept well under an
    LLM context window."""
    from ..ai.toolset import tool_catalog
    from ..intelligence.coverage import compute
    from ..intelligence.flaky import assess

    pid = project.id
    n_reqs = db.execute(select(func.count()).select_from(RequirementItem).where(RequirementItem.project_id == pid)).scalar() or 0
    n_tests = db.execute(select(func.count()).select_from(TestCase).where(TestCase.project_id == pid)).scalar() or 0
    n_runs = db.execute(select(func.count()).select_from(Run).where(Run.project_id == pid)).scalar() or 0

    latest = db.execute(
        select(Run).where(Run.project_id == pid).order_by(Run.number.desc()).limit(1)
    ).scalars().first()

    cov = compute(db, pid, persist=False)
    gaps = [u for u in cov.get("uncovered", []) if u.get("risk") in {"critical", "high"}][:5]

    stats = {s.test_case_id: s for s in db.execute(select(TestStat).where(TestStat.project_id == pid)).scalars()}
    cases = {c.id: c for c in db.execute(select(TestCase).where(TestCase.project_id == pid)).scalars()}
    flaky = sorted(
        ((cases[cid].key, assess(s).score) for cid, s in stats.items()
         if cid in cases and assess(s).score >= 0.3),
        key=lambda kv: (-kv[1], kv[0]),
    )[:5]

    pending = db.execute(
        select(ApprovalRequest).where(
            ApprovalRequest.project_id == pid, ApprovalRequest.status == ApprovalStatus.PENDING,
        )
    ).scalars().all()

    tools = sorted(t["name"] for t in tool_catalog())

    lines = [
        f"# {project.name} ({project.key}): agent briefing",
        "",
        f"- Requirements: **{n_reqs}** · Tests: **{n_tests}** · Runs: **{n_runs}**",
        f"- Coverage: **{cov.get('coverage_pct', 0)}%** covered, "
        f"**{cov.get('automation_pct', 0)}%** automated",
        f"- Pending approvals: **{len(pending)}**",
        "",
        "## Latest run",
    ]
    if latest:
        t = latest.totals or {}
        lines.append(
            f"Run #{latest.number}: **{latest.status}** ({t.get('passed', 0)} passed, "
            f"{t.get('failed', 0)} failed) on `{latest.environment}`. "
            f"Report: `/api/projects/{pid}/runs/{latest.id}/report.md`"
        )
    else:
        lines.append("_No runs yet._")

    lines += ["", "## Top coverage gaps (high/critical)", ""]
    lines += [f"- **{g['ref']}** ({g.get('risk')}): {g.get('title', '')}" for g in gaps] or ["_None._"]

    lines += ["", "## Flakiest tests", ""]
    lines += [f"- {key}: score {score:.2f}" for key, score in flaky] or ["_None._"]

    lines += ["", "## Releases", ""]
    from ..models import Milestone
    from ..services import release as _release
    milestones = db.execute(
        select(Milestone).where(Milestone.project_id == pid, Milestone.status != "archived")
        .order_by(Milestone.created_at.desc()).limit(5)
    ).scalars().all()
    if milestones:
        for m in milestones:
            ev = _release.evaluate_readiness(db, m)
            verdict = ev["verdict"].upper().replace("_", "-")
            signed = f", signed off {m.signoff['decision']}" if m.signoff else ""
            lines.append(f"- **{m.version}** ({m.status}): readiness {verdict}, "
                         f"{len(m.exit_criteria or [])} exit criteria{signed}")
    else:
        lines.append("_No releases yet._")

    lines += [
        "", "## Available tools",
        "", "Reachable over MCP, HTTP and chat: " + ", ".join(f"`{t}`" for t in tools) + ".",
        "", "Reports for this project: "
        f"`/coverage`, `/traceability`, `/flaky`, `/heals` under `/api/projects/{pid}/` "
        "(each `report.json`/`report.md`).",
        "", "Every write is a proposal pending human approval.",
    ]
    return "\n".join(lines) + "\n"
