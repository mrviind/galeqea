# Getting help

Use the right door and you'll get an answer faster.

| You want to… | Go here |
|---|---|
| **Ask "how do I…?"** or share how you use GaleQEA | [GitHub Discussions](https://github.com/mrviind/galeqea/discussions) — not the issue tracker |
| **Report a bug** — something is broken or wrong | [Open a bug report](https://github.com/mrviind/galeqea/issues/new?template=bug_report.yml) |
| **Request a feature** (within the testing/QE scope) | [Open a feature request](https://github.com/mrviind/galeqea/issues/new?template=feature_request.yml) |
| **Report a security vulnerability** | **Do not open an issue.** Follow [SECURITY.md](SECURITY.md) — email aravind3@gmail.com or use GitHub Private Vulnerability Reporting |
| **Report a Code of Conduct concern** | Email aravind3@gmail.com — see [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) |

## Before you file a bug

Two commands make almost every report actionable:

```bash
galeqea doctor          # what's installed and what isn't
galeqea audit --verify-only   # only if you suspect a ledger problem
```

Include their output, the GaleQEA version (`galeqea version`), the operating mode
you were in (No-AI, local model, API key, or the BYO-Agent bridge), and — if a run
was involved — the run id. The bug report form asks for exactly these.

## The scope, so you're not surprised

GaleQEA is a **test-automation** tool. Feature requests are weighed against that
scope: authoring, execution, healing, coverage, triage, reporting, and the
integrations that serve them. Requests to turn it into a general project-management
or non-testing tool will be declined, kindly — it's a deliberate boundary, not an
oversight.

## First time contributing?

Read [CONTRIBUTING.md](CONTRIBUTING.md). Issues labelled **good first issue** are
scoped to be a gentle way in.
