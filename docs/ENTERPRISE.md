# Enterprise readiness

GaleQEA is open-source (Apache-2.0) and deployable on free, permissively-licensed
components. This is the checklist an enterprise reviewer can tick off, with pointers
to the relevant docs. ✅ shipped · 🟡 partial · ⏳ planned.

## Deployment

- ✅ **Zero-config local**: `docker compose up` / `make start` runs on SQLite with no
  external services ([DEPLOY.md](DEPLOY.md)); a CI `zero-config-smoke` job guards it.
- ✅ **Production topology**: `docker compose --env-file deploy/compose/full.env up`:
  Postgres 16 (pgvector), S3-compatible object storage (SeaweedFS), a durable worker
  placeholder, Valkey. Point at your own managed Postgres / S3.
- ✅ **Migrations on boot** under a Postgres advisory lock; `galeqea db upgrade|current|history`.
- ✅ **Signed multi-arch images** to GHCR (amd64 + arm64), cosign-signed with SLSA
  provenance + SBOM (see [Security](#security)).
- ⏳ **Kubernetes**: a Helm chart (web + worker + migration Job, HPA/PDB, external
  secrets, air-gap bundle) is planned.

## Identity & access

- ✅ **Password sign-in** for shared deployments (argon2id), with per-account lockout
  and a login rate limit.
- ✅ **OIDC single sign-on**: any provider (Keycloak/Okta/Entra/Authentik/Google),
  JIT users, groups→role mapping ([security/OIDC.md](security/OIDC.md)); SAML/LDAP via
  a Keycloak/Authentik broker.
- ✅ **Role-based access**: viewer / author / approver / admin / owner, enforced at
  the route layer; a machine principal can never satisfy an approval gate.
- ✅ **Scoped API tokens** (runs:write, projects:read, reports:read, approvals:decide),
  hashed at rest, with expiry, last-used and revocation, usable by CI/MCP/CLI.
- ✅ **Session hardening**: HttpOnly + SameSite (Secure over HTTPS) cookies, CSRF
  double-submit on cookie-authenticated writes.

## Security

- ✅ **Strict CSP** (`script-src 'self'` + hashes, no `unsafe-inline`), secure headers,
  `frame-ancestors 'none'`.
- ✅ **Supply chain**: cosign keyless signatures, SLSA provenance, Syft SBOM
  (CycloneDX + SPDX); `pip-audit` + `osv-scanner` + `grype` + `gitleaks` in CI and
  weekly; OpenSSF Scorecard. Disclosure policy in [SECURITY.md](../SECURITY.md).
- ✅ **Permissive dependencies only**: a CI license gate (see [Licence posture](#licence-posture)).

## Operations

- ✅ **Health & readiness**: `/healthz`, `/readyz` (DB + runner + migrations), plus a
  stale-code drift guard on `/api/health`.
- ✅ **Metrics**: Prometheus `/metrics` (runs, tests, failures, heals, tokens, queue
  depth) with a ready-made Grafana dashboard (`deploy/grafana/`).
- ✅ **Structured logs**: JSON off a TTY, a request id on every line and response.
- ✅ **Tracing**: optional OpenTelemetry (FastAPI/SQLAlchemy/httpx, OTLP).
- ✅ **Artifact retention**: per-project `retention_days` + a daily sweep.
- ⏳ **Backup/restore**: `galeqea backup`/`restore` planned (SQLite file + artifacts;
  `pg_dump` guidance for Postgres today).

## Compliance & governance

- ✅ **Hash-chained audit ledger**: append-only, verifiable; JSON/CSV export; an
  optional SIEM stdout stream.
- ✅ **Human approval on every write**: the agent can never approve its own output;
  every plan and every state change is an audited, human-decided gate.
- ✅ **GDPR endpoints**: subject-access export (`GET /api/gdpr/users/{id}/export`) and
  erasure (`POST /api/gdpr/users/{id}/erase`), admin-only; erasure anonymises personal
  data while keeping the ledger verifiable.
- ✅ **Data-flow, sub-processors, DPA**: see
  [security/data-flow.md](security/data-flow.md),
  [security/sub-processors.md](security/sub-processors.md),
  [security/DPA-template.md](security/DPA-template.md).

## Product

- ✅ **Golden Path**: paste a URL; the agent explores, plans, and (after your
  approval) builds per-unit tests across functional, a11y (real axe-core), perf, SEO,
  visual, security and more, then runs, triages, and keeps them green.
- ✅ **Runs on any model**: Anthropic/OpenAI/Gemini/Azure/Ollama or fully offline; the
  mechanical layer (run, heal, schedule, report) needs no model.

---

## Licence posture

- **Core: Apache-2.0.** All original code.
- **Every bundled runtime dependency is permissive**: MIT / BSD / Apache-2.0 / ISC /
  MPL-2.0 / PSF and similar. A CI `licenses` job enforces this and fails the build on
  anything outside the allowlist.
- **One reviewed exception: `psycopg` (LGPL-3.0).** The PostgreSQL driver ships only in
  the optional `postgres` extra, is dynamically imported and user-replaceable, so its
  weak copyleft does not propagate to GaleQEA. A default SQLite install bundles no LGPL
  code. The exception is named with its rationale in
  `.github/scripts/check_licenses.py` and in `NOTICE`; any future exception must go
  through the same script, which fails on an undocumented copyleft dependency.
- **AGPL / BSL / SSPL components** (MinIO, Garage, Redis 8, Grafana/Loki/Tempo,
  Zitadel…) are **never bundled**, only ever documented, self-hosted integrations an
  operator opts into.
