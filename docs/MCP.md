# Using QE Agent over MCP

QE Agent is MCP-first. The registry that backs the built-in chat is exposed directly,
so an external host — Claude Code, Cursor, VS Code — gets **exactly** the same
capabilities, the same schema validation and the same approval gate. There is no
reduced "MCP subset" to drift out of sync with the product.

## Connect

```bash
galeqea mcp-config     # prints the snippet below, with your paths filled in
```

```json
{
  "mcpServers": {
    "galeqea": {
      "command": "galeqea",
      "args": ["mcp"],
      "env": { "GALEQEA_HOME": "~/.galeqea" }
    }
  }
}
```

Protocol version: `2025-11-25`. Transport: stdio for desktop hosts; streamable HTTP
for remote deployments, which additionally require OAuth 2.1 with PKCE (S256).

## What you get

**Tools** (20) — `list_tests`, `get_test`, `get_run`, `get_coverage`,
`list_requirements`, `get_flaky_tests`, `select_tests_for_change`, `run_rca`,
`get_audit_trail`, `run_tests`, `cancel_run`, `create_test`, `update_test`,
`approve_heal`, `schedule_run`, `create_jira_ticket`, `push_results_to_xray`,
`fetch_ci_report`, `remember`, `recall`.

**Resources** — `galeqea://{project}/tests`, `/requirements`, `/runs/latest`,
`/coverage`, `/flaky`, `/approvals`, `/app-model`.

**Prompts** — `analyze_requirements`, `design_tests`, `triage_run`,
`explain_failure`, `coverage_review`.

## The security model

### Annotations are honest

They come from the registry, not from a description string, so a client can decide
what to confirm without trusting prose:

```json
{ "readOnlyHint": true,  "destructiveHint": false, "openWorldHint": false }  // list_tests
{ "readOnlyHint": false, "destructiveHint": false, "openWorldHint": true  }  // create_jira_ticket
```

### State-mutating tools cannot mutate state

This is the important one. Calling `create_test` over MCP does **not** create a test:

```json
{
  "ok": true,
  "status": "awaiting_approval",
  "approval_id": "e7950ccfa4c94f74b0a06543",
  "risk": "medium",
  "message": "This action needs human approval before it takes effect. Request e7950… is queued for review."
}
```

An MCP caller is treated as a machine principal and can never satisfy a gate. The
worst a fully compromised MCP client can do is put something in a human's review
queue.

### The rest

- **Least privilege** — tools declare required scopes; remote tokens carry a scope
  list and are checked per call.
- **Strict input validation** — every argument is validated against the tool's JSON
  Schema, with `additionalProperties: false`. Unknown arguments are rejected, not
  ignored.
- **Rate limiting** — per client, per direction: 120 reads/min, 20 writes/min.
- **Prompt-injection isolation** — page snapshots, documents and CI logs are wrapped
  in nonce-delimited untrusted blocks. The nonce is random per call, so injected
  content cannot forge the closing delimiter and escape into instruction space.
- **Full audit** — every tool call is written to the hash-chained ledger.

## Example session

> **You:** Which requirements have no test, and what should I write first?

The host calls `get_coverage`, which returns gaps ordered worst-first:

```
2/3 requirements covered (67%). 1 high or critical-risk requirement has no
approved test: REQ-002.
```

> **You:** Propose tests for REQ-002.

The host calls `create_test`. Each proposal returns an `approval_id`. Nothing
exists yet — open QE Agent's Approvals page, read the rationale and the diff, and
decide.

## Remote deployments

Set `GALEQEA_SINGLE_USER_MODE=false` and issue a scoped API token. Per the MCP
specification, authorization servers must implement OAuth 2.1 with appropriate
security measures for both confidential and public clients; QE Agent requires PKCE
with the S256 method and has no plaintext-challenge fallback.
