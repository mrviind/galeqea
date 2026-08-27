# Releasing

Releases are automated from commit history, so the changelog and version number are
derived — not hand-maintained. This works because every commit follows
[Conventional Commits](CONTRIBUTING.md#commit-messages--conventional-commits).

## How a release happens

1. Commits land on `main` (each already Conventional-Commit shaped).
2. The **Release** workflow (`release-please`) keeps an open **release PR** that
   accumulates the pending changes, the next version number, and the generated
   `CHANGELOG.md` entry.
3. A maintainer reviews and **merges** that PR. Merging tags `vX.Y.Z` and publishes
   the GitHub release. Nothing ships without a human merging — the same shape as the
   product's approval gate.

## What the version number means

QE Agent follows [Semantic Versioning](https://semver.org/). The **public API** is
the REST API, the MCP tool surface, and the `galeqea` CLI.

- `fix:` → PATCH
- `feat:` → MINOR
- `feat!:` / `BREAKING CHANGE:` → MAJOR

**Pre-1.0 (0.x):** while the major version is `0`, a breaking change bumps the
**minor**, and a feature bumps the **patch** — the API is still stabilising. The
release config encodes this (`bump-minor-pre-major`).

## Versioned artefacts

The tag is the source of truth for a release. The per-package versions in
`apps/api/pyproject.toml`, `apps/web/package.json`, and `apps/runner/package.json`
are bumped as part of the release PR; when packages are published to PyPI / npm,
add those steps to the Release workflow behind the same merged-PR gate.
