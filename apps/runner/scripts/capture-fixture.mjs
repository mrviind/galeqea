/**
 * Regenerates apps/api/tests/fixtures_recorded_session.ndjson.
 *
 *   cd examples/demo-app && python3 -m http.server 8765 &
 *   node apps/runner/scripts/capture-fixture.mjs > apps/api/tests/fixtures_recorded_session.ndjson
 *
 * The recording tests assert against a *real* capture rather than a hand-written
 * one, so that a change to the in-page capture script shows up as a test failure
 * instead of quietly making the fixture fiction. This script is how the fixture
 * is refreshed: Playwright plays the part of the human, dispatching trusted
 * events that the capture listeners see exactly as they would see a person's.
 *
 * The sequence below is chosen to exercise every compression rule: a focus-only
 * click, a partial edit, a credential field, a submit that follows its own
 * click, an Alt+click assertion, and a navigation caused by a link.
 */
import { chromium } from 'playwright';
import { record } from '../src/recorder.mjs';

const browser = await chromium.launch({ headless: true });
const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });
const page = await context.newPage();

const session = { id: 'rec-test-1', startUrl: 'http://127.0.0.1:8765/index.html', maxActions: 200, maxMinutes: 2, discover: true };
const done = record({ context, page, session, artifacts: '/tmp' });

// Give the initial navigation + init script time to settle.
await page.waitForTimeout(900);

// --- the "human" ---
await page.click('#email');                                  // focus-only click: must be collapsed
await page.fill('#email', 'a');                              // partial typing: must be collapsed
await page.fill('#email', 'ravi@example.com');
await page.click('[data-testid="cc-number-v2"]');
await page.fill('[data-testid="cc-number-v2"]', '9111222233334444');   // name="card" -> secret
await page.click('[data-testid="pay-button"]');              // submit -> should absorb the submit event
await page.waitForTimeout(600);
await page.click('#order', { modifiers: ['Alt'] });          // Alt+click assertion
await page.waitForTimeout(200);
await page.click('a[href="/account.html"]').catch(() => {});
await page.waitForTimeout(800);

await page.close();
await done;
await context.close();
await browser.close();
