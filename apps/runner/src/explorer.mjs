/**
 * Exploratory driver.
 *
 * The runner observes and acts; the supervisor decides. Each iteration reports
 * what the page looks like and asks for one action, exactly as healing asks for
 * one locator. That keeps every policy question — which strategy, which model,
 * what counts as destructive — on the server, and leaves the browser process
 * with no knowledge of any of it.
 */

import { emit, log, ask } from './protocol.mjs';

const INTERACTIVE = [
  'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea',
  '[role=button]', '[role=link]', '[role=tab]', '[role=menuitem]',
  '[role=checkbox]', '[role=radio]', '[role=switch]', '[role=combobox]',
  '[contenteditable=true]',
].join(',');

/** Everything the server needs to choose the next action. */
async function observe(page) {
  const candidates = await page.evaluate((selector) => {
    function accessibleName(el) {
      const aria = el.getAttribute('aria-label');
      if (aria) return aria.trim();
      const labelledBy = el.getAttribute('aria-labelledby');
      if (labelledBy) {
        const parts = labelledBy.split(/\s+/)
          .map((id) => document.getElementById(id)?.textContent || '').join(' ');
        if (parts.trim()) return parts.trim();
      }
      if (el.labels && el.labels.length) return (el.labels[0].textContent || '').trim();
      return (el.getAttribute('placeholder') || el.getAttribute('title')
        || el.getAttribute('alt') || el.value || el.innerText || '').trim().slice(0, 120);
    }
    function impliedRole(el) {
      const explicit = el.getAttribute('role');
      if (explicit) return explicit;
      const tag = el.tagName.toLowerCase();
      if (tag === 'a') return el.hasAttribute('href') ? 'link' : 'generic';
      if (tag === 'button') return 'button';
      if (tag === 'select') return 'combobox';
      if (tag === 'textarea') return 'textbox';
      if (tag === 'input') {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        if (t === 'checkbox') return 'checkbox';
        if (t === 'radio') return 'radio';
        if (['submit', 'button', 'reset'].includes(t)) return 'button';
        return 'textbox';
      }
      return 'generic';
    }

    const out = [];
    for (const el of document.querySelectorAll(selector)) {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;
      const style = getComputedStyle(el);
      if (style.visibility === 'hidden' || style.display === 'none') continue;
      out.push({
        role: impliedRole(el),
        name: accessibleName(el),
        text: (el.innerText || '').trim().slice(0, 80),
        type: el.getAttribute('type') || '',
        href: el.getAttribute('href') || '',
        testId: el.getAttribute('data-testid') || '',
        value: el.value || '',
        disabled: !!el.disabled,
      });
      if (out.length >= 60) break;
    }
    return out;
  }, INTERACTIVE);

  let ariaSnapshot = '';
  try {
    ariaSnapshot = await page.locator('body').ariaSnapshot();
  } catch { /* older engines */ }

  const a11y = await page.evaluate(() => {
    const issues = [];
    for (const img of document.querySelectorAll('img')) {
      if (!img.hasAttribute('alt')) issues.push({ rule: 'image-alt', node: img.outerHTML.slice(0, 120) });
    }
    for (const input of document.querySelectorAll('input:not([type=hidden]),select,textarea')) {
      const labelled = input.labels?.length || input.getAttribute('aria-label')
        || input.getAttribute('aria-labelledby') || input.getAttribute('title');
      if (!labelled) issues.push({ rule: 'form-label', node: input.outerHTML.slice(0, 120) });
    }
    for (const btn of document.querySelectorAll('button,[role=button]')) {
      const name = (btn.innerText || btn.getAttribute('aria-label') || '').trim();
      if (!name) issues.push({ rule: 'button-name', node: btn.outerHTML.slice(0, 120) });
    }
    if (!document.documentElement.getAttribute('lang')) issues.push({ rule: 'html-lang', node: '<html>' });
    return issues.slice(0, 10);
  });

  return { url: page.url(), title: await page.title(), candidates, ariaSnapshot, a11y };
}

/** Turn a candidate description back into something clickable. */
function locate(page, candidate) {
  if (candidate.testId) return page.getByTestId(candidate.testId).first();
  if (candidate.name && candidate.role && candidate.role !== 'generic') {
    return page.getByRole(candidate.role, { name: candidate.name, exact: false }).first();
  }
  if (candidate.name) return page.getByText(candidate.name, { exact: false }).first();
  return null;
}

export async function explore({ page, session, artifacts }) {
  const maxSteps = session.maxSteps || 30;
  const consoleErrors = [];
  const networkFailures = [];

  page.on('console', (m) => {
    if (m.type() === 'error') consoleErrors.push({ text: m.text().slice(0, 400) });
  });
  page.on('pageerror', (e) => consoleErrors.push({ text: `pageerror: ${String(e).slice(0, 400)}` }));
  page.on('requestfailed', (r) => networkFailures.push({
    url: r.url().slice(0, 300), failure: r.failure()?.errorText || 'failed',
  }));
  page.on('response', (r) => {
    if (r.status() >= 500) networkFailures.push({ url: r.url().slice(0, 300), status: r.status() });
  });

  const started = Date.now();
  await page.goto(session.baseUrl, { waitUntil: 'domcontentloaded', timeout: 30000 });
  emit('explore_started', { sessionId: session.id, url: page.url(), charter: session.charter });

  let previous = null;
  let steps = 0;

  for (; steps < maxSteps; steps++) {
    const loadStart = Date.now();
    const observation = await observe(page);
    observation.loadMs = Date.now() - loadStart;
    // Drain diagnostics so each observation reports only what is new.
    observation.consoleErrors = consoleErrors.splice(0);
    observation.networkFailures = networkFailures.splice(0);

    const reply = await ask('explore_decide', {
      sessionId: session.id,
      step: steps,
      observation,
      previous: previous ? { url: previous.url, candidates: previous.candidates } : null,
    }, 120000);

    if (!reply?.ok || reply.action === 'finish') {
      emit('explore_log', { sessionId: session.id, message: reply?.rationale || 'exploration complete' });
      break;
    }

    const candidate = typeof reply.targetIndex === 'number'
      ? observation.candidates[reply.targetIndex] : null;
    const label = candidate ? (candidate.name || candidate.role) : reply.action;

    emit('explore_step', {
      sessionId: session.id, step: steps, action: reply.action,
      target: label, value: reply.value, rationale: reply.rationale, url: page.url(),
    });

    try {
      if (reply.action === 'back') {
        await page.goBack({ waitUntil: 'domcontentloaded', timeout: 15000 });
      } else if (reply.action === 'goto' && reply.url) {
        await page.goto(reply.url, { waitUntil: 'domcontentloaded', timeout: 20000 });
      } else if (candidate) {
        const locator = locate(page, candidate);
        if (!locator) { previous = observation; continue; }
        if (reply.action === 'fill') await locator.fill(reply.value ?? '', { timeout: 8000 });
        else await locator.click({ timeout: 8000 });
      }
      // Let the app settle; exploration is not a race.
      await page.waitForLoadState('domcontentloaded', { timeout: 8000 }).catch(() => {});
      await page.waitForTimeout(220);
    } catch (err) {
      emit('explore_log', {
        sessionId: session.id,
        message: `step ${steps} (${reply.action} ${label}) did not complete: ${String(err.message || err).slice(0, 160)}`,
      });
    }

    previous = observation;
  }

  // A final observation so the last action's consequences are examined too.
  const final = await observe(page);
  final.consoleErrors = consoleErrors.splice(0);
  final.networkFailures = networkFailures.splice(0);
  final.isTerminal = true;
  await ask('explore_decide', {
    sessionId: session.id, step: steps, observation: final, previous, finalPass: true,
  }, 60000);

  let screenshot = null;
  try {
    screenshot = `${artifacts}/explore-${session.id}.png`;
    await page.screenshot({ path: screenshot, fullPage: false, timeout: 10000 });
  } catch { screenshot = null; }

  emit('explore_finished', {
    sessionId: session.id, steps, durationMs: Date.now() - started,
    url: page.url(), screenshot,
  });
}
