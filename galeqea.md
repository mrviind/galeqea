# GaleQEA — Requirement Document & Master Prompt for an AI-Developed, Open-Source AI-Driven Test Automation Framework

## 1. Project Vision & Suggested Name

**Suggested project name: GaleQEA** (working brand; verify with a formal USPTO/EUIPO clearance search in Nice Classes 9 and 42, and register the GitHub org, npm scope `@galeqea`, and PyPI project before public launch). A name-conflict check found no GitHub/npm/PyPI package, commercial software product, or software-field trademark using "GaleQEA." Fallback name: **Verald** (also clear; note only a faint phonetic resemblance to the security vendor Veracode — worth a quick clearance check). Names to avoid because they collide with existing tools/products: *Testweave* (existing Arweave testing SDK on GitHub/npm), *Provekit* (active World Foundation ZK dev toolkit), *Testronaut* and *Nova* (existing AI test agents), and *Qorra*/*Qualibrate*/*Qualia* (existing commercial software/SaaS names).

**One-line pitch:** *GaleQEA is a free, open-source, self-hostable, AI-first test automation platform where a chat-driven agent turns requirement documents into approved, categorized, and auto-generated tests — and runs, heals, analyzes, and reports on them — while keeping a human in the loop for every write.*

**Design ethos:** AI-first, local-first, extensibility-first, human-in-the-loop by default, zero vendor lock-in, and fully operable offline with no AI at all if desired.

---

## 2. The Master Prompt (hand this to an AI coding agent)

> **You are building GaleQEA, a groundbreaking open-source AI-driven test automation platform, entirely from scratch. Write all original code; copy no code from any existing project and do not reproduce any trademarked names, logos, or proprietary test artifacts. License everything under Apache-2.0.**
>
> Build a self-hostable platform composed of: (1) a **core test engine** that authors, stores, categorizes, schedules, and executes manual, exploratory, and automated tests; (2) an **AI agent orchestration layer** that analyzes requirements, proposes and generates tests, executes chat commands, performs root-cause analysis, and evaluates results; (3) an **MCP (Model Context Protocol) server** that exposes every platform capability as discoverable, well-scoped tools/resources/prompts; (4) a **chat interface** that drives the entire product in plain English and streams timestamped progress; and (5) a **secure integrations layer** for Jira/Xray, CI/CD, and Git providers.
>
> **Model flexibility is mandatory.** Support three model modes selectable per-user: (a) **user-supplied API key** for any provider (Anthropic, OpenAI, Google Gemini, Azure OpenAI, local/self-hosted, etc.) via a provider-abstraction layer; (b) **local/offline model** via an Ollama or OpenAI-compatible endpoint so the platform runs air-gapped; and (c) a **default "No-AI / No-Cloud" mode** where every core function (authoring, running, scheduling, reporting, manual RCA) works with zero LLM and zero outbound network calls. Regarding Claude subscriptions: implement a **"Bring-Your-Own-Agent" bridge** that shells out to a locally installed, user-authenticated Claude Code / Agent SDK CLI on the user's own machine, rather than routing Pro/Max OAuth tokens through GaleQEA's own services. This is a hard compliance requirement: Anthropic's Claude Code "Legal and compliance" documentation, updated 20 February 2026, states that "Using OAuth tokens obtained through Claude Free, Pro, or Max accounts in any other product, tool, or service, including the Agent SDK, is not permitted," and that Anthropic "does not permit third-party developers to offer Claude.ai login into their own applications, or to route requests through Free, Pro, or Max plan credentials on behalf of their users," with enforcement beginning 4 April 2026. Default all cloud/SaaS deployments to API-key auth.
>
> **Every write, change, or addition — creating tests, editing scripts, healing locators, filing tickets, committing to a repo — MUST pass a configurable human approval gate.** The AI never approves its own output. Provide per-action approval and gated-workflow (batch) approval modes, full audit history, role-based permissions, and immutable logs.
>
> **Core workflow to implement end-to-end:** user uploads a requirement document (plus optional supporting/client-context docs) → agent analyzes and proposes candidate test cases with rationale and requirement traceability → user approves/rejects/edits each case (human-in-the-loop) → approved cases are auto-categorized into **manual**, **exploratory/block**, and **automated** buckets → for automated cases the agent generates runnable test scripts (Playwright-based execution engine) → user runs anything by typing plain English in chat (e.g., "run the UAT button testing"), sees live logs in the main window and timestamped step-by-step status + progress in chat, with buttons for Run once / Run again / Run only this test / Schedule.
>
> **Intelligence features to implement:** regression-pack analysis that separates known/regression failures from new issues; flaky-test detection from historical run data; self-healing locators using multi-attribute/semantic intent matching; predictive test selection / test-impact analysis; anomaly detection on results; LLM-as-judge evaluation of ambiguous outcomes; automated root-cause analysis that ingests logs/traces/reports and attaches a confidence-scored RCA to each failure.
>
> **Integrations (all optional, credential-based, encrypted at rest in a secure vault):** Jira + Xray (prompt for Xray Client ID, Client Secret, and Jira base URL/API token; obtain a bearer token by POSTing `{"client_id","client_secret"}` to `https://xray.cloud.getxray.app/api/v2/authenticate` — note the token expires after 24 hours while the API keys do not expire); Jenkins, GitHub Actions, GitLab CI, Azure DevOps; GitHub/GitLab/Bitbucket repositories. Fetch build/test reports, analyze failures, run RCA, and provide a **manual "Create Jira ticket" button** plus optional AI-initiated ticket creation via MCP (behind the approval gate).
>
> **Futuristic capabilities:** an opt-in web-research tool so the agent can look up docs/errors to improve; a first-class **plugin architecture** (stable SDK, manifest, sandboxing, hot-load) so anyone can add test types, integrations, model providers, or reporters; persistent memory + storage; full conversation/execution history; rich real-time dashboards and reports; and a clean, beautiful, modern "wow-factor" UI.
>
> **Non-negotiable platform qualities:** users, roles & permissions, audit history, structured logs, persistent storage, agent memory, rich reports, re-run & scheduling, an encrypted secret vault, self-hostable single-command deploy, and full offline capability. Follow MCP security best practices: because the MCP specification requires that "Authorization servers MUST implement OAuth 2.1 with appropriate security measures for both confidential and public clients," implement OAuth 2.1 with mandatory PKCE (S256 method, no exceptions) for any internet-accessible server; use least-privilege scoped tokens; require explicit client-side confirmation for state-mutating/external/paid actions; validate all input against prompt injection; apply per-agent rate limiting; and keep fetched data sharply separated from instructions.
>
> Ship with excellent documentation, a contributor guide, example plugins, and a governed CI pipeline that itself enforces the human-review gate on AI-generated code.

---

## 3. Full Feature Specification by Module

### Module A — Model & Agent Layer
- **Provider abstraction** supporting Anthropic API, OpenAI, Google Gemini, Azure OpenAI, and any OpenAI-compatible/local endpoint; single-line provider switch; per-user and per-workspace model config.
- **Three operating modes:** API-key mode, local/offline (Ollama or OpenAI-compatible) mode, and default No-AI/No-Cloud mode where all core features work without any model.
- **Claude subscription handling:** local BYO-Agent bridge to a user-installed Claude Code/Agent SDK CLI on the user's own workstation; never route Pro/Max OAuth tokens through GaleQEA services (per Anthropic's 20 Feb 2026 legal/compliance policy, enforced from 4 Apr 2026). SaaS defaults to API keys.
- **Agent roles:** Requirement Analyst, Test Designer, Script Generator, Executor, RCA Analyst, and Judge (LLM-as-judge), each specialized and communicating over shared state.
- **Persistent agent memory** (conversation + project knowledge) with a local vector store; memory is inspectable, editable, and exportable.

### Module B — MCP Server
- Single bounded-context MCP server exposing **tools** (create_test, run_suite, heal_locator, fetch_ci_report, run_rca, create_jira_ticket, etc.), **resources** (test cases, run history, reports, requirements), and **prompts** (reusable analysis/generation templates).
- Transport: local stdio for desktop; streamable HTTP for remote/self-hosted; OAuth 2.1 + PKCE (S256) for remote auth per MCP spec.
- Security: least-privilege scoped credentials per tool, explicit client-side confirmation for any state-mutating/external/paid tool, schema validation on all I/O, per-agent rate limiting, prompt-injection isolation, and full tool-call audit logging.
- MCP-first design so GaleQEA is usable both from its own chat UI and from external MCP hosts (Claude Code, Cursor, VS Code, etc.).

### Module C — Requirement-to-Test Workflow
- Ingest requirement documents and optional supporting/context docs (PDF, DOCX, MD, images with OCR).
- Agent produces candidate test cases with rationale, priority, risk tag, and requirement-ID traceability.
- **Human-in-the-loop review board:** approve / reject / edit each candidate; bulk actions; comments.
- Auto-categorization into **Manual**, **Exploratory/Block**, and **Automated**; automated cases get generated, runnable scripts; every generated artifact records provenance (source requirement, prompt, model, version, approver).

### Module D — Chat-Driven Execution
- Plain-English command parsing ("run the UAT button testing", "schedule smoke nightly at 2am", "rerun only failed").
- Main window streams live execution logs; chat shows timestamped step-by-step status, processing indicators, and progress bars.
- Controls: Run once, Run again, Run only this test, Scheduled runs (cron-style), parallel execution across browsers/environments.

### Module E — Governance, Users & Audit
- Configurable approval gates: per-action approval or gated batch workflows; risk-tiered controls (higher-risk actions require higher approval roles).
- Role-based access control; user management; SSO-ready.
- Immutable audit trail linking each action to prompt/output trace, reviewer, and rationale; exportable compliance reports.
- Structural rule enforced in code: **AI can never approve its own writes.**

### Module F — Intelligence & ML
- **Regression-pack triage:** classify failures as known/regression vs new; commit-correlation to link failures to the change that introduced them.
- **Flaky-test detection** from historical outcomes (statistical + optional ML classifier); retry policies that distinguish true regressions from non-deterministic failures.
- **Self-healing locators** via multi-attribute/semantic intent matching, surfaced as reviewable diffs (never silent).
- **Predictive test selection / test-impact analysis** to run the highest-value subset on a change.
- **Anomaly detection** on results and durations; **LLM-as-judge** for ambiguous pass/fail with human override.

### Module G — RCA & Integrations
- **Xray + Jira:** credential prompts (Xray Client ID, Client Secret, Jira base URL/API token); token exchange via `POST https://xray.cloud.getxray.app/api/v2/authenticate` (24-hour bearer token; non-expiring API keys); push/pull test artifacts and results.
- **CI/CD:** Jenkins, GitHub Actions, GitLab CI, Azure DevOps — fetch build/test reports (JUnit/Allure/Playwright/etc.).
- **Git providers:** GitHub, GitLab, Bitbucket — read repos, propose script changes as reviewable PRs.
- **RCA engine:** ingest logs, traces, and reports; filter noise; produce confidence-scored, evidence-cited root-cause hypotheses; attach RCA to the failure; **manual "Create Jira ticket" button** + optional AI-initiated ticketing behind approval.

### Module H — Extensibility, Memory, Reports
- **Plugin SDK:** manifest-based, sandboxed, hot-loadable plugins for test types, integrations, model providers, reporters, and UI panels; public plugin registry.
- **Persistent storage** for tests, runs, artifacts, screenshots, memory, and history.
- **Reporting & dashboards:** real-time run dashboards, coverage-by-journey, flaky trends, RCA summaries, historical analytics; export to PDF/HTML/CSV.
- **Opt-in web research** tool for the agent to consult documentation and improve suggestions.

---

## 4. Design Inputs
- Requirement documents and optional supporting/client-context documents.
- User model choice: API key, local model endpoint, or No-AI mode.
- Optional integration credentials (Xray, Jira, CI/CD, Git) stored in the encrypted vault.
- Plain-English chat commands.
- Human approval/rejection decisions and role/permission configuration.
- Application-under-test targets (URLs, environments) and existing test assets/reports for import.
- Plugins installed by the user.

## 5. Design Goals
- **AI-first but AI-optional:** full core functionality with zero AI/cloud; graceful enhancement when a model is present.
- **Human always in control:** every write gated; complete auditability; no self-approval.
- **Zero lock-in:** open formats, any model provider, self-hostable, Apache-2.0.
- **Extensible by anyone:** stable plugin SDK and MCP interface.
- **Trustworthy intelligence:** self-healing and RCA surfaced as reviewable, evidence-backed suggestions, not silent changes.
- **Delightful UX:** clean, modern, real-time chat + dashboards.
- **Secure by default:** least-privilege credentials, encrypted secrets, prompt-injection defenses, MCP security best practices.
- **Original & lawful:** built from scratch, no copied code, no naming/IP conflicts.

## 6. Design Outputs / Deliverables
- Approved, categorized test cases (manual / exploratory / automated) with requirement traceability.
- Auto-generated, runnable automated test scripts.
- Live execution logs + timestamped chat status; scheduled and on-demand runs.
- Rich reports & dashboards (coverage, flaky trends, regression triage, anomalies).
- Confidence-scored RCA attached to failures; Jira tickets (manual button or approved AI-created).
- Immutable audit trail and compliance-ready exports.
- Self-hostable application (single-command deploy), MCP server, plugin SDK + example plugins, and full documentation/contributor guide.

## 7. Suggested Tech Stack & License

- **License:** **Apache-2.0** (permissive, includes an explicit patent grant — preferable to MIT for a project expecting many corporate contributors and integrations). Contributor DCO/CLA.
- **Execution engine:** **Playwright** — per its official docs it "can run tests on Chromium, WebKit and Firefox browsers as well as branded browsers such as Google Chrome and Microsoft Edge [and] can also run on emulated tablet and mobile devices," uses accessibility-tree snapshots, and ships an official MCP server (`npx @playwright/mcp@latest`) giving AI agents full browser control.
- **Backend / core:** **Python** (FastAPI) for the agent, ML, and MCP layers, using the official MCP Python SDK; optional **TypeScript/Node** services for browser orchestration. (Language chosen for the richest AI/ML and MCP tooling.)
- **Agent framework:** provider-agnostic orchestration (e.g., LangGraph-style multi-agent) over the provider abstraction; **Ollama**/OpenAI-compatible for local models.
- **Frontend:** **React + TypeScript**; desktop packaging via **Tauri** (Rust backend, native WebView). Tauri v2 ships a sub-600 KB core with typical installers of ~3–15 MB and ~20–100 MB idle RAM, versus Electron's 50–150 MB+ installers and 100–300 MB idle RAM — a decisive advantage for a local-first developer tool with a strong security boundary. Provide a browser-based mode for self-hosted server deployments.
- **Storage:** **PostgreSQL** (relational data, audit, history) + a **vector store** (e.g., pgvector) for agent memory; object storage for artifacts/screenshots.
- **Secrets:** encrypted vault (OS keychain locally; envelope-encrypted secrets in server mode).
- **Packaging/deploy:** Docker Compose + Helm for self-hosting; single-command bootstrap.
- **Quality:** the project's own CI enforces a human-review gate on all AI-generated code.