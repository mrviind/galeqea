#!/usr/bin/env node
/**
 * QE Agent Playwright runner.
 *
 * Reads an execution plan (JSON) and streams NDJSON events. Deliberately has no
 * knowledge of databases, models or approval rules - it executes steps, reports
 * facts, and asks the supervisor whenever a decision is needed.
 *
 *   node cli.mjs --plan plan.json
 *   node cli.mjs --self-test          # no browser required
 */

import fs from 'node:fs';
import path from 'node:path';
import { emit, log, startResponseReader } from './protocol.mjs';
import { Executor } from './executor.mjs';
import { explore } from './explorer.mjs';
import { record } from './recorder.mjs';

const args = parseArgs(process.argv.slice(2));

if (args['self-test']) {
  await selfTest();
  process.exit(0);
}

const plan = JSON.parse(
  args.plan ? fs.readFileSync(args.plan, 'utf8') : await readStdin(),
);

// The reply channel holds stdin open, which keeps Node's event loop alive.
// It must be closed explicitly once the run ends, or the process lingers and
// the supervisor waits forever for a stdout EOF that never arrives.
const replyChannel = startResponseReader();
let cancelled = false;
process.on('galeqea:cancel', () => { cancelled = true; });

try {
  await main(plan);
} catch (err) {
  emit('run_error', { message: String(err.stack || err).slice(0, 4000) });
  process.exitCode = 1;
}

replyChannel.close();
process.stdin.pause();

// --------------------------------------------------------------------- //
async function main(plan) {
  const { chromium, firefox, webkit } = await import('playwright');
  const engines = { chromium, firefox, webkit };

  const artifactsDir = plan.artifactsDir || path.join(process.cwd(), 'artifacts', plan.runId || 'run');
  fs.mkdirSync(artifactsDir, { recursive: true });

  const started = Date.now();

  emit('run_start', {
    runId: plan.runId,
    testCount: (plan.tests || []).length,
    browsers: plan.browsers || ['chromium'],
    baseUrl: plan.baseUrl,
    mode: plan.record ? 'record' : (plan.explore ? 'explore' : 'test'),
  });

  // Recording is a third job again: a person drives, and the output is a step
  // list rather than a verdict or a set of findings. Headed by definition.
  if (plan.record) {
    const engine = engines[(plan.browsers || ['chromium'])[0]] || chromium;
    const browser = await engine.launch({ headless: false });
    const context = await browser.newContext({
      viewport: plan.viewport || null,
      ignoreHTTPSErrors: plan.ignoreHTTPSErrors ?? true,
      recordVideo: plan.record.video ? { dir: artifactsDir } : undefined,
    });
    const page = await context.newPage();
    try {
      await record({ context, page, session: plan.record, artifacts: artifactsDir });
    } catch (err) {
      emit('run_error', { message: `recording failed: ${String(err.stack || err).slice(0, 2000)}` });
    } finally {
      await context.close().catch(() => {});
      await browser.close().catch(() => {});
    }
    emit('run_end', { runId: plan.runId, totals: { total: 0 }, durationMs: Date.now() - started, cancelled });
    return;
  }

  // Exploration is a different job from execution: a charter and a budget
  // rather than a list of steps, and findings rather than a verdict.
  if (plan.explore) {
    const engine = engines[(plan.browsers || ['chromium'])[0]] || chromium;
    const browser = await engine.launch({ headless: plan.headless !== false });
    const context = await browser.newContext({
      viewport: plan.viewport || { width: 1440, height: 900 },
      ignoreHTTPSErrors: plan.ignoreHTTPSErrors ?? true,
    });
    const page = await context.newPage();
    page.setDefaultTimeout(plan.defaultTimeoutMs || 15000);
    try {
      await explore({ page, session: plan.explore, artifacts: artifactsDir });
    } catch (err) {
      emit('run_error', { message: `exploration failed: ${String(err.stack || err).slice(0, 2000)}` });
    } finally {
      await context.close().catch(() => {});
      await browser.close().catch(() => {});
    }
    emit('run_end', { runId: plan.runId, totals: { total: 0 }, durationMs: Date.now() - started, cancelled });
    return;
  }

  const totals = { passed: 0, failed: 0, skipped: 0, flaky: 0, blocked: 0, needs_review: 0 };

  for (const browserName of plan.browsers || ['chromium']) {
    const engine = engines[browserName];
    if (!engine) { log('error', `unknown browser: ${browserName}`); continue; }

    let browser;
    try {
      browser = await engine.launch({
        headless: plan.headless !== false,
        slowMo: plan.slowMo || 0,
        args: browserName === 'chromium' ? ['--disable-dev-shm-usage'] : undefined,
      });
    } catch (err) {
      emit('run_error', {
        message: `could not launch ${browserName}: ${err.message}. ` +
                 `Run 'npx playwright install ${browserName}' first.`,
      });
      continue;
    }

    // Sequential within a browser when parallelism is 1; otherwise a bounded pool.
    const limit = Math.max(1, Math.min(plan.parallelism || 1, 8));
    const queue = [...plan.tests];
    const workers = Array.from({ length: limit }, () => worker());

    async function worker() {
      while (queue.length && !cancelled) {
        const testCase = queue.shift();
        const outcome = await runOne(browser, browserName, testCase, plan, artifactsDir);
        totals[outcome] = (totals[outcome] || 0) + 1;
        emit('run_progress', {
          runId: plan.runId,
          completed: Object.values(totals).reduce((a, b) => a + b, 0),
          total: plan.tests.length * (plan.browsers || ['chromium']).length,
          totals,
        });
      }
    }

    await Promise.all(workers);
    await browser.close().catch(() => {});
  }

  emit('run_end', {
    runId: plan.runId,
    totals,
    durationMs: Date.now() - started,
    cancelled,
  });
}

async function runOne(browser, browserName, testCase, plan, artifactsDir) {
  const startedAt = Date.now();
  emit('test_start', {
    testId: testCase.id, testCaseId: testCase.testCaseId, key: testCase.key,
    title: testCase.title, browser: browserName, attempt: testCase.attempt || 1,
  });

  const context = await browser.newContext({
    baseURL: plan.baseUrl || undefined,
    viewport: plan.viewport || { width: 1440, height: 900 },
    ignoreHTTPSErrors: plan.ignoreHTTPSErrors ?? true,
    recordVideo: plan.video ? { dir: artifactsDir } : undefined,
    storageState: testCase.storageState || plan.storageState || undefined,
    locale: plan.locale || 'en-US',
    timezoneId: plan.timezone || undefined,
    colorScheme: plan.colorScheme || undefined,
  });

  // A trace is what makes a failure re-investigable hours later without
  // reproducing it, so it is on by default for anything that can fail.
  const tracePath = path.join(artifactsDir, `${testCase.id}-${browserName}-trace.zip`);
  if (plan.trace !== false) {
    await context.tracing.start({ screenshots: true, snapshots: true, sources: false });
  }

  const page = await context.newPage();
  page.setDefaultTimeout(plan.defaultTimeoutMs || 30000);

  const executor = new Executor({
    page, context, run: plan, artifacts: artifactsDir,
    testCase: { ...testCase, id: testCase.id },
  });

  let status = 'passed';
  let errorMessage = '';
  let errorType = '';
  let records = [];

  try {
    const result = await executor.runSteps(testCase.steps || []);
    records = result.records;
    if (result.failed) {
      status = 'failed';
      errorMessage = result.failed.errorMessage || 'step failed';
      errorType = result.failed.errorType || 'StepFailure';
    } else if (records.some((r) => r.status === 'needs_review' || r.status === 'unverified')) {
      status = 'needs_review';
    }
  } catch (err) {
    status = 'error';
    errorMessage = String(err.message || err).slice(0, 2000);
    errorType = err.name || 'Error';
  }

  const artifacts = [];
  if (plan.trace !== false) {
    try {
      await context.tracing.stop({ path: tracePath });
      artifacts.push({ kind: 'trace', path: tracePath, label: 'Playwright trace' });
    } catch { /* tracing may not have started */ }
  }
  try {
    const video = page.video && (await page.video()?.path());
    if (video) artifacts.push({ kind: 'video', path: video, label: 'recording' });
  } catch { /* video disabled */ }

  await context.close().catch(() => {});

  emit('test_end', {
    testId: testCase.id,
    testCaseId: testCase.testCaseId,
    key: testCase.key,
    browser: browserName,
    status,
    attempt: testCase.attempt || 1,
    durationMs: Date.now() - startedAt,
    errorMessage,
    errorType,
    steps: records,
    consoleErrors: executor.consoleErrors.slice(-30),
    networkFailures: executor.networkFailures.slice(-30),
    heals: executor.healsApplied,
    artifacts,
  });

  return status === 'passed' ? 'passed'
    : status === 'needs_review' ? 'needs_review'
    : status === 'error' ? 'failed' : status;
}

// --------------------------------------------------------------------- //
function parseArgs(argv) {
  const out = {};
  for (let i = 0; i < argv.length; i++) {
    const token = argv[i];
    if (!token.startsWith('--')) continue;
    const key = token.slice(2);
    const next = argv[i + 1];
    if (!next || next.startsWith('--')) out[key] = true;
    else { out[key] = next; i++; }
  }
  return out;
}

function readStdin() {
  return new Promise((resolve) => {
    let data = '';
    process.stdin.setEncoding('utf8');
    process.stdin.on('data', (chunk) => { data += chunk; });
    process.stdin.on('end', () => resolve(data));
  });
}

async function selfTest() {
  // Validates the protocol and step vocabulary without needing a browser, so CI
  // can check the runner contract even where Playwright browsers are absent.
  const { Executor } = await import('./executor.mjs');
  const actions = [
    'goto', 'click', 'fill', 'expect_text', 'expect_visible', 'expect_url',
    'expect_semantic', 'snapshot', 'api_request', 'assert_a11y', 'assert_perf',
    'route_fault', 'network_condition', 'handoff', 'note',
  ];
  const modes = ['test', 'explore', 'record'];

  // The capture script is a string until the moment it is injected into a page,
  // so a syntax error inside it passes `node --check`, passes the build, and
  // only surfaces as an empty recording. Parsing it here turns that into a CI
  // failure instead of a mystery.
  let capture = 'ok';
  try {
    const { CAPTURE_SOURCE } = await import('./capture.mjs');
    new Function(CAPTURE_SOURCE);
  } catch (err) {
    capture = `unparseable: ${String(err.message || err)}`;
  }

  const schemaOk = await import('./schema.mjs')
    .then(({ validate }) => validate({ a: 1 }, { type: 'object', required: ['b'] }).length === 1)
    .catch(() => false);

  emit('self_test', { ok: capture === 'ok' && schemaOk, executor: typeof Executor === 'function', actions, capture, schema: schemaOk });
  if (capture !== 'ok' || !schemaOk) process.exitCode = 1;
  let playwright = 'missing';
  try { await import('playwright'); playwright = 'present'; } catch { /* optional at self-test time */ }
  emit('self_test_done', { playwright, node: process.version, modes });
}
