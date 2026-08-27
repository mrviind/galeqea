/**
 * Session recording: a person drives the browser, QE Agent writes the test.
 *
 * The recorder produces *typed steps with locator ladders*, not code. That is
 * the whole reason it exists in this codebase rather than being delegated to
 * `playwright codegen`: a generated code file is frozen the moment it is written,
 * whereas a recorded step list flows into the App Model, so the elements it
 * touched become healable, and every export target renders from the same data.
 *
 * The browser is headed by definition - there is a human in it - and the session
 * ends when the person closes the window, when the step budget is spent, or when
 * the supervisor says stop.
 */

import { emit, log } from './protocol.mjs';
import { CAPTURE_SOURCE } from './capture.mjs';
import { describeScreen } from './locator.mjs';

export async function record({ context, page, session, artifacts }) {
  const maxActions = session.maxActions ?? 300;
  const maxMs = (session.maxMinutes ?? 30) * 60_000;
  const started = Date.now();

  let actions = 0;
  let closed = false;
  let lastUrl = '';

  const finish = (reason) => {
    if (closed) return;
    closed = true;
    emit('record_end', { reason, actions, durationMs: Date.now() - started });
  };

  // exposeBinding rather than exposeFunction: the binding is re-installed on
  // every new document automatically, which is what keeps recording alive across
  // full-page navigations. A plain function would survive only the first page.
  await context.exposeBinding('__galeqeaRecord', (source, payload) => {
    if (closed || actions >= maxActions) return;
    actions += 1;
    emit('recorded_action', {
      sessionId: session.id,
      index: actions,
      frameUrl: source.frame.url(),
      isMainFrame: source.frame === source.page.mainFrame(),
      ...payload,
    });
    if (actions >= maxActions) {
      log(`recording budget of ${maxActions} actions reached`);
      finish('budget');
    }
  });

  await context.addInitScript(CAPTURE_SOURCE);

  // Every page the session opens is watched, including ones the application
  // opens itself - a checkout that pops a payment window would otherwise stop
  // being recorded at exactly the interesting moment.
  const watch = (target) => {
    target.on('framenavigated', async (frame) => {
      if (frame !== target.mainFrame() || closed) return;
      const url = frame.url();
      if (!url || url === 'about:blank' || url === lastUrl) return;
      lastUrl = url;
      emit('recorded_action', {
        sessionId: session.id, index: ++actions, kind: 'navigate', url,
        title: await target.title().catch(() => ''),
      });
      await observe(target, session);
    });
    target.on('close', () => {
      if (target === page) finish('closed');
    });
  };

  watch(page);
  context.on('page', (opened) => {
    emit('recorded_action', { sessionId: session.id, index: ++actions, kind: 'new_page', url: opened.url() });
    watch(opened);
  });

  emit('record_start', {
    sessionId: session.id, startUrl: session.startUrl, maxActions,
    maxMinutes: session.maxMinutes ?? 30, artifacts,
  });

  if (session.startUrl) {
    // The navigation is *not* emitted here. `framenavigated` fires during the
    // goto and reports it, so emitting again would record the opening step
    // twice - and the second copy would carry the post-redirect URL, making the
    // duplicate look like two different navigations rather than one.
    await page.goto(session.startUrl, { waitUntil: 'domcontentloaded' }).catch((err) => {
      emit('record_error', { message: `could not open ${session.startUrl}: ${String(err.message || err)}` });
    });
  }

  // Poll rather than race a pile of promises: the exit conditions are a closed
  // page, a spent budget and a wall-clock limit, and a 500ms poll reports all
  // three promptly without leaving a dangling listener behind on any of them.
  while (!closed) {
    if (Date.now() - started > maxMs) { finish('timeout'); break; }
    if (page.isClosed()) { finish('closed'); break; }
    await new Promise((r) => setTimeout(r, 500));
  }

  return { actions, durationMs: Date.now() - started };
}

async function observe(page, session) {
  if (session.discover === false) return;
  try {
    const described = await describeScreen(page);
    emit('screen_observed', { ...described, action: 'record' });
  } catch { /* discovery must never interrupt a person's session */ }
}
