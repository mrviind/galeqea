#!/usr/bin/env node
/**
 * Accessibility e2e for GaleQEA's own UI. We ship an a11y tool, so we run it on
 * ourselves. Drives a running app with Playwright and axe-core over every primary
 * page, and fails the build on any serious or critical violation.
 *
 *     make a11y                       # against http://localhost:8080
 *     BASE_URL=http://localhost:8080 node apps/runner/a11y.mjs
 *
 * Requires a running GaleQEA (`make up`) and Chromium (`make setup` installs it).
 */
import { chromium } from 'playwright';
import { AxeBuilder } from '@axe-core/playwright';

const BASE = process.env.BASE_URL || 'http://localhost:8080';
const PAGES = [
  ['Workspace', '/'],
  ['Runs', '/runs'],
  ['Tests', '/tests'],
  ['Releases', '/releases'],
  ['Settings', '/settings'],
];
// WCAG A/AA is the bar we hold others to, so it's the bar we hold ourselves to.
const TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];
const FAIL_ON = new Set(['serious', 'critical']);

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
const page = await context.newPage();
let failures = 0;

for (const [name, path] of PAGES) {
  await page.goto(BASE + path, { waitUntil: 'networkidle' });
  await page.waitForTimeout(600); // let cards/panels settle
  const { violations } = await new AxeBuilder({ page }).withTags(TAGS).analyze();
  const blocking = violations.filter((v) => FAIL_ON.has(v.impact));
  if (blocking.length === 0) {
    console.log(`✓ ${name} (${path}) has no serious/critical a11y violations`);
  } else {
    failures += blocking.length;
    console.log(`✗ ${name} (${path}) has ${blocking.length} blocking violation(s):`);
    for (const v of blocking) {
      console.log(`    [${v.impact}] ${v.id}: ${v.help} (${v.nodes.length} node(s))`);
      if (process.env.A11Y_VERBOSE) {
        for (const n of v.nodes.slice(0, 4)) {
          console.log(`      · ${n.target.join(' ')}`);
          const fix = (n.any[0]?.message || n.failureSummary || '').split('\n')[0];
          if (fix) console.log(`        ${fix}`);
        }
      }
    }
  }
}

await browser.close();
if (failures) {
  console.error(`\n${failures} blocking accessibility violation(s). GaleQEA must pass its own axe.`);
  process.exit(1);
}
console.log('\nAll pages clean at WCAG A/AA (no serious or critical violations).');
