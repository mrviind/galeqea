# Contributing to GaleQEA

## Setup

```bash
make setup      # Python + Node deps, Chromium, UI build
make test       # 59 tests, ~0.3s
make lint       # ruff + tsc + node --check
make dev        # API with reload on :8080, Vite on :5173
```

## The AI-code review gate

GaleQEA is built with AI assistance, and it enforces on itself what it enforces on
its users: **no AI-generated change reaches `main` without a human who read it.**

- Mark AI-assisted commits: `Assisted-by: <tool>` in the trailer.
- CI flags such PRs and requires a review from someone who is not the author.
- Approving a PR means you understood the change, not that the tests passed.

This is the same principle as the approval gate in the product. A project that
would not apply its own rule to itself has not really made the argument.

## What good work looks like here

**Say what you are unsure about.** The most valuable review comments on this project
have been about confidence calibration, not syntax. If a heuristic is a guess,
name it as a guess in the code.

**Never report success you did not verify.** The `_finish()` guard exists because
a run once reported `passed` while executing nothing. Prefer a loud failure to a
quiet pass, everywhere, always.

**Failure messages must be actionable.** Compare:

```
✗  Error: request failed
✓  the runner exited (code 1) without executing any of the 3 selected test(s),
   so this run proves nothing. Cannot find module 'playwright'
```

**Keep No-AI mode whole.** Any feature that only works with a model must degrade
visibly, not silently. `NoAIModeError` carries the message the user sees; make it
say what still works.

**Tests over assertions in prose.** A claim in a docstring is a hope. A claim in
`tests/` is a fact. The healing scoring bug was found by a test written to describe
the case, not by reading the code.

## Style

**Python** — Ruff, line length 100, `from __future__ import annotations`, type hints
throughout. Comments explain *why*, never *what*; if a comment restates the code,
delete it.

**TypeScript** — strict mode. No `any` in exported signatures. Components are
self-contained; group Tailwind classes layout → spacing → typography → colour →
effects.

**JavaScript (runner)** — ESM, no build step, no dependencies beyond Playwright.
The runner must stay simple enough to audit in one sitting.

## Adding a tool

Tools live in `apps/api/galeqea/ai/toolset.py` and are automatically exposed to the
chat agent *and* the MCP server. A state-changing tool needs two halves, placed
adjacently so a missing one is obvious:

```python
@registry.register(
    "archive_suite",
    description="Propose archiving a suite.",       # written for a model to read
    parameters={"properties": {"suite_id": {"type": "string"}}, "required": ["suite_id"]},
    read_only=False,
    approval_action="suite.archive",                 # ← the gate
    risk=RiskTier.MEDIUM,
)
def archive_suite(args, ctx):
    return {"suite_id": args["suite_id"]}            # files the request; does not act

@applier("suite.archive")                            # ← runs only after approval
def _apply_archive(db, request):
    ...
```

Add the risk tier to `ACTION_RISK`. Anything unlisted defaults to `HIGH` — a new
action fails closed.

## Adding a step action

1. Add the verb to `StepAction` in `models/testing.py`.
2. Implement it in `apps/runner/src/executor.mjs`.
3. Render it in `engine/codegen.py` for every export target, or emit an explicit
   `// Unsupported in export` comment — never silently drop a step.
4. Add a case to `tests/test_codegen.py`.

## Developer Certificate of Origin

Contributions are accepted under the [DCO](https://developercertificate.org/). Sign
off each commit:

```bash
git commit -s -m "Add semantic diff to visual baselines"
```

By signing off you certify you wrote the contribution or have the right to submit it
under Apache-2.0. Do not contribute code copied from another project, or reproduce
another product's trademarks, logos or proprietary test artefacts.

## Reporting bugs

The most useful report includes the run id and `galeqea doctor` output. If the audit
ledger reports a break, include `galeqea audit` — that is a serious finding and we
will treat it as one.
