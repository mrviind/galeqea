# Running GaleQEA in CI

GaleQEA exits non-zero when a run fails, so it drops into any pipeline.

## Basic

```yaml
- run: pip install -e apps/api && cd apps/runner && npm ci && npx playwright install --with-deps chromium
- run: galeqea run "the smoke tests" --environment staging
```

## Predictive selection on a pull request

Run the subset most likely to catch this change, and let the log record what was
skipped:

```yaml
- id: changed
  run: echo "paths=$(git diff --name-only origin/main... | paste -sd, -)" >> $GITHUB_OUTPUT
- run: galeqea run --changed "${{ steps.changed.outputs.paths }}"
```

Output includes the coverage note:

> Running 12 of 84 approved tests (14%). Omitted tests are listed in full — this
> selection reduces time, not accountability.

Use the full suite on `main`. Selection is for feedback speed on a branch, not a
replacement for a complete run before release.

## Importing an existing report

For pipelines that already run tests elsewhere, or for air-gapped installs that
cannot reach a CI API:

```bash
curl -X POST localhost:8080/api/projects/DEMO/integrations/ci/report \
  -H 'Content-Type: application/json' \
  -d "{\"content\": $(jq -Rs . < junit.xml)}"
```

JUnit XML, Playwright JSON and Allure JSON are detected automatically.

## Verifying the audit ledger

Worth running on a schedule if you rely on the ledger for compliance:

```yaml
- run: galeqea audit --verify-only    # non-zero if the hash chain is broken
```

## The AI-code review gate

GaleQEA's own pipeline enforces on itself what the product enforces on its users:
any commit touching generated or AI-assisted code requires human review before
merge. See `.github/workflows/ci.yml` and [CONTRIBUTING.md](../CONTRIBUTING.md).
