# Security policy

## Reporting a vulnerability

Please report security issues **privately**. Do not open a public issue.

- Use GitHub's **[Report a vulnerability](https://github.com/mrviind/galeqea/security/advisories/new)**
  (Security → Advisories), or
- email the maintainers with the details and, if possible, a minimal reproduction.

We aim to acknowledge within **3 business days** and to ship a fix or mitigation
for confirmed high-severity issues within **30 days**, coordinating disclosure with
the reporter. Please give us reasonable time to remediate before any public
disclosure. We credit reporters unless you prefer to remain anonymous.

## Supported versions

Security fixes land on `main` and the latest released image
(`ghcr.io/mrviind/galeqea`). Pin a released tag for production.

## What we do on our side

- **Signed, attested images**: release images are multi-arch, **cosign**-signed
  (keyless/Sigstore) and carry **SLSA provenance** and an **SBOM** attestation.
  Verify before you run:

  ```
  cosign verify ghcr.io/mrviind/galeqea:<tag> \
    --certificate-identity-regexp '^https://github.com/mrviind/galeqea' \
    --certificate-oidc-issuer https://token.actions.githubusercontent.com
  ```

- **Continuous scanning**: `pip-audit`, `osv-scanner` and **grype** (image) run in
  CI and weekly; **gitleaks** guards against committed secrets; an OpenSSF
  **Scorecard** runs on a schedule.
- **Permissive dependencies only**: a CI license check keeps every bundled
  dependency permissive (MIT/BSD/Apache/ISC/MPL-2); AGPL/BSL/SSPL components are
  only ever documented, self-hosted integrations, never bundled.
- **Human approval on every write**: the agent can never approve its own output;
  every state change is hash-chain audited.

See `docs/security/` for the OIDC SSO guide and the data-flow / sub-processor notes.
