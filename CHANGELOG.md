# Changelog

All notable changes to GaleQEA are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
"Public API" here means the REST API, the MCP tool surface, and the `galeqea` CLI —
a breaking change to any of those is a MAJOR bump.

Entries are grouped under **Added**, **Changed**, **Deprecated**, **Removed**,
**Fixed**, and **Security**. Because the project uses
[Conventional Commits](https://www.conventionalcommits.org/), this file is intended
to be generated from commit history at release time rather than hand-maintained.

## [Unreleased]

### Added
- First-run on-ramp: type a URL in the chat (`test https://your-app.com`) and
  GaleQEA drives a real browser to it, checks it loads cleanly, and sets it as the
  project target — with **no model and no test authoring**. If the URL is missing
  (`test my site`), the chat asks for it and uses the answer (conversational
  slot-filling). Built on the existing run pipeline via a built-in smoke probe that,
  as the product's own diagnostic, does not pass through the approval gate.
- Chat/MCP tool `open_test_pull_request` with an `@applier("git.open_pr")`: renders
  approved test cases to Playwright files and opens a pull request on the connected
  git provider — but only after the `git.open_pr` approval is granted. The agent
  proposes the PR; a human lets it out.
- Community-health files following common open-source practice: Code of Conduct
  (Contributor Covenant 2.1), `SUPPORT.md`, this changelog, GitHub issue forms,
  `CODEOWNERS`, Dependabot configuration, a CodeQL workflow, and release automation.
- `make start`: a single first-run command that installs every dependency, downloads
  Chromium, builds the UI, and launches on `:8080`.

### Changed
- `CONTRIBUTING.md` now documents the Conventional Commits standard (types, scopes,
  breaking-change convention) alongside the existing DCO sign-off requirement.

## [0.1.0] - 2026-08-24

### Added
- Initial public release: AI-first, local-first test automation with a structural
  approval gate, tiered locator healing, a persistent App Model, a hash-chained
  audit ledger, a No-AI default mode, and an MCP server exposing the same tool
  registry that powers the built-in chat.

[Unreleased]: https://github.com/mrviind/galeqea/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mrviind/galeqea/releases/tag/v0.1.0
