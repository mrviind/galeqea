import { test } from 'node:test';
import assert from 'node:assert/strict';
import { trimAriaSnapshot, estimateTokens } from './pagestate.mjs';

// A representative raw ariaSnapshot(): landmarks, headings, a long list, decorative
// noise (generic/text/paragraph/img), and interactive controls.
const RAW = `- banner:
  - heading "Welcome to the shop" [level=1]
  - link "Home"
  - link "Account"
- main:
  - heading "Products" [level=2]
  - paragraph: Some marketing copy that the model does not need at all here
  - generic:
    - text: decorative
  - list:
    - listitem:
      - link "Item 1"
    - listitem:
      - link "Item 2"
    - listitem:
      - link "Item 3"
    - listitem:
      - link "Item 4"
    - listitem:
      - link "Item 5"
    - listitem:
      - link "Item 6"
  - textbox "Search products"
  - button "Add to cart" [disabled]
  - img "decorative banner"
- contentinfo:
  - link "Privacy"`;

test('keeps interactive + structural, drops decorative', () => {
  const { text } = trimAriaSnapshot(RAW);
  assert.match(text, /banner/);
  assert.match(text, /heading "Welcome to the shop" \[h1\]/);
  assert.match(text, /button "Add to cart"/);
  assert.match(text, /textbox "Search products"/);
  // decorative / prose is gone
  assert.doesNotMatch(text, /marketing copy/);
  assert.doesNotMatch(text, /decorative/);
  assert.doesNotMatch(text, /paragraph/);
});

test('assigns stable ref=eN handles to interactive nodes only', () => {
  const { text, refs } = trimAriaSnapshot(RAW);
  assert.match(text, /link "Home" \[ref=e1\]/);
  // refs map only interactive roles
  assert.equal(refs.e1.role, 'link');
  assert.ok(Object.values(refs).every((r) => r.name !== undefined));
  // headings/landmarks never get a ref
  assert.doesNotMatch(text, /heading[^\n]*ref=/);
});

test('collapses repeated siblings after three', () => {
  const { text } = trimAriaSnapshot(RAW);
  // 6 "Item N" links → 3 shown + a "… ×3 more links" summary
  const items = (text.match(/link "Item \d"/g) || []).length;
  assert.equal(items, 3);
  assert.match(text, /… ×3 more links/);
});

test('carries a disabled state but caps text at 80 chars', () => {
  const long = `- main:\n  - button "${'x'.repeat(200)}"`;
  const { text } = trimAriaSnapshot(long);
  const name = /button "([^"]*)"/.exec(text)[1];
  assert.equal(name.length, 80);
  assert.match(trimAriaSnapshot(RAW).text, /button "Add to cart" \[ref=e\d\] \[disabled\]/);
});

test('is deterministic (same input → identical output)', () => {
  assert.equal(trimAriaSnapshot(RAW).text, trimAriaSnapshot(RAW).text);
});

test('honours the token budget', () => {
  // Distinct headings don't collapse, so a huge tree really does exceed the budget
  // and must be truncated (interactive siblings would have collapsed instead).
  const many = ['- main:'];
  for (let i = 0; i < 400; i++) many.push(`  - heading "Section ${i} of the document" [level=2]`);
  const { stateTokens, truncated } = trimAriaSnapshot(many.join('\n'), { budgetTokens: 200 });
  assert.ok(stateTokens <= 210, `state ${stateTokens} over budget`);
  assert.equal(truncated, true);
});

test('stays well under 500 tokens for a normal page', () => {
  const { stateTokens } = trimAriaSnapshot(RAW);
  assert.ok(stateTokens < 500, `state ${stateTokens} tokens`);
  assert.ok(estimateTokens('abcd') === 1);
});
