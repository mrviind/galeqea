# Test management integrations

Where GaleQEA's test cases can go, what each system actually is underneath, and
what that means for the export.

---

## The shape that travels

Every one of these tools is an implementation of the same structure — the one
IEEE 829 defines and ISTQB adopted as its reference. GaleQEA stores that shape
natively, so exporting is a translation rather than a reconstruction:

| Field | Meaning |
|---|---|
| `id` | stable identifier for tracking |
| `title` | what is being validated |
| `objective` | why the test exists — the rationale a reviewer approved |
| `preconditions` | state that must exist before execution |
| `steps` | ordered `(action, data, expected result)` triples |
| `priority` | relative importance |
| `requirement_refs` | traceability back to the requirement |
| `labels` | tags for selection and reporting |

The **expected result is defined before execution**, not recorded after. That is
the whole point of the field: it makes pass/fail an objective comparison rather
than a judgement made while looking at the output.

---

## Supported targets

### Xray Cloud — implemented

A test is a **Jira issue** of type Test. That is Xray's defining choice: tests
live in Jira's permission model, workflow and JQL, which is exactly what
Jira-native teams want and exactly what makes bulk test management awkward.

- **Auth:** `POST /api/v2/authenticate` with `{client_id, client_secret}` →
  a 24-hour bearer token. The key pair does not expire; the token does. GaleQEA
  caches it with its expiry and refreshes 45 minutes early, so a long run cannot
  straddle the boundary.
- **Create:** GraphQL `POST /api/v2/graphql`, `createTest` mutation, with native
  `steps { action data result }`.
- **Gotcha:** GraphQL answers `200 OK` with an `errors` array. Treating that as
  success is how an integration "successfully" pushes nothing — GaleQEA checks
  the array explicitly.
- **Results:** already supported separately via `POST /api/v2/import/execution`.

### Zephyr Scale — implemented

A test is **not** a Jira issue. Zephyr keeps its own object model — folders,
cycles, plans, parameters — and links to Jira. Better dedicated test-management
UX, weaker Jira-native integration. The opposite trade-off from Xray.

- **Auth:** Bearer API token.
- **Create:** `POST /v2/testcases`, then `POST /v2/testcases/{key}/teststeps`.
  Steps are a **second call** — the case endpoint does not accept them.

### Azure DevOps Test Plans — implemented

A test is a **work item** of type `Test Case`, so it inherits area paths,
iterations, boards and queries.

- **Auth:** PAT, basic auth with an empty username.
- **Create:** `POST /_apis/wit/workitems/$Test%20Case?api-version=7.1` with a
  JSON **patch** document — `Content-Type: application/json-patch+json`, not
  `application/json`.
- **Steps** live in `Microsoft.VSTS.TCM.Steps` as a custom XML blob:

```xml
<steps id="0" last="2">
  <step id="1" type="ValidateStep">
    <parameterizedString isformatted="true">&lt;DIV&gt;&lt;P&gt;action&lt;/P&gt;&lt;/DIV&gt;</parameterizedString>
    <parameterizedString isformatted="true">&lt;DIV&gt;&lt;P&gt;expected&lt;/P&gt;&lt;/DIV&gt;</parameterizedString>
    <description/>
  </step>
</steps>
```

  Three rules that bite: every `<step>` needs **exactly two**
  `parameterizedString` children even when there is no expected result;
  `ValidateStep` has an expected result and `ActionStep` does not; and the body
  is escaped **twice** — once as HTML, once as XML — so an unescaped ampersand
  fails validation outright rather than degrading.

### TestRail — implemented

Standalone rather than Jira-resident, and licensed per TestRail user — which
matters when manual testers or contractors do not otherwise need Jira seats.

- **Auth:** basic auth with an API key.
- **Create:** `POST /index.php?/api/v2/add_case/{section_id}` with
  `template_id: 2` ("Test Case (Steps)") and `custom_steps_separated` as
  `[{content, expected}]`. Cases belong to a **section**, so a section id is
  part of the connection.

---

## Not implemented, and why

| Tool | Position | Why not yet |
|---|---|---|
| **Tricentis qTest** | Enterprise ALM, strong at scale | REST API is capable; no current demand and it needs a licensed instance to verify against |
| **Qase** | Cleanest standalone UX | Straightforward REST API — the cheapest of these to add next |
| **PractiTest** | Full ALM breadth | Broad surface; a partial integration would be worse than none |
| **Jira alone (no test add-on)** | Very common in practice | GaleQEA already files Jira *issues*; a test case as a plain issue loses the step structure, so it is offered as a defect path rather than a test-management one |

Adding one is a single adapter in `apps/api/galeqea/integrations/testcases.py`:
map `PortableTestCase` onto the target's shape and register it in `TARGETS`.

---

## How pushing behaves

Every export **leaves the building**, so every export is behind the approval
gate — including one an agent requests over MCP. Calling `push_test_cases`
returns an approval id, not a result:

```json
{ "ok": true, "status": "awaiting_approval", "approval_id": "1bd61c14…", "risk": "high" }
```

Only **approved** test cases can be exported. A proposal is not a test yet, and
pushing one into a shared test management system would launder an unreviewed
suggestion into something that looks agreed.

---

## Getting requirements in

| Format | Support |
|---|---|
| `.xlsx` / `.xlsm` | **Structured** — the table is read as a table: header row detected past any title block, columns mapped by meaning (ID, Requirement, Acceptance Criteria, Priority, Module, Type), one requirement per row |
| `.docx` | Headings, paragraphs and tables |
| `.pdf` | Text layer; a scanned PDF is reported as needing OCR rather than ingested empty |
| `.md` / `.txt` | Headings, bullets and numbered lists |
| `.xls` (legacy) | Refused with an instruction to re-save as `.xlsx` — no data is lost in that conversion |

A spreadsheet's own **Priority column outranks inferred risk**: it is a human
judgement, and guessing over it would be presumptuous.

**Every requirement gets at least one test.** That is verified after generation,
not assumed — three separate steps can otherwise drop a requirement's last
proposal (similarity de-duplication, model enrichment replacing the baseline,
and suppression against tests that already exist). Anything missing is
backfilled and named in the report.
