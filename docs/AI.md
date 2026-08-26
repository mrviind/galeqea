# How GaleQEA analyses requirements — and how to plug in your own key

## The part that does not need AI

"AI reads the requirement and writes the tests" is mostly a claim about
*technique*, and the techniques that find real defects are mechanical once the
input domain is known — which the requirement usually states outright:

> The password must be between **8 and 64 characters**.
> The order status must be **one of Draft, Submitted or Approved**.
> The system shall **reject an upload of more than 5 MB**.

So GaleQEA parses the domain by rule and applies the classical techniques to it:

| Technique | What it produces |
|---|---|
| **Boundary value analysis** | For every stated limit: the value below it, on it, and above it. Off-by-one at a boundary is the defect this exists to catch. |
| **Equivalence partitioning** | One representative per class — inside the range, below it, above it, zero, negative, wrong type; every member of an enumeration plus one outsider. |
| **Format partitioning** | Realistic edge cases per format: `user@` and `@example.com` for email, `2026-02-29` for dates, a Luhn-failing card number. |
| **Decision tables** | Every combination of the conditions in an "if A and B and C" requirement, because testing only the all-true path leaves the rest unverified. |

**Boundary arithmetic is computed, never generated.** Asking a language model
for "one less than 8" is asking it to occasionally be wrong about something a
subtraction settles.

Every derived value carries the technique that produced it, so a reviewer sees
*"65 — just above the maximum, boundary value analysis"* rather than *"the AI
suggested 65"*. The first can be judged; the second can only be trusted.

### Where wording is ambiguous, it says so

"The order status must be one of Draft, Submitted or Approved" does not say
whether matching is case sensitive. GaleQEA marks `draft` as **unspecified**
rather than invalid, and raises it as a question. Asserting a verdict the
specification never gave bakes a guess into a test, and the guess then reads as
agreed behaviour forever after.

### Reading the sentence, not just the numbers

Two things that catch naive parsers:

- **"reject an upload of more than 5 MB"** states a *maximum* of 5, using the
  same comparative as "at least 5" which states a minimum. Reading the
  comparative alone puts every boundary value on the wrong side of the limit.
  A rejection verb in the same clause inverts the threshold.
- **"must not accept fewer than 8 characters"** is a double negative that
  resolves to a minimum of 8.

### What a model adds

When one is configured, it deepens the scaffold rather than replacing it:
sharper titles, edge cases implied but not written down, domain-specific data,
and concrete executable steps. It never gets to *drop* a requirement — anything
it omits is restored from the deterministic baseline, because a requirement
losing its test to an enrichment pass is a silent regression in coverage.

**Every requirement ends up with at least one test**, and that is verified after
generation rather than assumed. Three separate steps could otherwise drop a
requirement's last proposal, and a requirement arriving with zero coverage looks
exactly like success.

---

## Bring your own key

GaleQEA never proxies your traffic. The key is yours, the request goes straight
to your provider, and nothing about it reaches anyone else.

### It is stored, not remembered

Keys are sealed in the same envelope-encrypted vault as every other credential:
a per-secret data key wrapped by the installation master key, AES-256-GCM, with
the scope and name as additional authenticated data.

The API **never returns a key**. Reads give a hint — `sk-loc…-key` — so you can
confirm which credential is wired up without it appearing in a response body, a
log line, or a browser's memory.

### It is verified before it is stored

Saving probes the key against the provider first. A key that does not work is
refused **with the provider's own error message**, at the moment you are looking
at the form. Storing it anyway trades a five-second wait now for a failure in
the middle of an unattended run at 2am, with the real error long gone.

### Scoped per project, with a global fallback

One key per provider per scope. A project key overrides the global one, so one
workspace can run against a local Ollama model while another uses a hosted
provider — with different models, endpoints and budgets.

### Budgeted before the spend, not after

An optional monthly cap is enforced from the usage ledger **before** a request
is made. A budget you discover you have exceeded is a bill.

When a cap is reached, GaleQEA degrades to No-AI mode rather than failing the
run — and says why:

> the monthly budget for openai_compatible ($5.00) is spent ($6.20 so far).
> Raise it in Settings → Model, or wait for the month to roll over. Everything
> that does not need a model still works.

Reporting that as "no model configured" would send you to the wrong setting.

### Cost attribution

Every model call is written to the usage ledger with its provider, model, agent
role, token counts and cost. Settings → Model breaks the last 30 days down by
what the spend actually went on — requirement analysis, semantic healing, RCA
ranking, exploration.

### Providers

Anthropic · OpenAI · Google Gemini · Azure OpenAI · Ollama · any
OpenAI-compatible endpoint · the local Claude Code bridge.

For the Claude Code bridge, no key is stored at all: it shells out to the CLI you
authenticated yourself, and GaleQEA is barred from using it on a non-loopback
deployment. See the README on why that boundary exists.

---

## What still works with no key at all

This is not a degraded mode — it is the default, and most of the product lives
here:

- Requirement extraction from PDF, DOCX, XLSX, Markdown
- **Boundary, partition, format and decision-table test design** (this page's
  first half)
- The coverage guarantee and the traceability matrix
- Plain-English commands, running, scheduling, reporting
- Deterministic locator healing
- Statistical flake detection and regression triage
- Evidence-based root-cause analysis
- Exploratory testing with deterministic finding checks
- Visual regression via structural comparison
