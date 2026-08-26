# Authoring tests without writing them

GaleQEA has four ways into a test suite. All four end in the same place — a
`PROPOSED` test case waiting for a human — because the approval gate does not
make exceptions for the route a proposal arrived by.

| Route | Input | Needs a model? |
|---|---|---|
| [Requirement ingestion](../README.md) | DOCX / PDF / XLSX / Markdown | No |
| **Session recording** | A person using the application | No |
| **API specification import** | OpenAPI 3.x, JSON or YAML | No |
| Chat | Plain English | Optional |

This document covers the middle two.

---

## Session recording

*Author → Record a session.*

A headed browser opens. Use the application the way a tester would. Close the
window when you are done, and GaleQEA compiles what it saw into a step list.

### What makes this different from `playwright codegen`

Codegen writes a **code file**. The moment it is written it is frozen: the
selectors it chose are the selectors it will use forever, and repairing it means
editing source.

GaleQEA records **typed step data with a locator ladder**. That difference is
what everything else hangs off:

- every element is bound into the [App Model](../ARCHITECTURE.md) as it is
  touched, so a recorded test is repairable *before it has ever been run*;
- healing repairs the element once and every test referencing it follows;
- export to Playwright, pytest or Robot is a rendering of the same data, not a
  rewrite.

### The locator ladder

Each captured element carries up to six ways to find it, best first:

1. `data-testid` / `data-test` / `data-cy` / `data-qa` — the only attribute a
   team puts there *for* tests
2. ARIA role + accessible name — survives restyling; what a screen reader uses
3. associated `<label>` text
4. `placeholder`, `alt`, `title`
5. exact text, but only for controls whose text *is* their identity (buttons,
   links, tabs, menu items)
6. a short structural CSS path — last, because it is the rung that breaks

Where a rung matches more than one element the index is pinned, so a test never
resolves ambiguously against whichever node happened to come first.

`composedPath()` is used to find the target, so a control inside a web component
is captured as the control rather than as its shadow host.

### Recording an assertion

**Alt+click** anything. That records `expect_visible`, plus `expect_text` when
the element's text is stable enough to be worth asserting — text containing a
digit is skipped, because an order number or a total changes every run and
asserting it guarantees a false failure.

A recording with no Alt+clicks produces a test that proves the flow completes
without an error, not that it produced the right result. GaleQEA says so in the
proposal's rationale rather than inventing assertions to fill the gap.

### Credentials are never captured

Password inputs, anything the page marks with a credential or payment
`autocomplete` value, and fields whose name suggests a secret are replaced with a
generator reference **at the point of capture**. The typed value is never read,
so it cannot reach the database, an export, or a log.

The step carries `{"generate": {"field": "card"}}`. At plan time that resolves to
a value from the [data factory](#synthetic-test-data) — for a field named `card`,
a Luhn-valid number that no payment network can route.

### What compression does

A raw capture stream is a transcript, and a transcript makes a bad test:

```
click(#email) · fill(#email,"r") · fill(#email,"ra") · … · click(button) · submit(form) · navigate(/done)
```

Four rules collapse that, and each one is conservative — where a collapse would
change what the test exercises, the step is kept:

| Rule | Why |
|---|---|
| A click that only focused a field about to be filled | `fill` focuses on its own |
| Successive edits to one field | Only the value it ended up with matters |
| A `submit` immediately after the click or Enter that caused it | One intent recorded twice |
| Repeated navigation to the same location | Redirect chains and routers announcing themselves |

A navigation the tester *caused* becomes an `expect_url` assertion, not another
`goto` — re-navigating would skip whatever the click was supposed to do.

URLs are stored as paths, so a recorded test is not welded to the host it was
recorded against.

The counts are reported (`2 focus click event(s) collapsed`) and the raw stream
is kept alongside the compressed one, so a compression rule you disagree with is
something you can argue with rather than something you have to trust.

---

## API specification import

*Author → Import an API spec.*

An OpenAPI document already states the operations, which parameters are required,
their types and bounds, which status codes are legitimate and what each response
body must look like. That is most of a test suite, and extracting it needs no
model.

**Analyse** writes nothing — it reports what *would* be generated so you can see
the shape before approving a hundred cases. **File for review** creates them.

### What is generated per operation

| Kind | What it asserts |
|---|---|
| **contract** | The declared success status *and* that the response body conforms to the declared schema |
| **required-missing** | One call per required parameter and per required body property, omitted → 4xx |
| **boundary / format** | `minLength`, `maxLength`, `minimum`, `maximum`, `enum` and `format` read as test-design input |
| **unauthenticated** | Where the operation declares security: no credentials → 401 or 403, never 200 |
| **injection** | Hostile strings must be handled, not crashed on |

Schema conformance is the half hand-written API suites usually skip, and the
half that catches a backend quietly renaming or retyping a field. Violations are
reported with the JSON pointer that locates them:

```
$.total: expected integer, got string
$.items[2].price: -3 is below minimum 0
$.status: "weird" is not one of ["open","shipped","cancelled"]
```

### Where the tests point

At the **project environment**, not the `servers` entry in the specification. A
published spec almost always names production, and a generated suite that
defaults to calling production — with write operations and injection probes in
it — is an incident waiting to be filed as a feature request. If the project has
no environment URL, the UI says so rather than picking one.

### Reflection is only asserted for HTML responses

An injection probe sent to a JSON API and echoed back in the response body is
**correct behaviour** — the value was stored and returned. Asserting against that
would fail conformant APIs. So the reflection check is added only where the
operation declares an HTML response; everywhere else the probe asserts "must not
5xx", which is true regardless.

### Two things the parser refuses

- **Remote `$ref`.** A specification is untrusted input, and dereferencing a URL
  inside it would let the document choose what this process connects to. Remote
  references are reported as unresolved, never fetched.
- **Unbounded recursion.** Reference cycles are depth-limited, so a
  self-referential schema cannot hang the parser.

Both, plus operations that declare no 2xx or no response schema, are surfaced as
*specification* defects — they limit what can be asserted, and a reviewer should
know coverage is thinner there rather than assume otherwise.

---

## Synthetic test data

`galeqea.intelligence.testdata` generates values for both routes above, and for
requirement-derived cases.

### Reproducible

Every value is a pure function of a seed string. `value("person_name", "REQ-014/customer/0")`
returns the same name on every machine, forever. A generator seeded from the
clock produces a different person every run, which makes the one failure you most
need to reproduce — the one caused by an apostrophe in a surname — unreproducible.

blake2b rather than `random.Random`: the Mersenne stream is not contractually
stable across CPython versions, and a data set that silently changed under a
Python upgrade would invalidate every stored expectation.

### Safe by construction

| Kind | Constraint |
|---|---|
| E-mail | RFC 2606 / RFC 6761 reserved hosts only — guaranteed never to resolve |
| Telephone | NANP `555-01xx`, Ofcom `07700 900xxx` — the ranges reserved for fiction |
| Payment card | Luhn-valid, but major industry identifier `9`, which ISO/IEC 7812 reserves for national assignment and no scheme issues |
| IP address | RFC 5737 documentation range |

A card number that passes a checksum test but can never reach a payment network
is exactly what a test needs. No name, address or identifier in the corpus was
copied from a person or a data set; they are built from invented syllables and
generic street words — including the awkward ones (`D'Arcy`, `Jean-Luc`, `Zoë`,
`van der Meer`, a single-character given name) that are what actually break forms.

### Semantic inference

Field kind is read from the declared type first, then the name, in any casing —
`customerEmailAddress`, `customer_email_address` and `"Customer email address"`
all resolve to `email`. Ordering matters and is deliberate: `card code` is a CVV,
`card expiry` is an expiry date, and only then is a bare `card` a PAN.

Each kind also knows how it can be *wrong*, with the reason:

```
'plainaddress'          no @ separator
'user@example'          domain has no dot-separated TLD
'user name@example.com' unquoted space in the local part
```

Injection probes are kept separate from invalid values, because the expected
outcome is the opposite: they should round-trip intact and appear escaped, never
be rejected and never be executed.

### Using a generator in a step

```json
{"action": "fill", "value": {"generate": {"kind": "email", "unique": true}}}
```

Resolved at plan time, not in the runner — so the runner stays a dumb executor
and the run record shows the exact value used. Without `unique`, the value is
seeded on the step so a re-run sends the same thing and a failure reproduces
exactly; with it, the run id joins the seed, for cases like sign-up where the
value must differ every time.
