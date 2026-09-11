# Changelog

All notable changes to GaleQEA are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
"Public API" here means the REST API, the MCP tool surface, and the `galeqea` CLI.
A breaking change to any of those is a MAJOR bump.

Entries are grouped under **Added**, **Changed**, **Deprecated**, **Removed**,
**Fixed**, and **Security**. Because the project uses
[Conventional Commits](https://www.conventionalcommits.org/), this file is intended
to be generated from commit history at release time rather than hand-maintained.

## [0.1.1](https://github.com/mrviind/galeqea/compare/v0.1.0...v0.1.1) (2026-09-11)


### Added

* **agent:** first-run URL on-ramp with conversational slot-filling ([ebda38e](https://github.com/mrviind/galeqea/commit/ebda38e26fbee06f39437554a785845f8883b7ba))
* **integrations:** wire open_test_pull_request through the git.open_pr gate ([f6e8e35](https://github.com/mrviind/galeqea/commit/f6e8e357714238f130392b0357727eacefedd8b2))
* land the v1 feature set and release-ready polish ([dc3cb0a](https://github.com/mrviind/galeqea/commit/dc3cb0a6785dcd4adcd18d157182561c93b40621))
* **web:** funky-yellow brand and wind-mark favicon ([b0bc022](https://github.com/mrviind/galeqea/commit/b0bc0227560559f4736237fb01c4ea0c8867950d))


### Fixed

* **agent:** correct on-ramp chat rendering and command preview ([f2433ba](https://github.com/mrviind/galeqea/commit/f2433baa9deee6296aec08bcc4dbf8d155001589))
* **build:** add apps/api/README.md so a clean editable install succeeds ([27930a0](https://github.com/mrviind/galeqea/commit/27930a0e86161283dcfe787f2dc65aae31415a03))
* **build:** copy apps/api/README.md in the Docker image so the editable install works ([50dbcea](https://github.com/mrviind/galeqea/commit/50dbceaba007c233b1d626f5899d009da95a2bfa))
* **ci:** ignore major-version dependency bumps in Dependabot ([25f7054](https://github.com/mrviind/galeqea/commit/25f70541ce30f73eba5ebb81fbd599f834fd8931))
* **web:** stop the Copilot composer clipping its placeholder ([0289b48](https://github.com/mrviind/galeqea/commit/0289b485c3a040ac3dae5b9e46fabb6dbab8be64))

## [Unreleased]

### Added
- **A "what happens when you ask" pipeline overview on the Workspace empty
  state, reworked from a bordered-card grid into an open, animated timeline**.
  A first pass matched a competitor's layout too closely (four bordered
  cards in a row reads as a generic SaaS feature grid the moment more than
  one product uses that shape). Redesigned with no card borders: a soft
  colour-tinted circular icon badge per phase (Analyze/Plan/Run/Report) on an
  open connecting line, phase name in bold display type, checklist items
  marked with a small coloured dot instead of a checkmark glyph. The
  connecting line has a soft light continuously travelling through it
  (`.flow-line`, reusing the same "something is alive here" visual language
  as the existing `.agent-glow` border-sweep), and each phase rises into
  place with a short stagger on mount (`.rise-in`) instead of appearing all
  at once. Both are new CSS keyframes in `styles/index.css`, both
  automatically disabled under `prefers-reduced-motion` by the site-wide rule
  that already covers every animation.
  `components/workspace/FullFloorPipeline.tsx`.
  Colour-coded with GaleQEA's own existing status tokens (review/flaky/accent/
  pass) rather than a new arbitrary palette. Stacks vertically with a left
  accent rule (instead of the horizontal line + circles) below the `xl`
  breakpoint.
- **UI polish pass, part 3: richer Coverage metric cards**. The Requirements
  page's Covered/Automated numbers are now proper metric cards: a big
  percentage, a status word (Strong / Improving / Needs work) instead of a
  bare number, and an (i) that opens a small popover explaining the bands
  behind that word, the same shape as a metric card in a call/scenario
  analytics dashboard. The four risk-tier boxes (Critical/High/Medium/Low)
  are now colour-coded by severity using the same palette as risk chips
  elsewhere in the app, instead of four identical grey boxes. New reusable
  `InfoPopover`/`BandRow` components (`components/ui/InfoPopover.tsx`),
  portaled to `<body>` and positioned in fixed viewport coordinates rather
  than nested in normal flow. The metric cards live inside an
  `overflow-hidden` panel, and a naively-positioned absolute child would be
  silently clipped by it.
- **UI polish pass, part 2: every internal page now has a title**. A page
  identity strip (icon + name, e.g. "Command", "Runs", "Settings") now sits
  above every top-level page, derived from the same nav data so it can't drift.
  Stat-card numbers (pass rate, requirement coverage, etc.) are bigger and
  bolder for a real hero-number feel instead of blending into the page.
- **UI polish pass, benchmarked against a competitor's product review**. Chat
  replies now render real markdown (bold, links, lists) instead of literal
  `**asterisks**`, and an export reply ("export the test plan") shows a proper
  download chip instead of pasting a bare URL. Left nav is grouped by workflow
  stage (Plan & author / Execute / Review) instead of one flat 10-item list, with
  a data-driven Approvals badge. Panels have real elevation (a soft shadow token)
  instead of a flat border. The workspace empty state got two one-click
  suggestion chips (test the default site, try the sample requirements). The
  chat's compact progress rail now shows small step-indicator dots for
  done/future stages instead of unlabeled `·` characters, and always spells out
  a skipped stage instead of hiding it.
- **Redesigned Test Plan & Test Completion Report Word documents**. A shared
  visual system (`reports/docx_style.py`) replaces the old default-Word-template
  look across both documents: a cover with a big headline stat (pass rate / planned
  cases), an auto-updating table of contents (with a real section-list fallback for
  viewers that don't run Word's field-update pass), a running header/footer with
  live page numbers, rule-styled tables (tinted header, hairline rows, no boxed
  grid) with colored status **pills** (PASSED/FAILED/GAP/risk level), a proportional
  pass/fail **stat bar**, callout boxes for failure errors, and hairline-framed
  evidence screenshots. Typography is Calibri throughout, deliberately not the
  newer "Aptos" (2023+ Microsoft 365 default): verified by rendering both in
  LibreOffice, Aptos has no bundled cross-platform substitute and rendered
  *inconsistently* between weights (a broken-looking serif/sans mix on the same
  page), while Calibri's substitute (Carlito) renders identically everywhere. The
  report's own cover title now reads "Test Completion Report" (was "Test Execution
  Report", inconsistent with what the rest of the product calls it).
- **Bundled sample requirements template: try the whole floor in one click**. A
  realistic sample requirements doc ("TaskFlow", 17 REQ/NFR/BR items with acceptance
  criteria and deliberately ambiguous wording) ships with the app. From the
  Requirements page (or by asking chat "is there a sample requirements doc?"), a user
  can **download the template** to see the shape a good requirements doc takes, or
  **run the full floor with the sample** (ingest → generate requirement-derived test
  cases → crawl the default target → plan → run → report) in one call, with no spec
  or target to write first. This also finished wiring `services/full_floor.py`'s
  `floor_from_requirements`, which existed from an earlier goal but was never
  connected to a route; it's now live-verified end to end (`POST
  /requirements/sample`), including the newer Word/Excel/RTM report links.
- **Corporate documents + Word/Excel export, all from chat**. The core QA artefacts
  are now formal, exportable deliverables: a **Test Plan** (Word, IEEE-829 structure:
  scope, features, approach, entry/exit criteria, deliverables, schedule, risks,
  approvals), a **Test Completion Report** (Word *and* Excel: executive summary, results,
  by-type, failures with screenshot evidence), and a **Requirements Traceability Matrix**
  (HTML *and* Excel). The **test-case register** exports to Excel. Reachable from the UI
  (Export ▾ → Word/Excel on a run; an Excel button on the test board) and from **chat**:
  "export the test plan", "export test cases as excel", "export this report as word".
- **Page-aware chat**. The agent now knows which view you're looking at and answers
  questions about it with no model required: on the test board "how many test cases?"
  counts them by status and technique; on a run "what failed?" lists the failures; on
  requirements "how many are covered?" reads the traceability matrix. Exports are
  page-aware too, so "export this" gives you the right artefact for the open page.

### Fixed
- **A run's own title was invisible**. RunDetail's header showed "← [Failed]
  [cost] [duration] [actions...]" with no run number or title at all. The title
  `<div>` had `min-w-0` (correct, lets it shrink) but no `flex-1` (missing, so
  it never claimed space in the first place). Next to `shrink-0` action
  buttons, it was laid out at exactly 0px wide. The text was always there
  (confirmed via the DOM), just invisible.
- **`SectionTitle` (used on nearly every panel across the app) truncated the
  wrong text**. "Import from another tool" rendered as "Import from anot...",
  "Your API keys" as "Your API...". The title and its hint competed for the
  same shrinkable space; now the title never shrinks (matching the primitive's
  own existing rule for its action button, "a clipped action is worse than a
  clipped title," extended to hints) and only the hint truncates.
- **Command page's live execution log rendered at ~60px wide**, wrapping its
  placeholder text one word per line, while the run-history column next to it
  took the rest, a two-column `xl:grid-cols-[1.15fr_1fr]` layout with neither
  direct child given `min-w-0`. An unbreakable string in the narrower (1fr)
  column's content (a long run title/URL) set that track's min-content width
  *before* the fr ratio was applied, starving the other column. Adding
  `min-w-0` to both grid children lets the intended ratio actually apply.
- **Ollama provider construction always crashed**. `OllamaProvider.__init__`
  didn't declare `api_key` as a named parameter, so it fell into `**opts`; the
  constructor then passed `api_key=""` explicitly *and* forwarded `**opts`
  (which still held the caller's `api_key`) to `super().__init__()`, raising
  `TypeError: got multiple values for keyword argument 'api_key'`.
  `build_provider()` always passes `api_key`, so this fired on every attempt to
  use Ollama, including the client's per-message model selection, which meant
  a stale "Ollama" choice in a browser's local storage broke chat entirely, even
  for a plain-English question the deterministic page-aware path would otherwise
  have answered without touching any provider. Found live-testing the chat UI
  after the markdown-rendering fix below. `api_key` is now accepted (and
  ignored, as Ollama needs none) like its sibling local providers already do.
- **Chat replies with an `export` or unhandled `page_answer` block showed a
  bogus "Reload GaleQEA to view this update." card** underneath already-broken
  raw-markdown text. `Blocks.tsx`'s switch had no case for either block type.
  `export` now renders a real download chip; `page_answer` (metadata only, the
  reply text already carries the human-readable summary) renders nothing.
- **Table of Contents field rendered blank**. The cached fallback text (`<w:t>`)
  was nested *inside* the `<w:fldChar>` marker, which is invalid OOXML; renderers
  silently dropped it. Result text now lives in its own sibling run between the
  "separate" and "end" markers, per spec. Found by actually rendering the redesigned
  Test Plan / Test Completion Report to PDF (LibreOffice headless) rather than only
  checking the docx was structurally valid.
- **Requirement-doc title mangled when the ref used a bold-heading style**
  (a bold `REQ-101` followed by an em dash and `Title.`). The leading-punctuation
  trim ran before markdown was flattened, so titles came out as an em dash followed
  by `" Title."`. Found while building the sample requirements template; fixed in
  `engine/ingest.py::_title_of`.
- **Share publish no longer freezes the app**. `share.publish` is async but was making
  blocking storage writes directly on the event loop, so a slow object-store write could
  stall the whole process (and trip the container healthcheck). The writes now run in a
  thread pool.
- **Durable job queue + `galeqea worker`** (WO#4 P2-1). A `Queue` interface with two
  backends: the **in-process** queue (asyncio on the API loop, the zero-config SQLite
  default, unchanged) and a durable **Procrastinate** queue (Postgres LISTEN/NOTIFY +
  SKIP LOCKED, MIT) for the `full` profile. Runs, retention sweeps, PDF renders and
  webhook deliveries are enqueued rather than fire-and-forgotten, so on Postgres they
  survive a restart and spread across `galeqea worker` processes (`docker compose up
  --scale worker=N`), while SQLite needs no external broker. Queue depth is a Prometheus
  gauge (`galeqea_queue_depth{backend}`), the run board shows **"queued · position N"**,
  and a Postgres event bridge mirrors a worker's run events back to the web process so
  the live SSE stream, webhooks and metrics stay real-time even when runs execute in a
  worker.
- **The Full Floor: point GaleQEA at a URL and get the whole QA arc**. Analyse →
  plan → approve → run → evidence → a client-ready report, in one flow. It crawls the
  target in a real browser (5–7 key pages by default), lays out a typed test plan
  (accessibility, functional, forms, performance, SEO, security, links) with cases and
  data vectors for **human approval**, then executes every case, **captures a full-page
  screenshot for every failure**, and renders a **branded Microsoft Word report**:
  cover page with your logo, executive summary, scope, the test-case register, and each
  failure with its screenshot embedded. The default target is configurable
  (`GALEQEA_DEMO_TARGET_URL`, defaulting to a demo site) so a brand-new user can run the
  whole floor with one command. The same floor runs against a **live website**, a
  **localhost app**, or the cases generated from an uploaded **requirements document**.
  New service `full_floor` (`analyze` + `run_and_report`); the Word report is at
  `GET …/runs/{id}/report.docx`, `galeqea report <run>`, and **Export ▾ → Word** on the
  run detail. Verified live end-to-end against a real site: 8 pages, a 40-test plan,
  36 executed (26 passed / 10 failed), every failure evidenced, a 5.4 MB report.
- **Requirements → tests v2, part 4: a review board that shows its reasoning** (WO#9-D).
  Every proposal on the board now shows the **rule** it covers, the **technique** that
  produced it (a chip in the list and the detail), the rule's **source anchor** (heading
  path · page · line, so a reviewer can jump to the spec), and any **`ASSUMPTION:`** tags.
  Reviewers can **edit** a proposal in place (title and rationale) and either **Save**
  (a pure edit that keeps it proposed) or **Save & approve**; either way the edit is
  recorded as a project **few-shot example** that primes future generation toward the
  team's own style. A **"regenerate with an instruction"** box revises a proposal from
  plain English ("make the assertions stronger", "add a mobile viewport case") via
  `POST …/tests/{id}/regenerate`. `POST …/tests/{id}/review` gained a `save` decision so an
  author can edit without deciding.
- **Requirements → tests v2, part 3: first-class exporters, round-trip, and chat verbs**
  (WO#9-C). Cases export to the formats test managers actually import: **TestRail CSV**
  (Text and Steps templates), **Xray Test Case Importer CSV** (Manual step rows + a
  Cucumber row carrying the Gherkin, with the `Tests` column linking coverage back to the
  rule), **Qase JSON**, a **Gherkin `.feature`** whose Examples table is the case's BVA or
  pairwise rows, and **Playwright `.spec.ts`** with `test.info().annotations.push({type:
  'requirement', …})` for every ref and rule the case covers. Every format is available
  over `GET …/tests/export.bulk?format=…&ref=…`, the CLI (`galeqea tests export-bulk`),
  and chat
  ("export tests for REQ-014 as testrail csv"). A **round-trip** endpoint
  (`POST …/tests/roundtrip`) reads an existing TestRail/Xray export or a `.feature` and
  reports which imported cases went **stale** against the current requirements, which are
  **unlinked**, and which requirements are **newly uncovered**. New chat verbs cover
  "generate tests from <doc>", "what's ambiguous in <doc>?", and "show traceability".
- **Requirement docs de-duplicate themselves** (WO#9-C). Re-uploading the same file
  (identical content hash) now **updates the doc in place** instead of piling up a
  duplicate, and the traceability matrix reports each requirement **once**, against its
  **latest** document version (older uploads are superseded and hidden). Tidy an existing
  mess with `galeqea requirements dedupe`/`archive-doc`, the `POST …/requirements/dedupe`
  and `…/docs/{id}/archive` endpoints, or chat ("dedupe requirement docs", "archive
  requirement doc <id>").

### Fixed
- **Test API now exposes rule-level traceability** (WO#9-C). `GET /tests` and
  `GET /tests/{id}` (and the MCP `list_tests`/`get_test` tools and CLI `tests list`) now
  serialise a case's `covers`, `technique`, and `assumptions`; the detail view adds the
  covered rules with their `source_anchor`, so an agent reading a test sees which rule it
  exercises and where that rule was written, without a second call.
- **Requirements → tests v2, part 2: atomic rules, technique-driven cases, and a
  rules-layer RTM** (WO#9-B). Requirements are now decomposed into **atomic rules**
  (`requirement_rules`), each typed (functional / validation / permission / nav /
  nonfunctional) with a machine-readable constraint shape, extracted **deterministically**
  (must / shall / only / between / at least / one of / if…then) so the whole pipeline
  produces rules and cases with **no model at all**. Cases are generated per rule and link
  back with `covers`, so a coverage gap is precise: "this specific rule has no case", not
  "this requirement is untested". The classical-design engine gains the **string-edge
  partitions** every text field breaks on (empty / null / whitespace / unicode / overflow)
  and an in-repo **pairwise (all-pairs)** generator that combines a section's parameters
  ("shipping × payment") in far fewer rows than the full cross-product, rendered as a
  data-driven Examples table. An **ambiguity gate** turns vague wording into explicit
  `ASSUMPTION:` tags (a case is still generated, on a stated best-effort reading) rather
  than silently inventing a threshold. The **traceability matrix** now joins requirement →
  rules → cases → last result → defects, with per-rule coverage, a **P1–P4** risk priority
  (business impact × change proximity × failure history), and a new standalone **HTML**
  render (`GET …/traceability/report.html`) alongside the existing JSON/MD, CLI, and MCP
  resource.
- **Requirements → tests v2, part 1: ingestion with provenance** (WO#9-A). Every
  requirement item now carries a **source anchor** (`{heading_path, page, line,
  char_start, doc_id}`) so a generated test can point a reviewer back to the exact
  place in the document a rule was written. The deterministic splitter tracks the full
  heading breadcrumb (`["5. Checkout", "Payment"]`), maps a char offset to a PDF page,
  keeps a table as one chunk instead of shattering it row-by-row, and caps an oversize
  chunk at ~2K tokens. A glossary/roles digest is distilled onto the document so
  generation can prime on the doc's own vocabulary. An optional **`docling`** extra
  (`pip install 'galeqea[docling]'`) gives layout-faithful PDF/DOCX/PPTX parsing;
  ingestion degrades cleanly to pypdf/python-docx without it. An optional vision **UI
  inventory** step turns a screenshot/wireframe into a structured control list and
  cross-checks it against the text both ways ("the UI has a field the PRD never
  mentions"; "a rule names a control no screen shows").
- **Cheap by design: token-efficient page state, a zero-token re-run, and per-role
  model routing**. The model is now in the loop only where it earns its keep, and it
  sees far less when it is.
  - **Token-efficient page state**. Every snapshot handed to a model is an
    accessibility tree (`ariaSnapshot` mode `ai`) run through a deterministic trimmer:
    interactive controls and landmarks/headings only, long runs of siblings collapsed
    (`… ×N more`), text capped, each interactive node addressable by a stable `ref=eN`
    handle. A typical page drops ~16× (a 200-row table, 100×); `state_tokens` is
    recorded per call and only the current snapshot is retained. Heavy artifacts go to
    disk, read back on demand through a confined `read_artifact` tool.
  - **A step cache that makes a green re-run free**. Once a step is located (whether
    deterministically or by the model), its locator ladder is cached against the
    normalized intent + a page fingerprint (path + landmark/heading skeleton). The next
    run resolves it from Tier 0 of the healing engine: **no model, zero tokens**.
    Assertions and queries are never cached (a stale verdict is worse than a slow one).
    Exportable per test as `step_cache.yaml` alongside the test export.
  - **Per-role model routing + per-call ceilings**. Route each agent role to its own
    model: a small/cheap one for **grounding** (locating & self-healing), a frontier one
    for **planning** (authoring & exploration), a careful one for **judging**. Set it in
    Settings → Model (per-role), or in chat ("use gpt-4o-mini for locating"). Each role
    can also carry a **per-call token ceiling** ("cap locating at 2000 tokens"); a call
    that would run past it **stops and asks** rather than spending, and everything that
    does not need a model still works. Routed model and role are recorded on every trace
    and verdict.
  - **A token bench and a regression guard**. `node bench-tokens.mjs` reports raw-vs-
    trimmed page-state tokens across fixtures (overall ~16.5×, p95 502 tok); CI fails if
    the reduction regresses or `state_tokens` p95 runs past 600. `state_tokens` and cache
    outcomes are exported as Prometheus histograms/counters. _Deferred follow-up:_
    **per-stage golden-path token attribution** (breaking a run's token spend down by
    pipeline stage). The bench proves the aggregate reduction and the two-run assertion
    proves the zero-token re-run; the per-stage breakdown is a reporting nicety left for a
    later pass.
- **Tester ergonomics: evidence bundles, a manual runner, and exploratory sessions**.
  Every failing result gets a one-click **evidence bundle**: a zip of its
  screenshot/video/trace/console/HAR plus a `metadata.json` (the failure, its steps, and
  any linked defects), from the run detail view, `GET …/evidence.zip`, or
  `galeqea evidence <result>`. The keyboard-first manual runner gains **next-untested**
  (`GET /runs/{id}/next-untested`) and **paste-an-attachment** onto a result. And a tester
  can run a timeboxed **exploratory (SBTM) session** from chat: "start exploratory
  session on checkout, 30 min", log findings inline with "note: …" / "bug: …", then "end
  session" for a report of the charter, timebox vs. elapsed, and every note and bug.
- **Import test assets from other tools**. Bring existing tests and results in from
  another stack: Gherkin `.feature` files and TestRail / Xray / Zephyr **CSV** exports
  become PROPOSED test cases (each carrying its origin as provenance, reviewed in the
  board like any proposal), and **JUnit** XML result files become a historical run. A CSV
  import first proposes a **column mapping** (a guess from the headers that a person
  confirms or edits) rather than assuming one. Reachable via `POST /tests/import`,
  `galeqea import file <path>`, and an Import panel on the Author screen (with the mapping
  step inline).
- **Slack and Microsoft Teams notifications**. Connect a Slack Incoming Webhook or a
  Teams connector (its URL sealed in the vault) and GaleQEA posts a compact message when
  a subscribed event fires: a finished or failed run, a proposed self-heal, a queued
  approval, a signed-off milestone, or a finished cycle. Delivery rides the same event
  bus as the outbound webhooks and metrics, so a slow or failing channel never blocks a
  run. Choose what posts in Settings → Integrations (a "failures only" toggle and a test
  send), or in chat: "connect slack" opens a secure webhook-URL form and "notify slack
  on failures" / "on everything" tunes the subscription.
- **Publish a release report to Confluence**. "publish release report 1.4 to confluence
  space QA" (or `POST /milestones/{id}/publish`, the `publish_release_report` tool,
  `galeqea release publish`) renders the release report as a Confluence page: storage
  format with tables and Go/No-Go **status macros**, under a fixed parent, labelled
  `test-report`. Re-publishing **updates the same page in place** (version+1, with a 409
  retry when someone edited it meanwhile) so the link stays stable across releases
  (`report_pages`). Uses the same Atlassian API token as Jira via a dedicated Confluence
  connection. _Deferred follow-up:_ embedding chart/screenshot **attachments** on the page
  via the Confluence v1 attachment API. The current body is self-contained tables + macros.
- **Push run results to Xray, Zephyr Scale, or TestRail**. "push run #42 to xray plan
  APP-10" (or `POST /runs/{id}/push`, the `push_results` tool, `galeqea results push`)
  sends a finished run's results to a test-management system through one gated
  `results.push`. Each backend matches results to its own test identity: an Xray key or
  a **stable definition** (the GaleQEA test key, so an untagged test is created/updated
  idempotently and its requirement links become Xray coverage), a Zephyr `PROJ-T` key in
  the test name (JUnit automation import, auto-creating cases), or a TestRail case-id tag
  (a run is created and a result posted per case). Linked defects ride along on the Xray
  execution. Pushing is **idempotent per run+target** (`run_exports`): re-pushing returns
  the stored execution key instead of creating a duplicate. Xray Cloud and Data Center
  are both supported behind one backend seam.
- **Import Jira stories as requirements (the tester's daily loop)**. Connect Jira in
  Settings → Integrations, or with "connect jira &lt;site&gt;" (the API token is typed into
  a password field and sealed in the vault, never in the chat transcript), then "import
  stories from open sprint" / "fixVersion 1.2" / raw JQL pulls the issues (the current
  `POST /rest/api/3/search/jql` with `nextPageToken` paging, the legacy `/search` now
  removed), renders each ADF description to Markdown, and makes each story a requirement
  keyed by its Jira key. Imports are **idempotent and source-anchored** (`jira_issue_maps`
  holds the issue id + a description hash): re-importing updates in place, and a story
  whose description changed since import marks its linked tests stale
  (`/integrations/jira/stale`, or a scheduled poll). Generated tests flow through the
  normal review board; once approved, their coverage is **written back to the Jira story**
  as a comment (and a "Test" link when the test exists as an Xray issue). Reachable four
  ways: chat, HTTP (`/integrations/jira/import`, `/stale`), MCP (`import_jira_stories`,
  `write_back_jira_coverage`, both gated), and CLI (`galeqea jira import` / `stale`), with
  a separate `defect_project_key` so bugs and imported stories can live in different Jira
  projects. Verified against recorded HTTP fixtures plus an opt-in `GALEQEA_LIVE_JIRA=1`
  live smoke; no credential is ever logged.
- **File a defect from a failure, idempotently**. One sentence ("file a bug for run #42
  APP-T-03") or the **File bug** button on a failing result files a gated `defect.create`
  proposal; on approval GaleQEA opens an issue in the connected tracker: Jira Cloud first
  (a rich ADF body with reproduction from the test steps, environment/build/browser, run
  links, and the screenshot/trace/logs attached), GitHub/GitLab issues second (Markdown
  body, evidence linked back). Filing is **idempotent by failure fingerprint**: the same
  underlying failure seen again comments on the existing issue and bumps its
  re-occurrence count instead of opening a duplicate (`defect_maps`), and a `DefectLink`
  ties the issue to the result. A defect's tracker status is cached and refreshable
  (`POST /defects/{id}/refresh`), and **release readiness stops counting a failure as an
  open blocker once its linked defect is resolved**. Reachable four ways: chat, HTTP
  (`POST /results/{id}/defect`, `GET /defects`), MCP (`file_defect`), and CLI
  (`galeqea defect file` / `list`). Jira request shapes are covered by recorded HTTP
  fixtures (no live tenant in CI), and no credential is ever logged.
- **Releases view + environments**. A new **Releases** page: milestones down the side
  (progress bar, readiness Go/No-Go badge, sign-off state) and a detail pane with the
  metrics grid, each exit criterion evaluated with its actual value, the cycle
  configuration-matrix grid, and human-only Sign off GO / NO-GO buttons. Settings gains
  an **Environments** panel (add/list the places cycles run against), and the Runs page
  gains **release + environment filters**.
- **Release management in chat**. Plain-English verbs a QA lead uses drive the whole
  flow deterministically (no model): "create release 1.4 due Sept 15", "exit criteria:
  pass rate ≥ 95%, 0 open blockers, P1 requirements 100% covered, flaky ≤ 2%", "add
  environment staging …", "plan regression for 1.4 on chromium+firefox, mobile+desktop"
  (a plan card with the configuration matrix and case count), "start cycle" (one cycle
  per configuration), "are we ready to release 1.4?" (each exit criterion evaluated
  with evidence → Go/No-Go), "sign off 1.4 as go" (approver-only, human-only, immutable,
  audited) and "release report 1.4". A release-metrics service computes execution
  progress, pass rate, requirement/tested/P1 coverage, automation ratio, defect
  density, flaky rate, MTTR and effort variance; readiness, metrics (JSON/MD) and the
  release report are exposed over HTTP.
- **Test-manager object model** (release management). New entities for the objects a
  QA lead runs a release on: **Milestone/Release** (version, target date, exit-criteria
  rules, immutable sign-off), **Environment** (base URL, build label, vaulted
  credentials), **TestPlan** (a milestone's selection + a configuration matrix),
  **Cycle** (plan × one configuration, pinning each case's version at creation),
  **DefectLink** (a result → a tracked defect) and **SharedStep**; plus `parameters`
  and shared-step references on TestCase, and cycle/environment/milestone context on a
  run. One Alembic migration, dialect-neutral, applies over existing data.
- **Release lifecycle: archive/delete, full four-surface parity, and AI reach**.
  A release can now be **archived** ("archive release 1.4" / `POST …/milestones/{id}/archive`,
  author+), which keeps it in history but drops it out of the default list and frees the
  version for reuse, or **deleted** ("delete release 1.4" / `DELETE …/milestones/{id}`,
  admin).
  Milestone listing hides archived by default with an `include_archived` filter, and a
  dialect-neutral migration de-duplicates any existing releases (keeping the newest per
  version) then adds a partial unique index so a version has only one live milestone.
  The whole release surface is now reachable over **MCP**: `list_releases` (read),
  `create_release` / `archive_release` / `delete_release` (each gated behind the human
  approval gate) and `sign_off_release` (human-only, an AI principal can never sign),
  plus a `releases` resource and a `releases/{version}/report` resource. `cycle.finished`
  fires on the event bus (and to webhooks) when every pinned case in a cycle has a
  result (the cycle's counters roll up and it flips to complete), joining
  `milestone.signed_off`; both are subscribable webhook events. The release report has a
  published JSON Schema (`docs/schemas/release-report.schema.json`), the agent-context
  documents (`/api/ai/context` and a project's briefing) describe the release object
  model and its endpoints, and the Releases view guards a Go sign-off against a NO-GO
  readiness behind an **override note** dialog (the note is stored on the immutable
  sign-off).
- **GDPR endpoints + enterprise docs**. Admin-only subject-access export
  (`GET /api/gdpr/users/{id}/export`, everything GaleQEA holds about a user) and
  erasure (`POST /api/gdpr/users/{id}/erase`, which anonymises personal data and
  revokes tokens while keeping the hash-chained audit ledger verifiable: the ledger
  hashes the actor id, not the email, so the chain still validates). New
  `docs/ENTERPRISE.md` (a readiness checklist + the licence posture) and
  `docs/security/` (data-flow diagram, sub-processor list, DPA template).
- **OpenID Connect single sign-on**. Set `GALEQEA_AUTH_OIDC_ISSUER` + client
  id/secret and the Login screen shows **Sign in with SSO**; GaleQEA discovers the
  provider from `/.well-known/openid-configuration` (Authlib), creates the user
  just-in-time on first login, and maps their role from a groups claim
  (`GALEQEA_AUTH_OIDC_ROLE_MAP`, most-privileged group wins). Works with Keycloak,
  Okta, Entra, Authentik, Google, anything OIDC; SAML/LDAP via a Keycloak/Authentik
  broker. A ready-to-run Keycloak dev stack ships at
  `deploy/compose/oidc-keycloak.yml` and the flow is documented in
  `docs/security/OIDC.md`.
- **Prometheus metrics + optional OpenTelemetry tracing**. A `/metrics` endpoint
  (prometheus-client) exposes runs, test executions, failures, self-heals, approval
  decisions, live queue depth (`galeqea_active_runs`) and cumulative LLM tokens;
  counters advance off the same event bus the webhooks use. A ready-made Grafana
  dashboard ships at `deploy/grafana/galeqea-dashboard.json`. OpenTelemetry tracing
  (FastAPI + SQLAlchemy + httpx, OTLP export) is opt-in via `GALEQEA_OTEL_ENABLED=true`
  + `OTEL_EXPORTER_OTLP_ENDPOINT` and the `apps/api[otel]` extra, a no-op otherwise,
  so the default install carries no OTel weight.
- **Structured logs, request ids, and a SIEM stream**. All logging (GaleQEA,
  uvicorn, SQLAlchemy) now flows through structlog: human-readable on a terminal and
  JSON everywhere else (containers, CI), selectable with `GALEQEA_LOG_FORMAT`
  (`auto`/`json`/`console`). A request-id middleware stamps every request (honouring
  an upstream `X-Request-ID`), binds it to the log context so one request's lines are
  greppable end to end, and echoes it on the response. Set `GALEQEA_AUDIT_SIEM=true`
  to mirror every hash-chained audit-ledger entry as a single JSON line on stdout for
  a log collector to tail. The JSON/CSV audit export already shipped.
- **Golden Path plan approval is now a first-class, audited gate**. The plan a person
  approves before a site is tested used to exist only as a chat reply, with no HTTP/CLI
  surface and no audited approval. It is now a real `plan.approve` `ApprovalRequest`:
  proposing files it (deterministically, no model needed), and approving it goes through
  the one `approvals.decide` service from every surface (chat "approve", `POST
  …/journeys/{id}/plan/approve`, `galeqea plan approve`, and the `approve_plan` MCP tool,
  human-only), producing one hash-chained audit event with the human principal, and
  `SelfApprovalError` for a machine. Proposing is reachable everywhere too: `POST
  …/journeys` {target} and `galeqea plan propose <url>`. Approving builds the plan's
  golden-path tests as APPROVED so `galeqea test` (and a chat re-run) executes them.
- **Sign-in for shared deployments**. A multi-user install now has a real login:
  `POST /api/auth/login` / `logout` / `GET /api/auth/me`, a branded Login screen in
  the SPA (with a "Sign in with SSO" button that appears once OIDC is configured),
  and a session-aware sidebar showing who's signed in with a way out. Single-user
  desktop installs are unchanged, with no login screen.
- **Scoped API tokens in Settings**. Create least-privilege tokens (`runs:write`,
  `projects:read`, `reports:read`, `approvals:decide`) for CI, the MCP server and the
  CLI; the plaintext is shown once, only the hash is stored, and each token lists its
  scopes, last use and expiry with one-click revoke. A token can never exceed its
  owner's role.
- **S3 artifact storage**. `S3Storage` is now a real backend (boto3, any
  S3-compatible endpoint: AWS, SeaweedFS, Cloudflare R2, Ceph…), selected with
  `GALEQEA_STORAGE_BACKEND=s3` + `GALEQEA_S3_*`. Run artifacts (screenshots, video,
  trace, HAR, console logs), reports and share pages all flow through the storage
  seam, so the same code serves a laptop off disk and an enterprise off a private
  bucket. Objects are always fronted by the API (`url()` returns an `/api/…` path
  and the bucket never needs to be public), with `presigned_url` available for a
  short-lived direct link. Buckets are created on demand.
- **Per-project artifact retention**. Set `retention_days` in a project's settings
  and a daily sweep (plus `galeqea retention` on demand) expires that project's run
  artifacts past the window, deleting the bytes from disk or S3 and the `Artifact`
  rows. Runs, results and reports are untouched; a project with no `retention_days`
  keeps its artifacts forever.

### Fixed
- **Creating a release with a version that already exists is idempotent, not a 500**.
  `POST …/milestones` now returns the existing live milestone (`200`) when its version
  is already taken, instead of colliding with the new one-live-milestone-per-version
  unique index and surfacing a raw `IntegrityError`.
- **A stale session cookie can't lock a single-user desktop out**. Single-user mode
  now returns the local owner before consulting a session cookie, and its request gate
  (401 / CSRF / viewer) is a no-op, so a leftover cookie never forces a login screen on
  an install that has none.
- **Zero-config container no longer crashes on an empty bool env**. Compose passes
  `GALEQEA_S3_FORCE_PATH_STYLE` as an empty string in the local profile, which
  pydantic rejected as a bool and crashlooped the container on boot. Blank boolean
  env values now fall back to the field default, and the compose default is
  explicit.
- **Postgres migrations no longer fail auth with a masked password**. Migrations
  rendered the alembic URL with `str(engine.url)`, which SQLAlchemy masks as
  `galeqea:***@…`; alembic then authenticated as `***` and failed on every
  password-protected Postgres. The real URL is now rendered.
- **The app rides a cold database instead of crash-looping**. A freshly-created
  Postgres (pgvector, arm64) can take over a minute to finish `initdb` before its
  role accepts TCP logins; migrations now retry for ~3 minutes rather than crashing
  and restarting until the database happens to be ready.
- **Compose Postgres healthcheck is authenticated**. It runs a real `psql` login
  instead of `pg_isready`, which reports healthy during `initdb` before the password
  works; the SeaweedFS healthcheck targets `127.0.0.1` (it binds IPv4) and the worker
  placeholder's healthcheck is disabled (it doesn't serve the app port).
- **`stale_api` is not falsely reported inside a built image**. A packaged install
  has immutable source, so the source-drift check is disabled there (it remains
  active for a developer running an un-restarted server).
- **The Docker image builds and runs again**. The runtime base moved from the
  Playwright `-jammy` tag (Ubuntu 22.04 / Python 3.10, which cannot run the app's
  `>=3.11` code and whose pip rejects `--break-system-packages`) to `-noble`
  (Ubuntu 24.04 / Python 3.12). Same Playwright version, so browser/runner parity
  is unchanged.
- **Compose profiles: `local` and `full`**. One `docker-compose.yml` serves two
  topologies. `docker compose up` is the zero-config default: the app on SQLite in a
  named volume, no external services, no env. `docker compose --env-file
  deploy/compose/full.env up` brings up the full stack: **Postgres 16** (pgvector),
  a **SeaweedFS** S3 endpoint (Apache-2.0, not MinIO/AGPL), a **worker** placeholder,
  and **Valkey** (BSD-3). It points the app at Postgres + S3. Every bundled image is
  permissively licensed. The container entrypoint waits for Postgres before starting
  so a fleet can boot together. New `docs/DEPLOY.md` covers both profiles and how to
  point GaleQEA at your own managed Postgres or any S3-compatible object store.
- **CI: `zero-config-smoke`**. A new job builds the shipped image, boots it with no
  configuration, and asserts it comes up healthy on SQLite (`/api/health` ok,
  `stale_api` false, `/readyz` 200, no external DB), that the bundled demo app is
  reachable and the golden-path CLI is wired (it correctly gates a fresh install), and
  that the runner self-test passes, so the deployable artifact can never regress to
  needing configuration to boot.
- **Multi-user admin is loginable on first boot**. A shared (multi-user) deployment
  no longer starts with an admin nobody can sign in as. Set `GALEQEA_ADMIN_PASSWORD`
  and the first-boot admin is created with that password; leave it unset and the server
  mints a **one-time setup link** (`/setup?token=…`, only its hash stored on disk) and
  logs it, so an operator sets the password once and the link then stops working
  (`GET /setup` serves the form only while pending; `POST /api/setup` is 200 once,
  410 thereafter). Single-user mode is unaffected.
- **Golden Path: Keep-green**. "keep it green" turns a one-off run into an ongoing
  guarantee three ways: a **schedule** (a recurring run of the built suite, reusing the
  existing scheduler), a copy-paste **GitHub Actions workflow** that runs the Golden
  Path on a cron and every PR and fails the check unless readiness is Go, and **webhook
  routing** (the run.finished / run.failed webhooks already in place, Slack included).
  The keep-green card carries the schedule, the YAML, and the routing status.
- **Tests page: grouped, searchable, filterable**. The review board now groups test
  cases by their Golden Path suite (`Golden Path · Accessibility` …) under collapsible
  headers with counts, with a search box (title / key / page / tag) and a type filter,
  so 60+ cases read as a few named suites rather than a flat wall. The empty state now
  points at the chat on-ramp ("or test a URL from the chat"), not only requirements.
- **Golden Path: Manual checklist**. A plan's manual and exploratory-charter tests
  can't be driven by a browser, but they're still release evidence, so they ride the
  same run as a human checklist: each is a RunTest a person marks pass / fail / blocked
  / skipped, with a note and an optional attachment, and the executor is recorded on
  the row (`POST …/runs/{id}/manual/{run_test_id}`). Hotkeys on the board (j/k to move,
  p/f/b/s to mark). Manual rows stay out of the automated pass rate (mixing a human's
  pending checkbox into it would be misleading), and an **unexecuted** manual check is
  an open blocker for readiness until it's filled in. The report shows manual results
  alongside the automated, with the executor named.
- **Golden Path: Exploratory**. "poke at /checkout as a shopper for 10 min" runs a
  guardrail-bounded, timeboxed exploration (N ≤ 15, default 10; read-only respects the
  no-submit rule, the destructive-action avoid-list, and the target's rate limit) over
  the existing explorer, and surfaces the anomalies it finds: JS/console errors, dead
  ends (broken links, 4xx/5xx), slow pages, accessibility barriers, surprising
  behaviour, as an anomaly card. Each anomaly has **Make this a test** (files a
  proposed `TestCase` whose steps actually guard the regression: a GOTO for a dead
  end, an axe assertion for an a11y barrier, a Core-Web-Vitals check for a slow page,
  with provenance back to the finding) and **File bug** (gated). Sessions and their
  findings appear in the run report's Exploratory section (JSON / MD / HTML).
- **Golden Path: Regression delta & Rerun×3**. A run no longer just says "43
  passed"; it says what *changed*. `rerun failed` / `rerun the a11y suite` / `rerun
  what touched /checkout` selects a slice (previous failures, a type suite, or a
  path prefix) and runs it as a **child run** linked to its parent, then shows a
  **regression delta**: per test, new-fail / fixed / still-failing / new-flaky /
  unchanged vs the baseline (the previous run on the same target+env by default),
  with a one-line "what changed". The delta lands as a chat card, a "Since last run"
  section in Report v2 (MD + HTML), and JSON. **Rerun×3 from triage** re-runs a
  failure group three times in isolation: a test that passes on any attempt is
  auto-reclassified **flaky** (its original result flips, so triage stops counting
  it as an open failure and the readiness flaky rate reflects it) and quarantine is
  suggested; a test that fails every time hardens confidence in its class. All
  deterministic, all $0 to re-run.
- **API-process drift guard**. The provenance guard that covered the UI bundle now
  covers the API process itself: `/api/health` reports `api_started_at`,
  `newest_source_mtime` and `stale_api`; `/readyz` reports `stale_api` (informational,
  not a 503); and `galeqea doctor` queries the *running* server and **fails loudly
  (exit 2)** when it is serving code older than the source on disk. So the classic
  "edited a `.py`, forgot to restart uvicorn" (which unit tests can't catch) is now
  visible where a spot-check looks.
- **Golden Path: Report v2 & shareable artifacts**. The release report a
  stakeholder actually reads: an executive summary (verdict, the three headline
  risks, and the trend since the last run), readiness, coverage vs the plan *and*
  the discovered app map (which pages went untested), root-cause groups and their
  dispositions, accessibility broken down by axe impact, cost, and the concrete
  next actions. It renders to Markdown and to a **self-contained HTML share page**
  (no external assets), with a **stakeholder mode** that redacts names, links and
  screenshots. `share the report` publishes every format (MD / HTML / JSON / JUnit)
  through a new **storage interface** (`LocalStorage` now, `S3Storage` ready for
  later) behind one token, with a public toggle and a 30-day expiry; the public
  `GET /api/shared/...` endpoint serves it unauthenticated only while public and
  unexpired (private → 403, missing/expired → 404). A **PDF** is rendered on demand
  through the runner's own Chromium (`page.pdf`), with no extra print service, and
  stakeholder shares include it. Stakeholder redaction (names, links → path only,
  screenshots) is applied to **every** format, not just the HTML, so the four
  artifacts behind one token never leak what the HTML hides; the Markdown is one
  document with a single H1 (the run detail is a "Detailed results" section). The
  Share card offers every format plus Copy-for-AI, and routes to a team tool
  (Slack / Jira / Confluence / Xray) through Settings, so no format is a dead end.
- **Golden Path: Readiness Go/No-Go gate**. Before a run can be called releasable,
  six criteria are evaluated from the run's own report, deterministically: no open
  blockers, plan coverage ≥ threshold, flaky ≤ threshold, Core Web Vitals within
  budget, no critical/serious accessibility, no critical security. Each renders with
  its actual value, its threshold and an evidence link, and the verdict is **Go** only
  when every hard criterion passes. **Sign-off is a human act**: an AI principal can
  never satisfy the gate (`SelfApprovalError`: the agent, asked to sign, declines and
  says who can), a No-Go can be signed only with an override note, and the decision is
  appended to the hash-chained audit ledger, immutable by construction. `GET`/`POST
  .../runs/{id}/readiness[/sign-off]`.
- **Real accessibility engine (axe-core)**. `ASSERT_A11Y` now runs `@axe-core/playwright`
  (axe-core 4.13, MPL-2.0) against WCAG 2.0/2.1/2.2 A+AA, so "Accessibility (axe, WCAG
  2.2 AA)" is literally true. Each violation carries its rule id, **impact** tier
  (critical / serious / moderate / minor), the Deque help URL, and up to three offending
  selectors + HTML snippets; `fail_on` is a set of impact tiers (default serious +
  critical). The failure reads "N violation(s): 2 critical (color-contrast), 5 serious
  (label)", which flows into the report, the triage label and the readiness verdict. The
  old structural checker remains only as the fallback when axe cannot inject (a strict
  CSP), and the result says so.
- **Root-cause classification from the error**. Triage now classes a failure from what
  actually went wrong: a stale/absent locator → test-bug (heal), a navigation timeout /
  net error / 5xx / 429 → env (rerun, don't file a bug), a 404 on an internal link or a
  content/a11y/perf assertion → app-bug. The intelligence layer's history-based flaky /
  env / data calls still win when present.
- **Per-target rate limiting in the runner**. The guardrails' `rate_limit_rps` is now a
  real shared token bucket across all parallel workers, so a large suite can't hammer a
  production target and manufacture the env failures we'd then have to triage.
- **Access auto-detects the credential kind**. Discovery records how each gated page
  challenges (a 401 `WWW-Authenticate: Basic/Digest`, or a password form), so the Access
  card offers the right prompt and "add credentials" stores the kind automatically;
  basic and digest ride httpCredentials, a form login is filled and its session sealed.
- **Credentials are a list, injected per unit; an uncovered page skips, never fails**.
  A target can sit behind more than one wall (a form login on `/secure`, HTTP basic on
  `/basic_auth`), so credentials are a list keyed by (kind, scope path). Discovery keeps
  each page's required auth kind; Build tags every unit with it; the compiler injects the
  matching credential per test (a sealed storageState for form, httpCredentials for
  basic/digest). A page whose required kind has **no** matching credential is now
  **skipped with a reason** (`auth-gated (basic): add credentials`) and recorded as a
  skipped result, never run into a 401 and counted as a failure, so a missing credential
  can no longer manufacture a No-Go. The readiness card shows "N units skipped: add basic
  credentials to cover them" as a hint, not a criterion failure. Verified end to end on
  the-internet: `/secure` (form) + `/basic_auth` + `/download_secure` (basic) all pass in
  one suite while `/digest_auth` skips until a digest credential is added.
- **Golden Path: Access & log-in**. The pages behind a login become tested pages:
  - **Add credentials** seals a target's username/password in the vault (posted from
    the Access panel, never through the chat); the runner then signs in. A **form**
    login fills the detected fields and the resulting session (storageState) is sealed
    and replayed on every later run, while **basic** auth rides httpCredentials. The
    password and the session cookies never appear in the transcript, the audit ledger,
    a webhook, a report, or a stored test. Only a masked username hint is kept in the
    clear. `forget credentials for <target>` wipes both.
  - After sign-in the pages that were behind the wall (and the post-login landing page,
    e.g. `/secure`) are promoted into the plan, built, and run authenticated.
  - **Log in for me** parks a real browser at the login page for a human to sign into,
    then seals the resumed session (headed pause-and-attach, on the existing handoff).
- **One test-type helper everywhere**. The report's "by test type" table, the Run
  board's per-type bars, the JUnit `testsuite`/`classname`, and the readiness rules all
  resolve a test's golden-path type through a single helper (provenance → tag → key →
  category), so they can never disagree. A failure in a critical type (security,
  accessibility) now blocks the release verdict outright, and results are named
  `key · unit · label` (e.g. `DEMO-GP-A11Y-04 · /checkboxes · Accessibility`) so a
  reader sees which page failed. Accessibility failures name their rule ids inline.
- **Golden Path: Smoke, Run board, Triage**. The run leg of the journey:
  - **Smoke** ("smoke it") runs a ≤3-min front-door subset, the built tests
    tagged `smoke` (the entry page's functional journey and its console/hygiene
    check), not a parallel set, and gates the full run on it. If it blocks (the
    door won't open, the console is noisy), the blockers come back as a card to
    resolve first; if it's clean, the suite is cleared to run.
  - **Run board** ("approve" / "run the full suite") approves the built suite and
    runs it, then shows a live board: passed / failed / running / queued counts, an
    ETA, per-type progress bars, and the running cost of **$0, 0 tokens**, because
    executing a built test calls no model. A long run returns a "running" board the
    Runs view keeps streaming rather than hanging the chat.
  - **Triage** ("triage") groups the run's failures by root-cause *signature*, each
    group labelled by its human cause and the pages it hit (e.g. "accessibility:
    color-contrast, label · /checkboxes"), the hash riding along only as data, with a
    verdict (verified / partial / blocked / failed), an RCA class (app-bug / test-bug /
    flaky / env / data), a confidence, and the affected test keys. All five
    dispositions are always offered: Heal, Rerun (×3, a real child run), Mark-expected,
    File-bug (gated), Quarantine (7-day expiry). The ones that don't fit are greyed with
    a reason rather than hidden; the stage isn't done until every failure has one.
  - A built test that didn't execute (quarantined, unapproved) is shown on the board
    and in the report as `skipped: <reason>` and counted, never silently missing.
- **Golden Path: Build**. "build the tests" turns an approved plan into durable,
  reviewable tests-as-data: one proposed `TestCase` per test type × *target unit*
  (a page, a form, an endpoint, a viewport×page, a fault×page), capped by the plan
  row's count, so a later failure reads "a11y: /login has 2 serious violations", not
  "accessibility failed". Steps are in the runner's own vocabulary (assert_a11y,
  assert_perf, snapshot, api_request, network_condition, …); each test carries
  provenance back to the plan version, the type, and the unit. Tests of one type are
  grouped into a Golden Path suite (`Golden Path · Accessibility` …) so "run the a11y
  suite" and per-type Run-board progress work naturally, and the entry-page fast
  checks are tagged `smoke` for the smoke gate to reuse. Re-building reuses keys and
  reconciles suite membership rather than duplicating. The journey advances to Build.
- **Database migrations (Alembic)**. The schema now evolves through versioned
  migrations instead of `create_all`, so an existing install upgrades in place. On
  startup `galeqea up` brings the DB to head; a legacy DB built by the old
  `create_all` is reconciled (missing tables and columns added) and stamped, so no
  drop-and-recreate is ever needed. New `galeqea db upgrade|current|history`.
  `create_all` remains for the test suite and a brand-new empty DB. Column types are
  dialect-neutral, and CI runs the suite on both SQLite and PostgreSQL 16.
- **`/healthz` and `/readyz`**. Liveness, and readiness that checks the database is
  reachable, the runner is installed, and migrations are at head (503 until ready).
- **Golden Path: plan covering every test type**. The plan is now a review board
  of 14 test types (functional/E2E, forms negative+boundary, links, API/contract,
  accessibility, performance, visual, responsive, cross-browser, security hygiene,
  SEO, resilience, data-driven, exploratory, manual). Each row is derived
  deterministically from the discovery and carries a count, risk, confidence
  (High/Med/Low), estimated run-minutes and *build* tokens: 0 for the types
  generated by rule, and 0 on every re-run. Types are toggleable in the card or in
  chat ("enable accessibility", "drop visual"); each edit produces a new plan
  version with a diff. Form types vanish on a site with no forms.
- **Golden Path: Access & Guardrails**. The first time a target is seen is a real
  stop. If discovery hits a login wall, an **Access card** offers three ways forward
  (log in for me · add credentials · skip gated pages, never a dead end); otherwise a
  **Guardrails card** states what the agent will and won't do: read-only on anything
  that looks like production, synthetic test data, a destructive-action avoid-list, a
  rate limit, all editable in one click and remembered per target. The rail now marks
  auto-skipped stages distinctly from ones that ran, labels the current and next stage,
  and the crawler auto-dismisses cookie/consent banners. The plan card surfaces the
  page cap as a choice ("Testing 8 of 40" / "test all 40") and links auth-gated pages
  straight to the Access action.
- **Golden Path journey** (foundation). A `Journey` tracks a target's progress along
  the rail (target → access → guardrails → explore → plan → build → smoke → run →
  triage → readiness → report → keep-green). The chat header shows the rail, a reload
  resumes at the same stage, and "what's next" / "continue" resolve against the active
  journey (one per target+environment). Every stage renders one card: what happened,
  a primary next action, and the plain-English command shown. A ⌘K command palette
  lists the deterministic commands (with fill-in-the-blank), each dispatched into the
  chat as "you: …" so the transcript stays a replayable log.
- **AI-readable reports**. Runs, coverage, traceability, flaky, RCA and heals, each
  as `report.json` (versioned `schema_version`, every item carrying `id`/`href`/`ui_href`),
  `report.md` (LLM-friendly, token-budgeted), and, for runs, JUnit XML. JSON schemas
  live in `docs/schemas/` and are validated in tests; JUnit conforms to a bundled XSD.
  The run report carries a **cost block** (0 calls / 0 tokens / $0 for a re-run, the
  pay-to-build promise, quantified), groups results by test type, and states a
  release-readiness verdict. A run-report card in the UI has an Export ▾ (JSON /
  Markdown / JUnit / copy) and every report offers **Copy for AI** (the Markdown).
- **Agent context surface**. `GET /api/ai/context` (an llms.txt-style map of the
  HTTP/MCP/CLI surface, the report formats and the approval-gate rule),
  `GET /api/projects/{id}/ai/context` (a one-read project briefing), and the MCP
  `galeqea://{id}/context` resource. The OpenAPI schema moved to `/api/openapi.json`.
- **Token-cheap CLI for agents**. `galeqea runs list|get|report`, `tests list|export`,
  `coverage`, `flaky`, `traceability`, `rca`, `context`, `approvals list`,
  `schedule list`; each writes to a file with `--out` (printing only the path) and
  supports `--format json|md|junit`.
- **Outbound webhooks**. Endpoints subscribe to `run.finished`, `run.failed`,
  `heal.proposed`, `approval.requested`; deliveries are signed with the Standard
  Webhooks header set (`webhook-id` / `webhook-timestamp` / `webhook-signature`),
  retried with backoff, and recorded in a visible delivery log. Managed in Settings.
- **MCP report resources** (`runs/{id}/report`, `rca/{run_id}`, `traceability`,
  `heals`, `audit`, `context`) and new tools `get_run_report`, `list_schedules`,
  `export_test`, bringing the machine surface to parity (see `docs/PARITY.md`).
- **Chat parity**. A report request ("coverage report as markdown", "export last
  run as junit", "copy the run report for AI") renders as a card with the export
  actions; "plan coverage", "status brief" and "quality retro" answer deterministically.
- Agentic **test-a-website** flow: the on-ramp now *explores → proposes a test
  plan (functional / non-functional) → waits for approval in the chat → tests
  every discovered page in a real browser → reports*. Deterministic and model-free
  end to end; a configured model only sharpens the plan.
- **Browser-driven site discovery** in the runner (`--discover`): renders each page
  so client-rendered navigation is visible, seeds from `sitemap.xml` / `robots.txt`,
  probes pushState routes, and classifies auth walls, HTTP errors and off-site
  redirects as *findings* rather than crashing on them. Bounded (depth ≤ 3, ≤ 40
  pages) with normalized dedupe (fragment, `utm_*`/tracking params, trailing slash).
  Falls back to the HTTP-only crawl when the runner is absent. Skipped pages and
  truncation are surfaced in the plan, with no silent caps.
- `galeqea reset --demo`: clears a project's approval queue, chat history and run
  history (the junk that accrues from poking at the demo) and reseeds clean starter
  requirements. It affects the named project only, never touching other projects or
  real authored work; asks before deleting unless `--yes`.
- **Build provenance**: `GET /api/health` now returns `version`, `sha`
  (`-dirty` when the tree is modified), `built_at` (UI build time, read live),
  `started_at` and `ai_mode`. Shown in the `up` banner, `doctor`, `galeqea version`,
  the UI footer, and Settings, so a stale, reload-less server can't hide.
- First-run on-ramp: type a URL in the chat (`test https://your-app.com`) and
  GaleQEA drives a real browser to it, checks it loads cleanly, and sets it as the
  project target, with **no model and no test authoring**. If the URL is missing
  (`test my site`), the chat asks for it and uses the answer (conversational
  slot-filling). Built on the existing run pipeline via a built-in smoke probe that,
  as the product's own diagnostic, does not pass through the approval gate.
- Chat/MCP tool `open_test_pull_request` with an `@applier("git.open_pr")`: renders
  approved test cases to Playwright files and opens a pull request on the connected
  git provider, but only after the `git.open_pr` approval is granted. The agent
  proposes the PR; a human lets it out.
- Community-health files following common open-source practice: Code of Conduct
  (Contributor Covenant 2.1), `SUPPORT.md`, this changelog, GitHub issue forms,
  `CODEOWNERS`, Dependabot configuration, a CodeQL workflow, and release automation.
- `make start`: a single first-run command that installs every dependency, downloads
  Chromium, builds the UI, and launches on `:8080`.

### Changed
- **Report v2 is now the canonical report: one generator, one document.** The run
  report endpoints (`/runs/{id}/report.{json,md,junit.xml}`), the `galeqea runs
  report` CLI, the MCP `runs/{id}/report` resource and the share page all render from
  `report_v2` (schema_version **2.0**): an agent or CI reading the canonical endpoint
  now gets the full release report (executive summary, readiness gate, coverage vs
  the app map, regression delta since the last run, accessibility by impact, next
  actions), with the v1 per-test detail preserved as the "Detailed results" section /
  `results` array. The JSON schema in `docs/schemas/` and its test are updated to v2.
- Quality-planning tools reframed to pure QE, with no project-management vocabulary.
  `plan_test_sprint` → **`plan_coverage`** (ranks what to test next by risk ×
  coverage-gap × recent-failure history, no capacity/story-points);
  `estimate_test_effort` → **`estimate_coverage_cost`** (tests to author, run-minutes,
  and build tokens, where re-runs cost 0, stated explicitly); `test_standup` →
  **`test_status_brief`**; `test_retrospective` → **`quality_retrospective`**
  (failures caught/recurring, flaky count, heals applied, coverage). Story points,
  capacity, velocity, sprints and stand-up language are gone.
- Repositioned as **Gale QE Agent**, "the AI-first, open-source test automation
  agent that runs on any model". New `galeQEa` wordmark and "Gale QE Agent" titles.
  Cost framing made accurate: the model is spent to *build and reason*; re-running a
  built test (execution, deterministic healing, scheduling, reporting) needs no model
  and costs nothing.
- Fresh-session chat greeting shows three QE example commands (one per beat of the
  QE arc), led by the flagship on-ramp.
- `CONTRIBUTING.md` now documents the Conventional Commits standard (types, scopes,
  breaking-change convention) alongside the existing DCO sign-off requirement.

### Fixed
- The generated **Keep-green GitHub Actions workflow is now runnable.** It called
  `galeqea test` and `galeqea readiness`, neither of which existed, a dead end for
  anyone who clicked "add to CI". Both commands now exist: `galeqea test <url>` runs the
  Golden Path headlessly, **reusing the human-approved plan** for that target, strictly
  target-scoped (the journey for that exact URL, and only its own tests), so an approved
  plan for one target can never make a different URL runnable; exit 3 with **no run
  created** if none (it never auto-approves in CI), and writes a JUnit/JSON/MD report;
  `galeqea
  readiness <run|latest|url> --fail-on no_go` prints the criteria table and **exits 1**
  to gate the build. The workflow also now installs Chromium, caches pip/npm, passes
  login credentials via repo secrets → env, uploads results + report, and comments the
  report on the PR. A test executes the exact commands the YAML emits, and a static test
  asserts every `galeqea <cmd>` mentioned in generated YAML actually exists.
- A shared report's **PDF** now actually comes out of the share API. The runner
  streamed the PDF back as base64 on one NDJSON line, which for a real-size report
  exceeded asyncio's 64 KiB line limit and raised `LimitOverrunError`, swallowed by
  the "best-effort" render, so the Share card offered a PDF that 404'd. The runner now
  writes the PDF to a file and hands back its path (the reader limit is also raised to
  16 MiB), and the outcome is never silent: the share response, the stored metadata and
  the Share card carry `pdf_status = ready | failed (<reason>)`, and each delivery is
  logged.
- The SPA catch-all no longer shadows the API: an unknown `/api/...` path returns a
  JSON 404 instead of the index.html a caller would then fail to parse.
- Tests never write to the real `~/.galeqea` database. The suite asserts its home is
  the isolated temp dir (the junk approval queue came from dev *server* use, not tests).
- A single **Approve & run** click no longer fires two chat requests. `runCommand`
  opened the mobile chat drawer, mounting a second Copilot whose duplicate
  `RUN_COMMAND_EVENT` listener sent `approve` a second time; the racing requests made
  one flash the no-model dead-end before the real run landed. Fixed with a shared
  dispatch-token so exactly one listener handles each command.
- Website discovery honours slash-significant routes: the tested URL keeps its real
  trailing slash (`/a/` stays `/a/`, which some servers 404 without), while dedupe
  still treats `/a` and `/a/` as one page.
- Chat message timestamps show the date for any message not from today, so a
  conversation spanning days no longer reads as though its clock ran backwards.

### Security
- **Supply-chain hardening**. Release images are multi-arch (amd64 + arm64), pushed
  to GHCR, **cosign**-signed (keyless/Sigstore) and carry **SLSA provenance** + an
  **SBOM** (CycloneDX + SPDX via Syft) attestation. Continuous scanning runs in CI and
  weekly: `pip-audit`, `osv-scanner`, **grype** (image, fails on high+), and
  **gitleaks** for committed secrets, plus an OpenSSF **Scorecard**. A `SECURITY.md`
  documents private disclosure and image verification.
- **Permissive-license gate**. A CI `licenses` job keeps every bundled dependency
  permissive: a Python allowlist (MIT/BSD/Apache/ISC/MPL-2, LGPL-3.0 for the
  dynamically-imported psycopg driver is a reviewed exception) and an npm
  forbidden-copyleft denylist (no AGPL/GPL/LGPL/SSPL/BSL). AGPL/BSL/SSPL components
  stay documented, self-hosted integrations, never bundled.
- **argon2id password hashing**. New passwords use argon2id; existing PBKDF2 hashes
  still verify and are transparently upgraded to argon2 on the owner's next login.
- **Session cookies + CSRF**. The JWT rides an HttpOnly, SameSite=Lax (Secure over
  HTTPS) cookie; cookie-authenticated mutations require a double-submit CSRF token
  (bearer/API-token callers are exempt and unaffected).
- **Brute-force resistance**. Per-account lockout after repeated failures plus a
  per-IP rate limit (slowapi) on the login route.
- **Least-privilege at the route layer**. A shared deployment returns 401 for any
  `/api/*` outside a small public allowlist (health, setup, login, the agent surface
  map, public shares); viewers are read-only; deciding an approval requires an
  approver-or-above human principal and, for a token caller, the `approvals:decide`
  scope.
- **Strict Content-Security-Policy**. `script-src` is `'self'` plus the hashes of the
  UI's own inline scripts (no `unsafe-inline`, computed from the built bundle at
  startup so it always matches); plus `X-Content-Type-Options`, `X-Frame-Options` and
  a locked-down `frame-ancestors`/`object-src`/`base-uri`.
- **Migrations serialise across a Postgres fleet**. `run_migrations` now takes a
  Postgres session-level advisory lock so exactly one process (of web + workers + a
  migration job booting together) applies the schema while the rest wait and then see
  head. SQLite is single-writer and needs no lock.
## [0.1.0] - 2026-08-24

### Added
- Initial public release: AI-first, open-source test automation with a structural
  approval gate, tiered locator healing, a persistent App Model, a hash-chained
  audit ledger, a No-AI default mode, and an MCP server exposing the same tool
  registry that powers the built-in chat.

[Unreleased]: https://github.com/mrviind/galeqea/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mrviind/galeqea/releases/tag/v0.1.0
