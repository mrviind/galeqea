# Architecture

This document records the decisions that shaped GaleQEA and the reasoning behind
them. Where a decision has a cost, the cost is stated.

---

## 1. Tests are data, not code

A GaleQEA test is a row plus an ordered list of typed steps. Each step carries:

- an **action** from a small, auditable vocabulary (~30 verbs);
- a **semantic intent** in plain language — *"submit the payment form"*;
- an optional **element reference** into the App Model;
- a **locator ladder**, ordered most-durable-first.

Storing tests as code is the obvious alternative and it is what most tools do. It
fails at four things simultaneously:

| Capability | With code | With data |
|---|---|---|
| Healing | rewrite source, hope the AST edit is safe | re-point a reference |
| Replay | re-execute and hope it reproduces | steps and results are records |
| Export | you are locked into the language you generated | render to any target |
| Review | diff a generated file | diff intent, ladder and rationale |

**The cost:** the step vocabulary is a ceiling. Anything it cannot express needs a
plugin-provided action or an approved `script` step. That is a real constraint, and
it is deliberate — an unbounded escape hatch would make every other property here
unenforceable.

---

## 2. The App Model: heal an element, not a selector

Most self-healing patches a *selector inside a test*. If forty tests reference the
same "Pay" button, a redesign breaks forty tests and produces forty independent
heals — forty analyses, forty reviews, forty chances to disagree.

GaleQEA maintains a persistent **App Model**: a graph of screens and elements, where
each element owns its intent, role, accessible name, attribute fingerprint and
locator ladder. A step points at `element_id`. When a locator breaks:

1. The element is re-identified **once**.
2. The heal is proposed **once**, with its evidence.
3. One human approval repairs **every** test that references it.

The API reports `tests_repaired` on apply, because that number is the point.

### How it gets populated

Two sources, both automatic.

**Ordinary runs.** Every step that resolves an element reports
what it found — role, accessible name, tag, fingerprint, and the locator that
actually worked — and every navigation reports the screen. The server upserts
those observations and, when a step's own locator is *already* in the element's
ladder, binds the step to the element. That binding is deliberately restricted
to the case where it is provably a no-op: the same element resolves by the same
means either way, so nothing the test does or asserts changes. Any other binding
would be a behavioural change and belongs behind the approval gate.

The payoff is measurable and the API reports it. Rename a `data-testid` that two
tests depend on:

```
HEAL PROPOSALS: 1          ← one, not one per test
ONE APPROVAL: tests_repaired=2, ladder_depth=2
RE-RUN: both pass, healed=False
```

**This was a real hole, not a design note.** The first version of this document
made the "heal once, repair many" argument while nothing populated the model —
in practice it was always empty, and healing silently degraded to the per-test
patching the model exists to avoid. Two follow-on defects came out of fixing it:
heal proposals were filed once *per test* (recreating the churn inside the
review queue), and `tests_repaired` counted heal events rather than the tests
bound to the element, which under-reported by exactly the amount deduplication
saves. Both are covered in `tests/test_healing.py`.

**Session recording**, which is the more interesting of the two. The in-page
capture script computes the ladder and fingerprint in the page, so the observation
it emits is the same shape a resolved step emits — which means a recorded test
enters the model *at the moment it is authored*, not on its first successful run.
That inverts the usual order: a recorded test is repairable before it has ever
been executed, and a UI churn that lands between recording and the first run is
healed rather than being a broken new test.

**The cost:** discovery adds one page evaluation per interacting step. Measured
at low single-digit milliseconds, and it can be disabled per run.

---

## 3. Healing is a ladder, and mostly free

```
Tier 0  Locator ladder      alternate rungs already on the element
                            → free, deterministic, no analysis at all
Tier 1  Fingerprint match   score live candidates against the stored fingerprint
                            → deterministic, offline, explainable, no model
Tier 2  Semantic re-resolve  give a model the intent + accessibility snapshot
                            → only when Tier 1 is weak or ambiguous
Tier 3  Refuse              a clean failure beats a confident wrong click
```

Two properties follow, and both are load-bearing:

- **A healthy suite never pays for healing.** Tiers 1–3 run only after a real miss,
  so the common path stays at native Playwright speed.
- **Healing works with no model configured.** Tier 1 rescues most real breakage —
  renamed test ids, restructured markup, changed classes.

### The scoring bug worth knowing about

The first implementation scored candidates as a weighted sum over eight attributes.
A renamed `data-testid` produced this:

```
role      match     0.22 × 1.0 = 0.22
name      match     0.30 × 1.0 = 0.30
testid    MISMATCH  0.20 × 0.0 = 0.00
tag       match     0.05 × 1.0 = 0.05
text      absent    0.10 × 0.0 = 0.00     ← no text on an <input>
position  absent    0.05 × 0.0 = 0.00     ← no stored bounding box
ancestry  absent    0.03 × 0.0 = 0.00     ← never captured
                    ──────────────────
                    total 0.57  → below the 0.72 floor → refused
```

The correct element was an obvious winner — the runner-up scored 0.27 — but three
signals scored zero because they were **absent from the fingerprint**, not because
they disagreed. Missing evidence was being counted as evidence against, which made a
sparse fingerprint permanently unhealable.

The fix is to normalise over the weights of the signals that were actually
comparable, and to accept a *decisive* winner at a lower absolute score when enough
of the fingerprint was comparable. The same case now scores 0.86 and heals. Every
heal reports which signals it could compare, so a reviewer can see the difference.

Regression coverage: `apps/api/tests/test_healing.py::test_decisive_winner_heals_without_a_model`.

---

## 4. The approval gate is structural

```python
def _assert_can_decide(db, req, decider):
    if decider.is_machine or decider.role == Role.AGENT:
        raise SelfApprovalError(...)
    if req.requested_by == decider.id:
        raise SelfApprovalError(...)
    if settings.allow_ai_self_approval:      # config may only make this stricter
        raise ApprovalError(...)
```

Three properties:

1. **Agents cannot write.** Every state-changing tool declares an `approval_action`
   and files a request instead of executing. The applier is unreachable from any
   state other than `APPROVED`.
2. **The rule cannot be configured away.** `GALEQEA_ALLOW_AI_SELF_APPROVAL` exists
   only so that setting it raises an error — a deliberate tripwire for anyone who
   goes looking for the flag.
3. **It holds across every entry point.** The MCP server, the CLI and the HTTP API
   all route through the same registry, so an external MCP client gets the gate too.
   Verified in `test_governance.py`.

Actions are risk-tiered, and anything unlisted defaults to `HIGH` — a newly added
action fails closed, not open.

---

## 5. No-AI mode is the default, and it is real

`GALEQEA_AI_MODE=no_ai` means zero LLM calls and zero outbound network traffic. The
work to make that genuinely useful, rather than a disabled shell, is concentrated in
three places:

- **`ai/router.py`** — a deterministic plain-English command router. Most of what
  people type at a test platform has a recognisable shape, and matching it with
  rules is faster, free, offline and perfectly predictable. It runs *first* even
  when a model is configured, saving a round trip on the common path.
- **`intelligence/`** — flakiness, triage, anomaly detection, selection and the
  first RCA pass are all statistical. No model was ever needed for them.
- **`ai/embeddings.py`** — a deterministic hashed n-gram encoder backs semantic
  de-duplication and memory recall when no embedding provider exists. It is weaker
  than a learned model at paraphrase, and good enough for the near-duplicate
  detection that actually matters.

---

## 6. The runner is deliberately ignorant

`apps/runner` executes steps and reports facts. It has no database, no credentials,
no notion of approval rules, and no model. When it needs a decision it *asks*, over
an NDJSON request/response channel on stdin/stdout:

```
runner  → heal_request    { intent, candidates, ariaSnapshot, failedLadder }
        ← { ok: true, locator: {...}, strategy: "fingerprint", score: 0.86 }

runner  → judge_request   { question, ariaSnapshot, screenshot }
        ← { ok: true, verdict: "pass", confidence: 0.82 }

runner  → handoff_request { reason, url }        ← parks until a human resumes
```

This is what makes intent-based healing and pause-and-attach possible without
pushing secrets or policy into the browser process. It also means the runner can be
replaced wholesale — the protocol is the contract.

**Two bugs this shape produced during development,** both worth recording:

- The plan was first sent on stdin. The runner read stdin to EOF; the supervisor
  kept stdin open for the reply channel. Instant deadlock. The plan now goes to a
  file, and stdin is exclusively the reply channel.
- The reply channel's `readline` interface kept Node's event loop alive after the
  run finished, so the process never exited and the supervisor waited forever for a
  stdout EOF. The runner now closes the channel explicitly, *and* the supervisor
  finalises on the `run_end` event rather than trusting the process to exit.

---

## 7. A run that executed nothing is never green

The single most damaging bug a test platform can have is reporting a pass when
nothing ran — it manufactures confidence out of an infrastructure failure. During
development a wrong runner path produced exactly that: zero results, status
`passed`.

`_finish()` now checks that a run which selected N tests produced at least one
result, and marks it `ERROR` with the runner's exit code and stderr tail otherwise:

> the runner exited (code 1) without executing any of the 3 selected test(s), so
> this run proves nothing.

---

## 8. Data model notes

- **`audit_events.seq` is the primary key.** On SQLite only an `INTEGER PRIMARY KEY`
  autoincrements; a ledger whose ordering is implicit rather than enforced is not a
  ledger. Found by an integrity error on the very first write.
- **All timestamps use `UTCDateTime`**, a `TypeDecorator` that normalises to
  timezone-aware UTC in both directions. SQLite returns naive datetimes even for
  `DateTime(timezone=True)`, and mixing them raises *"can't subtract offset-naive and
  offset-aware datetimes"* at the worst possible moment — mid-run finalisation.
  Normalising at the column boundary means no call site has to think about it.
- **Embeddings are JSON columns.** pgvector is used when available; the JSON
  fallback keeps SQLite installs fully functional.

---

## 9. Autonomous exploratory testing

Exploration answers a different question from a test. A test asks *"does this
still do what we agreed?"*; exploration asks *"what does this do that we never
agreed about?"* — so its output is findings to triage, not a verdict, and it is
kept out of the pass-rate statistics where it would mean nothing.

It reuses the runner protocol exactly: the runner observes and acts, the server
decides. A whole new mode needed no new transport — the runner asks
`explore_decide` the same way it asks `heal_request`.

**Two strategies, one loop.** The deterministic strategy is the default and
needs no model: prefer untouched controls, probe empty inputs with boundary
values, follow links toward unseen screens, back out of dead ends. It cannot
judge whether a message is confusing, but it finds real defects the same way
every time — which a model cannot promise. The model strategy uses the same loop
with the next action chosen from a *server-supplied candidate list*; it can
never invent a selector, which bounds both hallucination and page-borne
injection.

**Two tiers of refusal.** `DESTRUCTIVE` (delete, revoke, sign out) is never
clicked, in any environment. `TRANSACTIONAL` (pay, place order, transfer) is
blocked by default but unlockable per session — on staging, the submit button is
precisely where the interesting behaviour lives, and refusing it there means
exploration only ever sees the form. Whatever is skipped is *reported*, because
a coverage hole the user cannot see is worse than one they can. The refusal is
enforced in code after the model answers, not merely requested in the prompt.

**Three traversal bugs worth recording**, all found by running it rather than
reading it:

- Backing out past the entry page lands on `about:blank`, which has nothing to
  click, which looks like a dead end, which suggests backing out. That loop
  consumed 15 of an 18-step budget on the first run. Leaving the application now
  returns to the base URL, and three consecutive backtracks restart from the top.
- `about:blank` was also raising a *dead end* finding — a false positive that
  teaches people to ignore the list.
- Findings needed de-duplication at three levels: within a batch (two unlabelled
  inputs are one `form-label` finding), within a session, and across sessions
  (exploring weekly would otherwise file the same defect fifty-two times).

**A finding is not a rumour.** Each one carries the ordered actions that reach
it, so promoting it to a regression test is a rendering step — the trail is
already a list of steps. Promotion is idempotent, because a double-click
otherwise produced two identical tests.

---

## 10. Visual regression: structure first, pixels second

The ordering is the whole design, and one measurement justifies it. Delete a
required field from a checkout form and re-screenshot:

```
changed 0.93% of the image, 1 region: 656×112 at (400, 240)
structural: lost_controls = ["textbox: Card number"]   -> severity: breaking
```

Under one per cent. Any pixel threshold loose enough to tolerate anti-aliasing
is also loose enough to miss that, which is why pixel-only visual testing gets
muted within a month. The accessibility tree, by contrast, says plainly that an
interactive control disappeared — so structural comparison decides severity and
pixels supply the *location*.

**Regions, not confetti.** The image is divided into a 16px grid; a cell counts
as changed only when ~6% of it changed, and adjacent cells are flood-filled into
boxes. A reviewer gets three rectangles, not forty thousand pixels. Per-channel
tolerance absorbs sub-pixel rendering differences between runs on the same
machine.

**Two parser bugs worth recording.** Playwright's aria snapshot emits two
shapes — `- textbox "Card number"` and `- text: Your order is confirmed`. The
first version read only the quoted form, so a paragraph changing from "Your
order is confirmed" to "Your order failed" registered as *no structural change
at all*. The second: `- heading "Acme Checkout" [level=1]` failed to parse
because the trailing attribute broke the end anchor, silently dropping every
heading from the diff.

**Nothing was reviewable before this.** `record_snapshot` computed a diff,
returned it, and dropped it — so the feature was unreachable. Comparisons are
now rows, and an unchanged screen is stored as `auto_passed` rather than not
stored at all, so "did this screen get checked?" has an answer.

Baselines are **versioned, not overwritten**: accepting a change should never
destroy the evidence of what it replaced.

---

## 11. Two more ways in, one review board

Requirement ingestion was the first route into a suite. Two more were added, and
neither of them needed a new transport, a new approval action or a new test model
— which is the argument for having made tests data in the first place.

### Session recording rides the run protocol

Recording is a third mode on the same NDJSON channel as execution and
exploration. The runner launches headed, injects a capture script, and streams
`recorded_action` events; the supervisor routes them by the same `_on_<type>`
dispatch it uses for `step_end`. Nothing about the transport changed.

The capture script is the part worth reading. Three decisions carry it:

- **Capture phase, passive, never preventing.** Listeners register with
  `{capture: true, passive: true}`, so a page that calls `stopPropagation` in its
  own handlers cannot hide an interaction, and the recorder cannot alter what the
  application does. What you record is what happens without it.
- **A ladder, not a selector.** Six rungs, best first, with ambiguous ones index-
  pinned. This is the same structure `engine/healing.py` scores against, which is
  why a recorded element is a first-class App Model citizen and not an import.
- **Secrets are refused at source.** A password or payment field's value is never
  read; the step carries a generator reference instead. Redacting afterwards would
  mean the value existed in a buffer, an event and a log first.

Compression happens on the server, in `engine/record.py`, because it is a set of
*judgement calls* — which click was only a focus, which navigation was an outcome
— and judgement calls belong where they can be tested and argued with. Both the
raw stream and the compressed proposal are stored, so a compression bug is
falsifiable rather than invisible.

**Two defects found while building it, both only visible by running it:** the
initial navigation was emitted twice (the explicit post-`goto` emit raced
`framenavigated`, and the duplicate carried the post-redirect URL, so it looked
like two different navigations rather than one); and the generated test was named
after the last thing clicked, which is usually some incidental navigation — the
session ended on "Account settings" and the test covering a payment was named
after it. Buttons now outrank links, and the last one wins.

### An OpenAPI document is already most of a test plan

`engine/openapi.py` reads the spec's own constraints as test-design input and
hands them to the same `intelligence/testdata.py` the requirement path uses. No
model is involved, and none is needed: the document states which parameters are
required, what their bounds are and what each response must contain.

The generated cases file as `test.create` proposals — the existing applier, not a
new one — so an API test is reviewed, versioned, exported and healed exactly like
a UI test.

Three judgement calls are recorded in code because they are the ones that turn a
generated suite from useful into dangerous:

1. **Never default to the spec's `servers` URL.** It names production. A suite
   containing `POST` operations and injection probes that defaults to production
   is an incident. The project environment wins; if there is none, the UI says so.
2. **Never fetch a remote `$ref`.** A specification is untrusted input. Resolving
   a URL inside it lets the document choose what the process connects to.
3. **Only assert non-reflection for HTML responses.** A JSON API that echoes a
   stored value back is behaving correctly. Asserting against that would have
   failed conformant services — a bug in the first version of this generator,
   caught by reading the output rather than by a test.

The runner's `api_request` step was widened to match: status sets rather than a
single code (a malformed request is legitimately 400 *or* 422), response-schema
conformance, header and JSON-path assertions, a response-time budget, and
reflection checks. Every declared expectation is evaluated before anything is
thrown, so one run reports "wrong status *and* two schema violations" instead of
making a reviewer fix one, re-run, and discover the next.

---

## 12. Known limitations

Stated plainly, because a list of features without them is marketing:

- **Plugin sandboxing is cooperative.** In-process Python cannot be a real security
  boundary. Plugins install disabled, capabilities are granted explicitly, and a
  changed checksum revokes the grant — but an untrusted plugin should run out of
  process. The `external` transport exists for that and is not yet implemented.
- **Exploration cannot judge meaning without a model.** The deterministic
  strategy finds broken links, console errors, 5xx responses, unlabelled
  controls, dead ends and silently discarded input — facts. It cannot tell you
  an error message is *confusing*. That needs the model strategy.
- **OCR is not bundled.** Image-only requirement documents are reported as needing
  OCR rather than silently ingested empty.
- **Visual comparison is viewport-sized, not element-scoped.** A snapshot
  captures the whole screen, so an unrelated change elsewhere on the page lands
  in the same review item. Per-element baselines would scope it properly.
- **Predictive selection learns only from failures.** A passing test says nothing
  about whether it covers a change, so correlations build slowly on a healthy suite.
- **Recording covers one window.** A flow that opens a second window — a payment
  pop-up, an OAuth handshake — is captured up to that point and then annotated
  with a note saying the rest needs authoring by hand. Driving two contexts from
  one recorded step list is not modelled.
- **Recorded assertions are opt-in.** Alt+click is the only way to say "this must
  be here". GaleQEA will not invent assertions from a browsing session, so a
  recording where nobody asserted anything produces a test that proves the flow
  completes, not that it is correct. It says so, but it is still a real gap.
- **API generation reads the top level of a request body.** Nested-object
  mutation multiplies the case count far faster than it adds signal, so a deeply
  nested payload gets its outer fields exercised and its inner ones only through
  the happy path.
- **The generated `Authorization` header is a placeholder.** Secured operations
  emit `${GALEQEA_API_TOKEN}` for a reviewer to map to a vault secret. Emitting a
  fabricated token would make every secured test 401 and look like a product
  defect.
