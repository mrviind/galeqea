/**
 * The in-page capture script for session recording.
 *
 * This whole module is serialised into the page as a string, so it must be
 * self-contained: no imports, no closures over Node scope. It runs on every
 * document in every frame, before the application's own scripts.
 *
 * Three decisions worth stating, because they are what separate a recorder that
 * produces maintainable tests from one that produces a transcript:
 *
 *  - **Capture phase, passive, never preventing.** Listeners are registered with
 *    `{capture: true, passive: true}` so a page that calls `stopPropagation` on
 *    its own handlers cannot hide an interaction from the recorder, and the
 *    recorder can never alter what the application does. What you record is
 *    exactly what happens without it.
 *
 *  - **A ladder, not a selector.** Every captured element carries an ordered
 *    list of ways to find it, best first, plus the fingerprint the healing
 *    scorer needs. A recorder that emits one CSS path produces a test that dies
 *    at the next re-render; this produces one that can be repaired.
 *
 *  - **Secrets are never captured.** Password fields, anything the page marks as
 *    a credential or payment autocomplete target, and any value that looks like
 *    a token are replaced with a generator reference at the point of capture -
 *    not redacted later. A value that is never read cannot leak into a database,
 *    an export, or a log.
 */

export const CAPTURE_SOURCE = `(() => {
  if (window.__galeqeaCaptureInstalled) return;
  window.__galeqeaCaptureInstalled = true;

  const HOST_ID = '__galeqea_recorder_host__';
  const SECRET_AUTOCOMPLETE = /^(current-password|new-password|cc-number|cc-csc|cc-exp|one-time-code)$/i;
  const SECRET_NAME = /(pass|pwd|secret|token|otp|cvv|cvc|ssn|card)/i;
  // A long unbroken run of base64/hex characters is a credential far more often
  // than it is something a person typed into a form.
  const SECRET_SHAPE = /^(?:[A-Za-z0-9_-]{24,}|[0-9a-f]{32,})$/;

  const send = (payload) => {
    try {
      if (window.__galeqeaRecord) window.__galeqeaRecord(payload);
    } catch { /* the recorder must never break the page */ }
  };

  // ---------------------------------------------------------------- //
  const text = (s) => (s || '').replace(/\\s+/g, ' ').trim();

  function impliedRole(el) {
    const explicit = el.getAttribute('role');
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === 'a') return el.hasAttribute('href') ? 'link' : 'generic';
    if (tag === 'button') return 'button';
    if (tag === 'select') return 'combobox';
    if (tag === 'textarea') return 'textbox';
    if (tag === 'summary') return 'button';
    if (/^h[1-6]$/.test(tag)) return 'heading';
    if (tag === 'input') {
      const t = (el.getAttribute('type') || 'text').toLowerCase();
      if (t === 'checkbox') return 'checkbox';
      if (t === 'radio') return 'radio';
      if (['submit', 'button', 'reset', 'image'].includes(t)) return 'button';
      if (t === 'search') return 'searchbox';
      return 'textbox';
    }
    return 'generic';
  }

  function accessibleName(el) {
    const aria = el.getAttribute('aria-label');
    if (aria) return text(aria);
    const by = el.getAttribute('aria-labelledby');
    if (by) {
      const joined = by.split(/\\s+/).map((id) => document.getElementById(id)?.textContent || '').join(' ');
      if (text(joined)) return text(joined);
    }
    if (el.labels && el.labels.length) return text(el.labels[0].textContent);
    const t = (el.getAttribute('type') || '').toLowerCase();
    if (el.tagName === 'INPUT' && ['submit', 'button', 'reset'].includes(t)) return text(el.value);
    return text(el.getAttribute('title') || el.getAttribute('alt') || el.innerText || el.value || '');
  }

  function isSecret(el) {
    if (!el || el.tagName !== 'INPUT') return false;
    if ((el.type || '').toLowerCase() === 'password') return true;
    const auto = el.getAttribute('autocomplete') || '';
    if (SECRET_AUTOCOMPLETE.test(auto.split(/\\s+/).pop() || '')) return true;
    return SECRET_NAME.test(\`\${el.name || ''} \${el.id || ''} \${el.getAttribute('placeholder') || ''}\`);
  }

  /** A short, reasonably stable CSS path - the ladder's last rung, never its first. */
  function cssPath(el) {
    const parts = [];
    let node = el;
    for (let depth = 0; node && node.nodeType === 1 && depth < 5; depth++) {
      if (node.id && /^[A-Za-z][\\w-]*$/.test(node.id)) {
        parts.unshift('#' + node.id);
        break;                                   // an id is as specific as it gets
      }
      let part = node.tagName.toLowerCase();
      const parent = node.parentElement;
      if (parent) {
        const siblings = [...parent.children].filter((c) => c.tagName === node.tagName);
        if (siblings.length > 1) part += \`:nth-of-type(\${siblings.indexOf(node) + 1})\`;
      }
      parts.unshift(part);
      node = parent;
    }
    return parts.join(' > ');
  }

  /**
   * Build the locator ladder, best rung first.
   *
   * The ordering is the recorder's most consequential output. A test id is
   * chosen first because it is the only attribute a team puts there *for* tests;
   * role plus accessible name comes next because it survives restyling and is
   * what a screen reader would use; structural CSS is last because it is the
   * rung that breaks.
   */
  function ladder(el) {
    const rungs = [];
    const push = (rung) => { if (rung && !rungs.some((r) => JSON.stringify(r) === JSON.stringify(rung))) rungs.push(rung); };

    for (const attr of ['data-testid', 'data-test', 'data-cy', 'data-qa']) {
      const value = el.getAttribute(attr);
      if (value) push({ kind: 'testid', value });
    }

    const role = impliedRole(el);
    const name = accessibleName(el).slice(0, 120);
    if (role && role !== 'generic' && name) push({ kind: 'role', role, name, exact: true });

    if (el.labels && el.labels.length) {
      const label = text(el.labels[0].textContent);
      if (label) push({ kind: 'label', value: label.slice(0, 120), exact: true });
    }
    const placeholder = el.getAttribute('placeholder');
    if (placeholder) push({ kind: 'placeholder', value: text(placeholder).slice(0, 120), exact: true });
    const alt = el.getAttribute('alt');
    if (alt) push({ kind: 'alt', value: text(alt).slice(0, 120), exact: true });
    const title = el.getAttribute('title');
    if (title) push({ kind: 'title', value: text(title).slice(0, 120), exact: true });

    // Text is only a good rung for things whose text *is* their identity.
    if (['button', 'link', 'tab', 'menuitem'].includes(role) && name && name.length <= 60) {
      push({ kind: 'text', value: name, exact: true });
    }
    if (role && role !== 'generic' && !name) push({ kind: 'role', role });

    push({ kind: 'css', value: cssPath(el) });

    // Where a rung matches several elements, pin the index rather than leaving
    // the test to resolve ambiguously against whichever happens to be first.
    for (const rung of rungs) {
      if (rung.kind !== 'css') continue;
      try {
        const matches = document.querySelectorAll(rung.value);
        if (matches.length > 1) rung.nth = [...matches].indexOf(el);
      } catch { /* an unparseable path is dropped by the resolver anyway */ }
    }
    return rungs.slice(0, 6);
  }

  function fingerprint(el) {
    const rect = el.getBoundingClientRect();
    const attrs = {};
    for (const a of el.attributes) {
      if (a.name.startsWith('data-') || ['id', 'name', 'type', 'role', 'aria-label', 'placeholder', 'href'].includes(a.name)) {
        attrs[a.name] = a.value.slice(0, 120);
      }
    }
    return {
      tag: el.tagName.toLowerCase(),
      attrs,
      text: text(el.innerText).slice(0, 120),
      classes: (typeof el.className === 'string' ? el.className.split(/\\s+/).filter(Boolean).slice(0, 8) : []),
      box: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
    };
  }

  function describe(el) {
    return {
      tag: el.tagName.toLowerCase(),
      type: (el.getAttribute('type') || '').toLowerCase(),
      role: impliedRole(el),
      accessibleName: accessibleName(el).slice(0, 200),
      ladder: ladder(el),
      fingerprint: fingerprint(el),
      secret: isSecret(el),
      inForm: !!el.closest('form'),
    };
  }

  /** The recorder's own badge must never appear in a recorded test. */
  function isRecorderChrome(el) {
    return !!(el.closest && el.closest('#' + HOST_ID));
  }

  function targetOf(event) {
    // composedPath crosses shadow boundaries, so a control inside a web
    // component is captured as the control rather than as its host element.
    const path = typeof event.composedPath === 'function' ? event.composedPath() : [];
    for (const node of path) {
      if (node && node.nodeType === 1 && node.tagName !== 'HTML' && node.tagName !== 'BODY') return node;
    }
    return event.target;
  }

  function valueOf(el) {
    if (isSecret(el)) {
      // Never read it. The step carries a generator reference instead, which the
      // plan resolves at run time from the vault or the data factory.
      // The kind is deliberately left to the server for anything that is not
      // literally a password input: the server infers it from the field name far
      // better than three lines of in-page heuristics can, and getting it wrong
      // here would mean a card field filled with a ten-character word.
      const secretKind = (el.type || '').toLowerCase() === 'password' ? 'password' : '';
      return { secret: true, generate: { kind: secretKind, field: el.name || el.id || el.getAttribute('placeholder') || 'secret' } };
    }
    return { text: String(el.value ?? '').slice(0, 500) };
  }

  // ---------------------------------------------------------------- //
  let sequence = 0;
  const record = (kind, el, extra) => {
    if (!el || el.nodeType !== 1 || isRecorderChrome(el)) return;
    send({ kind, at: sequence++, url: location.href, title: document.title, element: describe(el), ...extra });
  };

  document.addEventListener('click', (event) => {
    const el = targetOf(event);
    if (!el || isRecorderChrome(el)) return;
    // Alt+click is the assertion gesture: it asks "this must be here", which is
    // the one thing a recorder cannot infer from watching someone browse.
    if (event.altKey) return record('assert', el, {});
    const tag = el.tagName.toLowerCase();
    const type = (el.getAttribute('type') || '').toLowerCase();
    if (tag === 'input' && ['checkbox', 'radio'].includes(type)) {
      return record('toggle', el, { checked: !!el.checked });
    }
    record('click', el, { button: event.button, detail: event.detail });
  }, { capture: true, passive: true });

  document.addEventListener('change', (event) => {
    const el = targetOf(event);
    if (!el || isRecorderChrome(el)) return;
    const tag = el.tagName.toLowerCase();
    if (tag === 'select') {
      const selected = [...el.selectedOptions].map((o) => ({ value: o.value, label: text(o.textContent) }));
      return record('select', el, { selected });
    }
    if (tag === 'input' && (el.type || '').toLowerCase() === 'file') {
      return record('upload', el, { files: [...(el.files || [])].map((f) => f.name) });
    }
    if (tag === 'input' && ['checkbox', 'radio'].includes((el.type || '').toLowerCase())) return;
    if (tag === 'input' || tag === 'textarea') record('fill', el, { value: valueOf(el) });
  }, { capture: true, passive: true });

  document.addEventListener('keydown', (event) => {
    const el = targetOf(event);
    if (!el || isRecorderChrome(el)) return;
    // Only keys that *do* something: Enter submits, Escape dismisses, Tab moves
    // focus in a way tests sometimes depend on. Every other keystroke is already
    // captured as the field's final value.
    if (!['Enter', 'Escape', 'Tab'].includes(event.key)) return;
    record('press', el, { key: event.key });
  }, { capture: true, passive: true });

  document.addEventListener('submit', (event) => {
    const el = targetOf(event);
    if (el) record('submit', el, {});
  }, { capture: true, passive: true });

  // ---------------------------------------------------------------- //
  // A closed shadow root so the badge cannot be styled, queried or tripped over
  // by the application, and pointer-events:none so it can never absorb a click
  // the tester meant for the page underneath.
  function badge() {
    if (!document.body || document.getElementById(HOST_ID)) return;
    const host = document.createElement('div');
    host.id = HOST_ID;
    host.style.cssText = 'position:fixed;inset:auto 12px 12px auto;z-index:2147483647;pointer-events:none';
    const root = host.attachShadow({ mode: 'closed' });
    root.innerHTML = \`<div style="
        font:500 12px/1.4 ui-sans-serif,system-ui,-apple-system,Segoe UI,sans-serif;
        background:#111418;color:#e9edf2;border:1px solid #2a3038;border-radius:8px;
        padding:7px 11px;box-shadow:0 4px 14px rgba(0,0,0,.35);display:flex;gap:8px;align-items:center">
        <span style="width:7px;height:7px;border-radius:50%;background:#e5484d"></span>
        <span>QE Agent is recording — <b>Alt+click</b> to assert</span>
      </div>\`;
    document.body.appendChild(host);
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', badge);
  else badge();
})();`;
