/**
 * Step executor.
 *
 * Executes one test's step list against a Playwright page and streams the
 * result. Three behaviours here are what separate this from a plain script
 * runner:
 *
 *  - **Heal-on-miss.** A failed locator triggers candidate harvesting and a
 *    round trip to the supervisor, which may return a replacement locator to
 *    use *for this run only*. Persisting it is a separate, human-approved act.
 *  - **Semantic assertions.** `expect_semantic` sends the accessibility
 *    snapshot to the supervisor to be judged. With no model configured it is
 *    reported `unverified`, never silently passed.
 *  - **Handoff.** A `handoff` step parks the live browser and waits for a human
 *    to clear an SSO prompt, a CAPTCHA or a shadow-DOM blocker, then resumes.
 */

import { emit, log, ask } from './protocol.mjs';
import {
  resolve, harvestCandidates, fingerprint, describeRung, buildLocator,
  describeScreen, describeElement,
} from './locator.mjs';
import { validate as validateSchema, readPath } from './schema.mjs';

const DEFAULT_TIMEOUT = 30000;

export class StepFailure extends Error {
  constructor(message, { type = 'assertion', detail = {} } = {}) {
    super(message);
    this.name = 'StepFailure';
    this.failureType = type;
    this.detail = detail;
  }
}

export class Executor {
  constructor({ page, context, testCase, run, artifacts, judge }) {
    this.page = page;
    this.context = context;
    this.testCase = testCase;
    this.run = run;
    this.artifacts = artifacts;
    this.judge = judge;
    this.consoleErrors = [];
    this.networkFailures = [];
    this.healsApplied = [];
    this.cancelled = false;
    // App Model discovery. Observation only - the runner reports what it saw
    // and the supervisor decides what that means.
    this.discover = run.discover !== false;
    this.currentRoute = null;
    this.lastElementRef = null;
    this._wireDiagnostics();
  }

  _wireDiagnostics() {
    // Console and network noise is captured continuously; the RCA engine needs
    // what happened *before* the failing step, not just at it.
    this.page.on('console', (msg) => {
      if (msg.type() === 'error') {
        this.consoleErrors.push({ text: msg.text().slice(0, 500), ts: Date.now() });
      }
    });
    this.page.on('pageerror', (err) => {
      this.consoleErrors.push({ text: `pageerror: ${String(err).slice(0, 500)}`, ts: Date.now() });
    });
    this.page.on('requestfailed', (req) => {
      this.networkFailures.push({
        url: req.url().slice(0, 300),
        method: req.method(),
        failure: req.failure()?.errorText || 'unknown',
        ts: Date.now(),
      });
    });
    this.page.on('response', (res) => {
      if (res.status() >= 500) {
        this.networkFailures.push({ url: res.url().slice(0, 300), status: res.status(), ts: Date.now() });
      }
    });
  }

  async runSteps(steps) {
    const records = [];
    for (let i = 0; i < steps.length; i++) {
      if (this.cancelled) {
        records.push({ index: i, action: steps[i].action, status: 'cancelled' });
        continue;
      }
      const step = steps[i];
      const started = Date.now();
      emit('step_start', {
        testId: this.testCase.id, index: i, action: step.action, intent: step.intent,
      });

      let record;
      try {
        const result = (await this.execute(step, i)) || {};
        record = {
          index: i, stepId: step.id, action: step.action, intent: step.intent,
          status: result.status || 'passed',
          durationMs: Date.now() - started,
          resolvedLocator: result.resolvedLocator || '',
          healApplied: result.healApplied || null,
          logs: result.logs || [],
          artifacts: result.artifacts || [],
          detail: result.detail || {},
        };
      } catch (err) {
        const shot = await this._safeScreenshot(`step-${i}-failure`);
        record = {
          index: i, stepId: step.id, action: step.action, intent: step.intent,
          status: 'failed',
          durationMs: Date.now() - started,
          errorMessage: String(err.message || err).slice(0, 2000),
          errorType: err.failureType || err.name || 'Error',
          detail: err.detail || {},
          artifacts: shot ? [shot] : [],
        };
        emit('step_end', { testId: this.testCase.id, ...record });
        records.push(record);
        if (!step.continue_on_failure && !step.optional) return { records, failed: record };
        continue;
      }

      emit('step_end', { testId: this.testCase.id, ...record });
      records.push(record);
    }
    return { records, failed: null };
  }

  // ------------------------------------------------------------------ //
  async execute(step, index) {
    const timeout = step.timeout_ms || DEFAULT_TIMEOUT;
    const value = step.value || {};
    const options = step.options || {};

    switch (step.action) {
      case 'note':
        return { status: 'skipped', logs: [`manual step: ${step.intent}`] };

      case 'goto': {
        const url = this._resolveUrl(value.url || value.text || '');
        const response = await this.page.goto(url, {
          waitUntil: options.waitUntil || 'domcontentloaded', timeout,
        });
        if (response && response.status() >= 400) {
          throw new StepFailure(`navigation to ${url} returned HTTP ${response.status()}`, {
            type: 'navigation', detail: { status: response.status(), url },
          });
        }
        await this._observeScreen();
        return { resolvedLocator: url, detail: { status: response?.status() ?? 0 } };
      }

      case 'wait_for': {
        if (value.url) {
          await this.page.waitForURL(value.url, { timeout });
          return {};
        }
        if (value.timeout_ms) {
          await this.page.waitForTimeout(Math.min(value.timeout_ms, 30000));
          return {};
        }
        const { locator, description } = await this._locate(step, index, { timeout });
        await locator.waitFor({ state: options.state || 'visible', timeout });
        return { resolvedLocator: description };
      }

      case 'click': case 'double_click': case 'hover':
      case 'check': case 'uncheck': case 'fill': case 'type':
      case 'select': case 'upload': case 'press': {
        return await this._interact(step, index, timeout);
      }

      case 'scroll': {
        if (value.selector || step.target?.ladder) {
          const { locator, description } = await this._locate(step, index, { timeout });
          await locator.scrollIntoViewIfNeeded({ timeout });
          return { resolvedLocator: description };
        }
        await this.page.mouse.wheel(0, value.pixels ?? 600);
        return {};
      }

      case 'expect_visible': {
        const { locator, description, healApplied } = await this._locate(step, index, { timeout });
        await locator.waitFor({ state: 'visible', timeout });
        return { resolvedLocator: description, healApplied };
      }

      case 'expect_text': {
        const { locator, description, healApplied } = await this._locate(step, index, { timeout });
        const actual = (await locator.innerText({ timeout })).trim();
        const expected = String(value.text ?? '');
        const ok = options.mode === 'exact' ? actual === expected : actual.includes(expected);
        if (!ok) {
          throw new StepFailure(
            `expected text ${options.mode === 'exact' ? 'to equal' : 'to contain'} ${JSON.stringify(expected)}, got ${JSON.stringify(actual.slice(0, 300))}`,
            { detail: { expected, actual: actual.slice(0, 500) } },
          );
        }
        return { resolvedLocator: description, healApplied, detail: { actual: actual.slice(0, 200) } };
      }

      case 'expect_value': {
        const { locator, description } = await this._locate(step, index, { timeout });
        const actual = await locator.inputValue({ timeout });
        if (actual !== String(value.text ?? '')) {
          throw new StepFailure(`expected value ${JSON.stringify(value.text)}, got ${JSON.stringify(actual)}`,
            { detail: { expected: value.text, actual } });
        }
        return { resolvedLocator: description };
      }

      case 'expect_url': {
        const actual = this.page.url();
        const expected = String(value.url ?? value.text ?? '');
        const ok = options.mode === 'exact' ? actual === expected
          : new RegExp(expected.replace(/[.*+?^${}()|[\]\\]/g, '\\$&').replace(/\\\*/g, '.*')).test(actual)
            || actual.includes(expected);
        if (!ok) throw new StepFailure(`expected URL to match ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`,
          { detail: { expected, actual } });
        return { detail: { actual } };
      }

      case 'expect_count': {
        const ladder = step.target?.ladder || [];
        const loc = buildLocator(this.page, ladder[0]);
        const actual = await loc.count();
        const expected = Number(value.count ?? 0);
        const cmp = options.comparison || 'eq';
        const ok = cmp === 'gte' ? actual >= expected : cmp === 'lte' ? actual <= expected : actual === expected;
        if (!ok) throw new StepFailure(`expected count ${cmp} ${expected}, got ${actual}`,
          { detail: { expected, actual, comparison: cmp } });
        return { detail: { actual } };
      }

      case 'expect_attribute': {
        const { locator, description } = await this._locate(step, index, { timeout });
        const actual = await locator.getAttribute(value.attribute, { timeout });
        if (actual !== value.text) {
          throw new StepFailure(`expected @${value.attribute} to be ${JSON.stringify(value.text)}, got ${JSON.stringify(actual)}`,
            { detail: { attribute: value.attribute, expected: value.text, actual } });
        }
        return { resolvedLocator: description };
      }

      case 'expect_semantic':
        return await this._expectSemantic(step, index);

      case 'snapshot':
        return await this._snapshot(step, index);

      case 'api_request':
        return await this._apiRequest(step, value, timeout);

      case 'set_storage': {
        await this.context.addCookies(value.cookies || []);
        if (value.localStorage) {
          await this.page.evaluate((entries) => {
            for (const [k, v] of Object.entries(entries)) localStorage.setItem(k, v);
          }, value.localStorage);
        }
        return {};
      }

      case 'network_condition':
        return await this._networkCondition(value);

      case 'route_fault':
        return await this._routeFault(value);

      case 'assert_a11y':
        return await this._assertA11y(step, options);

      case 'assert_perf':
        return await this._assertPerf(value, options);

      case 'handoff':
        return await this._handoff(step, index);

      case 'script': {
        // Only reachable for steps whose script body passed the approval gate;
        // the supervisor strips unapproved script steps before dispatch.
        if (!step.options?.approved) {
          throw new StepFailure('script step reached the runner without approval - refusing to evaluate', { type: 'policy' });
        }
        const result = await this.page.evaluate(new Function(`return (${value.body})`)());
        return { detail: { result: JSON.stringify(result ?? null).slice(0, 500) } };
      }

      default:
        throw new StepFailure(`unsupported action: ${step.action}`, { type: 'unsupported' });
    }
  }

  // ------------------------------------------------------------------ //
  async _interact(step, index, timeout) {
    const { locator, description, healApplied } = await this._locate(step, index, { timeout });
    const value = step.value || {};
    const options = step.options || {};

    switch (step.action) {
      case 'click': await locator.click({ timeout, force: options.force, button: options.button }); break;
      case 'double_click': await locator.dblclick({ timeout }); break;
      case 'hover': await locator.hover({ timeout }); break;
      case 'check': await locator.check({ timeout, force: options.force }); break;
      case 'uncheck': await locator.uncheck({ timeout, force: options.force }); break;
      case 'fill': await locator.fill(String(value.text ?? ''), { timeout }); break;
      case 'type': await locator.pressSequentially(String(value.text ?? ''), { delay: options.delay ?? 30, timeout }); break;
      case 'press': await locator.press(String(value.key ?? 'Enter'), { timeout }); break;
      case 'select': await locator.selectOption(value.options ?? String(value.text ?? ''), { timeout }); break;
      case 'upload': await locator.setInputFiles(value.files ?? [], { timeout }); break;
    }

    // An interaction may have navigated. Checking the URL is free; describing
    // the screen only happens when it actually changed.
    if (this.discover && this.page.url() !== this.lastUrl) {
      await this._observeScreen({ viaElement: this.lastElementRef, action: step.action });
    }
    return { resolvedLocator: description, healApplied };
  }

  /**
   * Report the screen the page is on. Emitted as an observation, not a claim:
   * the supervisor decides whether this is a known screen or a new one.
   */
  async _observeScreen({ viaElement = null, action = 'navigate' } = {}) {
    if (!this.discover) return;
    try {
      const described = await describeScreen(this.page);
      this.lastUrl = described.url;
      emit('screen_observed', {
        testId: this.testCase.id,
        ...described,
        fromRoute: this.currentRoute,
        viaElement,
        action,
      });
      this.currentRoute = described.url;
    } catch {
      /* discovery must never fail a run */
    }
  }

  /** Report the element a step just resolved, with the locator that worked. */
  async _observeElement(step, locator, rung) {
    if (!this.discover || !rung) return;
    try {
      const [described, print] = await Promise.all([
        describeElement(locator),
        fingerprint(locator),
      ]);
      this.lastElementRef = `${described.role}:${described.accessibleName}`.slice(0, 120);
      emit('element_observed', {
        testId: this.testCase.id,
        stepId: step.id,
        intent: step.intent,
        url: this.page.url(),
        locator: rung,
        fingerprint: print,
        box: print?.box ?? null,
        ...described,
      });
    } catch {
      /* discovery must never fail a run */
    }
  }

  /**
   * Resolve a step's target, healing if the ladder is exhausted.
   *
   * A heal returned here is used for *this run only* and reported upward as a
   * proposal. Nothing is written back to the App Model from inside the runner.
   */
  async _locate(step, index, { timeout }) {
    const ladder = step.target?.ladder || [];
    if (!ladder.length) {
      throw new StepFailure('step has no locator ladder', { type: 'authoring' });
    }

    const first = await resolve(this.page, ladder, { timeout, requireVisible: step.target?.requireVisible !== false });
    if (first.ok) {
      if (first.drifted) {
        // Succeeded, but not on the primary rung - worth telling the App Model.
        emit('locator_drift', {
          testId: this.testCase.id, stepIndex: index, elementId: step.element_id || null,
          from: describeRung(ladder[0]), to: first.description, rungIndex: first.rungIndex,
        });
      }
      await this._observeElement(step, first.locator, first.rung);
      return { locator: first.locator, description: first.description, healApplied: null };
    }

    // Every rung failed. Harvest, ask, and retry once with whatever comes back.
    log('warn', `locator ladder exhausted for step ${index} (${step.intent || step.action})`);
    const target = {
      ...(step.target?.fingerprint || {}),
      role: step.target?.role,
      accessible_name: step.target?.accessible_name,
      tag: step.target?.tag,
      test_id: step.target?.test_id,
      intent: step.intent,
    };

    let candidates = [];
    let ariaSnapshot = '';
    try {
      candidates = await harvestCandidates(this.page, target);
      ariaSnapshot = await this._ariaSnapshot();
    } catch (err) {
      log('warn', `candidate harvest failed: ${err.message}`);
    }

    const response = await ask('heal_request', {
      testId: this.testCase.id,
      testCaseId: this.testCase.testCaseId || this.testCase.id,
      stepId: step.id, stepIndex: index,
      elementId: step.element_id || null,
      intent: step.intent, action: step.action,
      failedLadder: ladder.map(describeRung),
      attempts: first.attempts,
      candidates,
      ariaSnapshot: ariaSnapshot.slice(0, 8000),
      url: this.page.url(),
    }, 120000);

    if (response?.ok && response.locator) {
      const healed = await resolve(this.page, [response.locator], { timeout: 5000 });
      if (healed.ok) {
        const heal = {
          strategy: response.strategy || 'unknown',
          from: describeRung(ladder[0]),
          to: healed.description,
          score: response.score ?? 0,
          transient: true,
        };
        this.healsApplied.push(heal);
        emit('heal_applied', { testId: this.testCase.id, stepIndex: index, ...heal });
        await this._observeElement(step, healed.locator, response.locator);
        return { locator: healed.locator, description: healed.description, healApplied: heal };
      }
    }

    const reason = response?.reason ? ` (${response.reason})` : '';
    throw new StepFailure(
      `could not find the element for "${step.intent || step.action}"${reason}. ` +
      `Tried: ${ladder.map(describeRung).join(' → ')}`,
      { type: 'locator', detail: { attempts: first.attempts, candidates: candidates.slice(0, 5) } },
    );
  }

  // ------------------------------------------------------------------ //
  async _ariaSnapshot() {
    // The accessibility tree is both far cheaper in tokens than raw HTML and a
    // better description of what a user can actually perceive.
    try {
      return await this.page.locator('body').ariaSnapshot();
    } catch {
      return await this.page.evaluate(() => document.body.innerText.slice(0, 6000));
    }
  }

  async _expectSemantic(step, index) {
    const snapshot = await this._ariaSnapshot();
    const shot = await this._safeScreenshot(`semantic-${index}`);
    const response = await ask('judge_request', {
      testId: this.testCase.id, stepIndex: index,
      question: step.expected || step.intent,
      ariaSnapshot: snapshot.slice(0, 12000),
      screenshot: shot?.path || null,
      url: this.page.url(),
    }, 120000);

    if (!response?.ok) {
      // No model available. Reporting "unverified" is honest; reporting "passed"
      // would be a lie that hides a real coverage gap.
      return {
        status: 'unverified',
        logs: [`semantic assertion not evaluated: ${response?.reason || 'no judge available'}`],
        artifacts: shot ? [shot] : [],
      };
    }
    if (response.verdict === 'fail') {
      throw new StepFailure(`semantic assertion failed: ${response.reasoning || step.expected}`, {
        type: 'semantic',
        detail: { confidence: response.confidence, reasoning: response.reasoning },
      });
    }
    return {
      status: response.verdict === 'pass' ? 'passed' : 'needs_review',
      detail: { confidence: response.confidence, reasoning: response.reasoning },
      artifacts: shot ? [shot] : [],
    };
  }

  async _snapshot(step, index) {
    const name = step.value?.name || `snapshot-${index}`;
    const shot = await this._safeScreenshot(name, { fullPage: step.options?.fullPage !== false });
    const aria = await this._ariaSnapshot();
    emit('visual_snapshot', {
      testId: this.testCase.id, name, path: shot?.path,
      ariaSnapshot: aria.slice(0, 8000), url: this.page.url(),
    });
    return { artifacts: shot ? [shot] : [], detail: { name } };
  }

  /**
   * Issue an HTTP request and check the whole contract, not just the status.
   *
   * Every declared expectation is evaluated before anything is thrown, so one
   * run reports "wrong status *and* two schema violations" rather than making a
   * reviewer fix the status, re-run, and discover the schema problem next.
   */
  async _apiRequest(step, value, timeout) {
    const started = Date.now();
    const response = await this.context.request.fetch(this._resolveUrl(value.url), {
      method: (value.method || 'GET').toUpperCase(),
      data: value.body,
      headers: value.headers || {},
      timeout,
      failOnStatusCode: false,
    });
    const elapsedMs = Date.now() - started;
    const status = response.status();
    const headers = response.headers();
    const body = await response.text();

    let parsed;
    let parseError = null;
    if (/json/i.test(headers['content-type'] || '') || /^[[{]/.test(body.trim())) {
      try { parsed = JSON.parse(body); } catch (err) { parseError = String(err.message || err); }
    }

    const failures = [];

    // A single expected status is the common case; a set is needed because
    // "reject this malformed request" is legitimately 400 or 422 depending on
    // the framework, and pinning one makes the test wrong rather than strict.
    const allowed = value.expect_status_in?.length
      ? value.expect_status_in
      : (value.expect_status ? [value.expect_status] : null);
    if (allowed && !allowed.includes(status)) {
      failures.push(`expected HTTP ${allowed.join(' or ')}, got ${status}`);
    }

    if (value.expect_content_type && !(headers['content-type'] || '').includes(value.expect_content_type)) {
      failures.push(`expected content-type containing '${value.expect_content_type}', got '${headers['content-type'] || '(none)'}'`);
    }

    for (const [name, expected] of Object.entries(value.expect_headers || {})) {
      const actual = headers[name.toLowerCase()];
      if (actual === undefined) failures.push(`header '${name}' is missing`);
      else if (expected && !String(actual).includes(expected)) {
        failures.push(`header '${name}': expected to contain '${expected}', got '${actual}'`);
      }
    }

    if (value.expect_schema) {
      if (parseError) failures.push(`response body is not valid JSON: ${parseError}`);
      else if (parsed === undefined) failures.push('a response schema was declared but the body is not JSON');
      else failures.push(...validateSchema(parsed, value.expect_schema));
    }

    for (const [path, expected] of Object.entries(value.expect_json || {})) {
      const actual = parsed === undefined ? undefined : readPath(parsed, path);
      if (JSON.stringify(actual) !== JSON.stringify(expected)) {
        failures.push(`${path}: expected ${JSON.stringify(expected)}, got ${JSON.stringify(actual)}`);
      }
    }

    // Used by injection probes: the payload must be stored and escaped, never
    // reflected back verbatim where a browser would execute it.
    for (const needle of value.forbid_body_contains || []) {
      if (body.includes(needle)) failures.push(`response reflected ${JSON.stringify(needle)} back unescaped`);
    }

    if (value.expect_max_ms && elapsedMs > value.expect_max_ms) {
      failures.push(`response took ${elapsedMs}ms, budget is ${value.expect_max_ms}ms`);
    }

    const detail = { status, elapsedMs, contentType: headers['content-type'] || '', body: body.slice(0, 1000) };
    if (failures.length) {
      throw new StepFailure(failures.join('; '), { type: 'api', detail: { ...detail, failures } });
    }
    return { detail };
  }

  async _networkCondition(value) {
    const client = await this.context.newCDPSession(this.page).catch(() => null);
    if (!client) return { status: 'skipped', logs: ['network throttling needs a Chromium browser'] };
    if (value.offline) {
      await this.context.setOffline(true);
      return { detail: { offline: true } };
    }
    await client.send('Network.emulateNetworkConditions', {
      offline: false,
      latency: value.latency_ms ?? 0,
      downloadThroughput: (value.download_kbps ?? 0) * 128,
      uploadThroughput: (value.upload_kbps ?? 0) * 128,
    });
    return { detail: value };
  }

  async _routeFault(value) {
    // Deterministic fault injection: does the UI degrade gracefully when this
    // endpoint 500s or hangs? Far more valuable than mocking it to succeed.
    const pattern = value.url_pattern || '**/*';
    await this.page.route(pattern, async (route) => {
      if (value.abort) return route.abort(value.abort === true ? 'failed' : value.abort);
      if (value.delay_ms) await new Promise((r) => setTimeout(r, value.delay_ms));
      return route.fulfill({
        status: value.status || 500,
        contentType: value.content_type || 'application/json',
        body: value.body || JSON.stringify({ error: 'injected fault' }),
      });
    });
    return { detail: { pattern, injected: value } };
  }

  async _assertA11y(step, options) {
    // Structural checks that need no third-party engine, so accessibility
    // coverage is present in the default offline install rather than optional.
    const findings = await this.page.evaluate(() => {
      const issues = [];
      for (const img of document.querySelectorAll('img')) {
        if (!img.hasAttribute('alt')) issues.push({ rule: 'image-alt', node: img.outerHTML.slice(0, 120) });
      }
      for (const input of document.querySelectorAll('input:not([type=hidden]),select,textarea')) {
        const labelled = input.labels?.length || input.getAttribute('aria-label') || input.getAttribute('aria-labelledby') || input.getAttribute('title');
        if (!labelled) issues.push({ rule: 'form-label', node: input.outerHTML.slice(0, 120) });
      }
      for (const btn of document.querySelectorAll('button,[role=button]')) {
        const name = (btn.innerText || btn.getAttribute('aria-label') || btn.getAttribute('title') || '').trim();
        if (!name) issues.push({ rule: 'button-name', node: btn.outerHTML.slice(0, 120) });
      }
      const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')].map((h) => Number(h.tagName[1]));
      for (let i = 1; i < headings.length; i++) {
        if (headings[i] - headings[i - 1] > 1) { issues.push({ rule: 'heading-order', node: `h${headings[i - 1]} → h${headings[i]}` }); break; }
      }
      if (!document.documentElement.getAttribute('lang')) issues.push({ rule: 'html-lang', node: '<html>' });
      return issues;
    });

    const max = options.max_violations ?? 0;
    if (findings.length > max) {
      throw new StepFailure(`${findings.length} accessibility violation(s), allowed ${max}`, {
        type: 'accessibility', detail: { findings: findings.slice(0, 20) },
      });
    }
    return { detail: { findings } };
  }

  async _assertPerf(value, options) {
    const metrics = await this.page.evaluate(() => {
      const nav = performance.getEntriesByType('navigation')[0] || {};
      const paints = Object.fromEntries(performance.getEntriesByType('paint').map((p) => [p.name, Math.round(p.startTime)]));
      return {
        ttfb: Math.round(nav.responseStart || 0),
        domContentLoaded: Math.round(nav.domContentLoadedEventEnd || 0),
        load: Math.round(nav.loadEventEnd || 0),
        firstContentfulPaint: paints['first-contentful-paint'] || 0,
        transferBytes: Math.round(nav.transferSize || 0),
        resourceCount: performance.getEntriesByType('resource').length,
      };
    });
    const budgets = value.budgets || options.budgets || {};
    const breaches = Object.entries(budgets)
      .filter(([k, limit]) => metrics[k] !== undefined && metrics[k] > limit)
      .map(([k, limit]) => `${k}=${metrics[k]}ms exceeds budget ${limit}ms`);
    if (breaches.length) {
      throw new StepFailure(`performance budget exceeded: ${breaches.join('; ')}`, {
        type: 'performance', detail: { metrics, budgets },
      });
    }
    emit('perf_metrics', { testId: this.testCase.id, metrics });
    return { detail: { metrics } };
  }

  async _handoff(step, index) {
    const shot = await this._safeScreenshot(`handoff-${index}`);
    emit('handoff_started', {
      testId: this.testCase.id, stepIndex: index,
      reason: step.intent || 'manual intervention required',
      url: this.page.url(), screenshot: shot?.path || null,
    });
    const response = await ask('handoff_request', {
      testId: this.testCase.id, stepIndex: index,
      reason: step.intent, url: this.page.url(),
      instructions: step.value?.instructions || 'Complete the blocked interaction in the browser window, then resume.',
    }, step.timeout_ms || 900000);

    emit('handoff_ended', { testId: this.testCase.id, stepIndex: index, resumed: !!response?.ok });
    if (!response?.ok) {
      throw new StepFailure(`handoff was not completed: ${response?.reason || 'timed out waiting for a human'}`,
        { type: 'handoff' });
    }
    return { status: 'passed', logs: ['resumed after human handoff'], artifacts: shot ? [shot] : [] };
  }

  // ------------------------------------------------------------------ //
  _resolveUrl(url) {
    if (!url) return this.run.baseUrl || 'about:blank';
    if (/^https?:\/\//i.test(url)) return url;
    const base = (this.run.baseUrl || '').replace(/\/$/, '');
    return `${base}/${String(url).replace(/^\//, '')}`;
  }

  async _safeScreenshot(label, opts = {}) {
    try {
      const file = `${this.artifacts}/${this.testCase.id}-${label.replace(/[^a-z0-9-]/gi, '_')}.png`;
      await this.page.screenshot({ path: file, fullPage: opts.fullPage ?? false, timeout: 10000 });
      const artifact = { kind: 'screenshot', path: file, label };
      emit('artifact', { testId: this.testCase.id, ...artifact });
      return artifact;
    } catch {
      return null;
    }
  }
}
