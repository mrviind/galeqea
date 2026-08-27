# Changelog

All notable changes to QE Agent are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
"Public API" here means the REST API, the MCP tool surface, and the `galeqea` CLI —
a breaking change to any of those is a MAJOR bump.

Entries are grouped under **Added**, **Changed**, **Deprecated**, **Removed**,
**Fixed**, and **Security**. Because the project uses
[Conventional Commits](https://www.conventionalcommits.org/), this file is intended
to be generated from commit history at release time rather than hand-maintained.

## [0.1.1](https://github.com/mrviind/qe-agent/compare/v0.1.0...v0.1.1) (2026-08-27)


### Added

* **agent:** first-run URL on-ramp with conversational slot-filling ([ebda38e](https://github.com/mrviind/qe-agent/commit/ebda38e26fbee06f39437554a785845f8883b7ba))
* **integrations:** wire open_test_pull_request through the git.open_pr gate ([f6e8e35](https://github.com/mrviind/qe-agent/commit/f6e8e357714238f130392b0357727eacefedd8b2))
* **web:** funky-yellow brand and wind-mark favicon ([b0bc022](https://github.com/mrviind/qe-agent/commit/b0bc0227560559f4736237fb01c4ea0c8867950d))


### Fixed

* **agent:** correct on-ramp chat rendering and command preview ([f2433ba](https://github.com/mrviind/qe-agent/commit/f2433baa9deee6296aec08bcc4dbf8d155001589))
* **build:** add apps/api/README.md so a clean editable install succeeds ([27930a0](https://github.com/mrviind/qe-agent/commit/27930a0e86161283dcfe787f2dc65aae31415a03))
* **build:** copy apps/api/README.md in the Docker image so the editable install works ([50dbcea](https://github.com/mrviind/qe-agent/commit/50dbceaba007c233b1d626f5899d009da95a2bfa))
* **ci:** ignore major-version dependency bumps in Dependabot ([25f7054](https://github.com/mrviind/qe-agent/commit/25f70541ce30f73eba5ebb81fbd599f834fd8931))
* **web:** stop the Copilot composer clipping its placeholder ([0289b48](https://github.com/mrviind/qe-agent/commit/0289b485c3a040ac3dae5b9e46fabb6dbab8be64))

## [Unreleased]

### Added
- First-run on-ramp: type a URL in the chat (`test https://your-app.com`) and
  QE Agent drives a real browser to it, checks it loads cleanly, and sets it as the
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

[Unreleased]: https://github.com/mrviind/qe-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mrviind/qe-agent/releases/tag/v0.1.0
