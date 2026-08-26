# Demo application under test

A deliberately tiny checkout page so GaleQEA's full pipeline can be exercised
end to end without pointing it at anything real.

```bash
make demo          # serves on http://localhost:8765
```

It contains, on purpose:

- a `getByLabel`-addressable email field (no test id) — exercises the label rung;
- a `data-testid` card field — the element used in the healing demo, where the
  test id is renamed to force a heal;
- a `getByRole('button', { name: 'Confirm payment' })` submit control;
- two validation paths with distinct, actionable error messages;
- an accessible confirmation region with an order number and total.

Rename `data-testid="card-input"` in `index.html` and re-run a test that
references it: healing re-identifies the element from its role, accessible name
and tag, proposes the new locator with its evidence, and the test passes while
the change waits for your approval.
