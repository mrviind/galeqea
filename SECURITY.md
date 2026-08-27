# Security

## Reporting a vulnerability

Email **aravind3@gmail.com** with a description and reproduction steps. Please do
not open a public issue. We aim to acknowledge within 3 working days.

## Threat model

QE Agent executes untrusted web content, ingests untrusted documents, holds
credentials for external systems, and — optionally — sends content to a model. The
design assumes each of those is hostile.

### Prompt injection

**Assumed to succeed sometimes.** A requirement PDF, a page under test, a CI log or
a Jira comment can all contain text engineered to look like an instruction.
Defences, in order of how much they are relied on:

1. **The approval gate is the real backstop.** Even a fully successful injection can
   only cause the agent to *propose* a write. A human still decides. Every other
   defence here is depth, not the load-bearing control.
2. **Untrusted content is nonce-fenced.** Each block is wrapped with a random
   per-call nonce, so injected text cannot forge the closing delimiter and escape
   into instruction space.
3. **Detection is surfaced, not silent.** `core/safety.py` scans for known patterns
   and shows them to the user. Silently stripping an attack hides an attack in
   progress; the Requirements page renders the flagged passages verbatim.
4. **Bounded choices.** The healing prompt can only return an index into a
   server-supplied candidate list — it cannot invent a selector. That bounds the
   blast radius of both a hallucination and an injection.

### Credentials

- Envelope encryption: a per-secret data key sealed by the installation master key,
  AES-256-GCM throughout, with the project and secret name as additional
  authenticated data.
- **Secret values are never returned by the API.** Only a hint (`sk-ant-…4f2a`), so
  a human can confirm which key is wired up without it entering a response body,
  a log, or a browser's memory.
- `ResolvedSecret.__repr__` returns `***`, so a secret cannot leak through a
  traceback or a debug log line.
- `core/safety.redact()` masks secret-looking keys before anything is logged or sent
  to a model.

### Code execution

- `script` steps are stripped by the plan compiler unless their body passed the
  approval gate, and the runner refuses one that arrives unapproved — two
  independent checks, because this is the one step that runs arbitrary code.
- The runner holds no credentials and has no database access.
- Artifact downloads are path-contained: an artifact row cannot serve a file from
  outside the artifacts root.

### The BYO-Agent bridge

The bridge shells out to a locally installed `claude` binary. It:

- refuses to run unless the server is bound to a loopback address;
- scrubs every Anthropic credential variable from the subprocess environment, so the
  CLI can only use its own authentication;
- never reads credential files and never forwards tokens;
- logs every invocation to the audit ledger.

This is a compliance boundary as well as a security one — see the README.

### Plugins

**Stated plainly: in-process Python is not a security boundary.** Plugins install
disabled, capabilities are granted explicitly, and a changed checksum revokes the
grant so new code never runs under an old approval. Run untrusted plugins out of
process; the `external` transport is reserved for that and is not yet implemented.

### MCP

- OAuth 2.1 with mandatory PKCE (S256) for any internet-accessible server. There is
  no plaintext-challenge fallback.
- Least-privilege scoped tokens, checked per tool call.
- Explicit client-side confirmation required for every state-mutating, external or
  paid action; annotations come from the registry, not from prose.
- Per-client rate limiting.
- Strict schema validation with `additionalProperties: false`.

## What QE Agent does not do

- **No telemetry.** Nothing is sent anywhere, by default or otherwise. There is no
  endpoint to disable.
- **No outbound traffic in No-AI mode.** The default configuration makes no network
  calls at all.
- **No credential proxying.** QE Agent never routes a user's subscription credentials
  through its own services.

## Hardening a deployment

```bash
GALEQEA_SINGLE_USER_MODE=false     # require authentication
GALEQEA_APPROVAL_MODE=per_action   # no batching
GALEQEA_VAULT_KEY=...              # from your own secret manager, not the disk file
GALEQEA_WEB_RESEARCH_ENABLED=false # default; keeps the agent off the network
```

Run behind a reverse proxy with TLS. Verify the audit ledger on a schedule:
`galeqea audit --verify-only` exits non-zero if the chain is broken.
