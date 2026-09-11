# Surface parity

Every capability GaleQEA has should be reachable four ways: the **HTTP API**, the
**MCP** server (tool / resource / prompt), the **CLI**, and the **chat** agent.
This table is the audit. `✓` = available; `gated` = available but behind the human
approval gate; `human` = deliberately human-only (a machine principal can never
satisfy it); `n/a` = intentionally not offered on that surface (see notes).

| Capability | HTTP | MCP | CLI | Chat |
|---|---|---|---|---|
| Run tests | `POST /runs` | `run_tests` | `galeqea run` | ✓ |
| Re-run failed | `POST /runs/{id}/rerun` | `run_tests` (failed selection) | n/a | "rerun only failed" |
| List / get tests | `GET /tests`, `/tests/{id}` | `list_tests`, `get_test` | n/a | ✓ |
| Create / update test | `POST`/`PATCH /tests` (gated) | `create_test`, `update_test` (gated) | n/a | gated |
| Export test to code | n/a | `export_test` | `galeqea export` | ✓ |
| Get run | `GET /runs/{id}` | `get_run` | n/a | ✓ |
| **Run report** (JSON/MD/JUnit) | `GET /runs/{id}/report.{json,md,junit.xml}` | `get_run_report` + resource `runs/{id}/report` | n/a | "export last run as junit" |
| **Word test report** (.docx, branded, evidence) | `GET /runs/{id}/report.docx` | n/a | `galeqea report <run>` | Export ▾ → Word |
| **Full Floor** (analyse→plan→run→report) | via `POST /journeys` + plan approve + run | n/a | `galeqea test <url>` | "test &lt;url&gt;" |
| Schedule a run | `POST` (governance) | `schedule_run` | n/a | ✓ |
| List schedules | `GET` | `list_schedules` | n/a | ✓ |
| **Coverage report** | `GET /coverage/report.{json,md}` | `get_coverage` + resource `coverage` | n/a | ✓ |
| **Traceability report** | `GET /traceability/report.{json,md}` | resource `traceability` | n/a | ✓ |
| **Flaky report** | `GET /flaky/report.{json,md}` | `get_flaky_tests` + resource `flaky` | n/a | ✓ |
| **RCA report** | `GET /runs/{id}/rca/report.{json,md}` | `run_rca` + resource `rca/{run_id}` | n/a | ✓ |
| **Heals report** | `GET /heals/report.{json,md}` | `approve_heal` (gated) + resource `heals` | n/a | ✓ |
| Findings | via reports | `get_findings` | n/a | ✓ |
| Requirements (read) | `GET /requirements` | `query_requirements`, `list_requirements` | n/a | ✓ |
| Requirements (ingest) | `POST /requirements` (upload) | via upload | n/a | ✓ |
| Coverage plan | via `/coverage` | `plan_coverage` | n/a | ✓ |
| Coverage cost | n/a | `estimate_coverage_cost` | n/a | ✓ |
| Status brief | n/a | `test_status_brief` | n/a | ✓ |
| Quality retrospective | n/a | `quality_retrospective` | n/a | ✓ |
| Audit ledger | `GET` (governance) | `get_audit_trail` + resource `audit` | n/a | ✓ |
| **Artifact retention** | `PATCH /projects/{id}` (`settings.retention_days`) + daily sweep | n/a | `galeqea retention` | set via project settings |
| **Sign in / out** (multi-user) | `POST /api/auth/{login,logout}`, `GET /api/auth/me` | n/a | n/a | SPA Login view |
| **Scoped API tokens** | `GET`/`POST`/`DELETE /api/tokens` | token authenticates the client | token authenticates the client | Settings → API tokens |
| **GDPR export / erase** | `GET`/`POST /api/gdpr/users/{id}/{export,erase}` (admin) | n/a | n/a | n/a |
| Approvals: list | `GET /approvals` | resource `approvals` | n/a | ✓ |
| Approvals: decide | `POST /approvals/{id}` | **human** | n/a | **human** |
| **Golden Path plan: propose** | `POST /projects/{id}/journeys` {target} | (chat on-ramp) | `galeqea plan propose <url>` | "test &lt;url&gt;" |
| **Golden Path plan: approve** | `POST …/journeys/{jid}/plan/approve` (or `/approvals/{id}/decide`) | `approve_plan` (**human**) | `galeqea plan approve <url>` | "approve" |
| Push to Jira / Xray | `POST` (gated) | `create_jira_ticket`, `push_*` (gated) | n/a | gated |
| Open test PR | `POST` (gated) | `open_test_pull_request` (gated) | n/a | gated |
| **File a defect from a failure** | `POST /results/{id}/defect` (gated, author+) | `file_defect` (gated) | `galeqea defect file <result>` | "file a bug for run #N &lt;KEY&gt;" |
| **List tracked defects** | `GET /defects` | via chat | `galeqea defect list` | ✓ |
| **Defects for a result** | `GET /results/{id}/defects` | via chat | n/a | ✓ |
| **Refresh defect status** | `POST /defects/{id}/refresh` (author+) | via chat | n/a | ✓ |
| **Connect Jira** (secure) | `POST /integrations` (admin) + `/integrations/jira/verify` | connection authenticates the client | n/a | "connect jira &lt;site&gt;" (secure form) |
| **Import Jira stories** | `POST /integrations/jira/import` (author+) | `import_jira_stories` (gated) | `galeqea jira import <selector>` | "import stories from &lt;sprint/fixVersion/JQL&gt;" |
| **Detect stale stories** | `POST /integrations/jira/stale` (author+) | via chat | `galeqea jira stale` | "check stale stories" |
| **Write coverage back to Jira** | via approval | `write_back_jira_coverage` (gated) | n/a | "write back coverage for &lt;KEY&gt;" |
| **Push results to Xray/Zephyr/TestRail** | `POST /runs/{id}/push` (gated, author+) | `push_results` (gated) | `galeqea results push <run> --provider` | "push run #N to xray plan &lt;KEY&gt;" |
| **List a run's exports** | `GET /runs/{id}/exports` | via chat | n/a | ✓ |
| **Publish release report to Confluence** | `POST /milestones/{id}/publish` (gated, author+) | `publish_release_report` (gated) | `galeqea release publish <version>` | "publish release report &lt;v&gt; to confluence space &lt;KEY&gt;" |
| **Slack / Teams notifications** | `POST /integrations` (slack/teams) + `/{provider}/test`, `/{provider}/events` | connection carries the target | n/a | "connect slack" · "notify slack on failures" |
| **Import from another tool** (Gherkin/CSV/JUnit) | `POST /tests/import` (author+) | n/a (file upload) | `galeqea import file <path>` | Author → Import panel |
| **Evidence bundle** (zip per failure) | `GET /runs/{id}/results/{rid}/evidence.zip` | n/a | `galeqea evidence <result>` | Run detail → Evidence |
| **Manual runner: next untested** | `GET /runs/{id}/next-untested` | n/a | n/a | keyboard runner |
| **Background worker** (Postgres full profile) | n/a | n/a | `galeqea worker` | n/a |
| **Per-role model routing / ceilings** | `GET`/`POST /api/settings/role-models` (admin) | n/a | n/a | "use gpt-4o-mini for locating" · "cap locating at 2000 tokens" · Settings → Model (per-role) |
| **Attach to a manual result** | `POST /runs/{id}/results/{rid}/attachment` (author+) | n/a | n/a | paste in the runner |
| **Exploratory (SBTM) session** | `POST /exploration/manual`, `/manual/entry`, `/manual/end` | n/a | n/a | "start exploratory session on &lt;charter&gt;, N min" · "note: …" · "bug: …" · "end session" |
| **Releases: list** | `GET /milestones` (`?include_archived`) | `list_releases` + resource `releases` | `galeqea release list` | ✓ |
| **Release: create** | `POST /milestones` (author+) | `create_release` (gated) | n/a | "create release &lt;version&gt;" |
| **Release: exit criteria** | `PATCH /milestones/{id}/exit-criteria` | via `create_release` / chat | n/a | "exit criteria: …" |
| **Environments** | `GET`/`POST /environments` | via chat | n/a | "add environment &lt;name&gt; &lt;url&gt;" |
| **Test plan** | `POST /milestones/{id}/plans` | via chat | n/a | "plan &lt;suite&gt; for &lt;version&gt;" |
| **Start cycles** | `POST /plans/{id}/cycles` | via chat | n/a | "start cycles" |
| **Release readiness** (Go/No-Go) | `GET /milestones/{id}/readiness` | via `list_releases` | n/a | "are we ready to release &lt;version&gt;?" |
| **Release sign-off** | `POST /milestones/{id}/signoff` (approver+) | `sign_off_release` (**human**) | n/a | "sign off &lt;version&gt; as go" **human** |
| **Release metrics** | `GET /milestones/{id}/metrics.{json,md}` | via resource | n/a | ✓ |
| **Release report** | `GET /milestones/{id}/metrics.md` | resource `releases/{version}/report` | `galeqea release report <version>` | "release report &lt;version&gt;" |
| **Release: archive** | `POST /milestones/{id}/archive` (author+) | `archive_release` (gated) | n/a | "archive release &lt;version&gt;" |
| **Release: delete** | `DELETE /milestones/{id}` (admin) | `delete_release` (gated, approver+) | n/a | "delete release &lt;version&gt;" (admin) |
| **Project context** (briefing) | `GET /ai/context` | resource `context` | n/a | n/a |
| **Surface map** (llms.txt) | `GET /api/ai/context` | (static, in every resource desc) | n/a | n/a |

## Gaps filled in WO#2

Before WO#2 the machine surface had **9 incomplete rows**: capabilities with no
structured report and/or no MCP exposure:

1. Run report as JSON/Markdown/JUnit: *added* (`/runs/{id}/report.*`, `get_run_report`, resource).
2. Coverage report as JSON/Markdown: *added*.
3. Traceability report: *added* (report + MCP resource).
4. Flaky report as JSON/Markdown: *added*.
5. RCA report as JSON/Markdown: *added*.
6. Heals report: *added* (report + MCP resource).
7. `export_test` over MCP/chat: *added* (was CLI-only).
8. `list_schedules` over MCP/chat: *added*.
9. Agent context (`/api/ai/context`, project `/ai/context`, MCP `context` resource): *added*.

**After WO#2: 0 unintentional gaps remain.**

## Deliberate non-parity (by design, not gaps)

- **Approvals: decide** is *human-only*. A machine principal can never satisfy an
  approval gate (`SelfApprovalError`, enforced in code), so there is intentionally
  no `decide_approval` tool. The agent proposes; a person disposes.
- **The CLI is a deliberately thin subset**: `run`, `export`, `reset`, `doctor`,
  `mcp`, `version`. It is the getting-started and CI surface, not a mirror of the
  full API; agents use MCP/HTTP, humans use the UI.
- A handful of read capabilities (coverage cost, status brief, quality retro) are
  agent-facing planning tools with no dedicated HTTP route; they are reached over
  MCP and chat, and their outputs surface in the coverage/traceability reports.
