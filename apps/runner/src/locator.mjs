/**
 * Locator resolution and healing candidate harvesting.
 *
 * The ladder runs fastest-and-most-deterministic first. Only when *every* rung
 * fails does the runner harvest candidates from the live page and hand them to
 * the supervisor to score. That ordering is the whole point: a healthy test pays
 * zero healing cost, and a broken one gets the full analysis exactly once.
 */

const INTERACTIVE = [
  'a[href]', 'button', 'input:not([type=hidden])', 'select', 'textarea',
  '[role=button]', '[role=link]', '[role=tab]', '[role=menuitem]', '[role=checkbox]',
  '[role=radio]', '[role=switch]', '[role=combobox]', '[role=textbox]', '[role=option]',
  '[contenteditable=true]', '[onclick]', '[data-testid]', '[data-test]', '[data-cy]',
].join(',');

/** Build a Playwright locator from one ladder rung. */
export function buildLocator(scope, rung) {
  const { kind, value, role, name, exact = false, nth } = rung;
  let loc;
  switch (kind) {
    case 'role':
      loc = scope.getByRole(role, name ? { name, exact } : {});
      break;
    case 'testid':
      loc = scope.getByTestId(value);
      break;
    case 'label':
      loc = scope.getByLabel(value, { exact });
      break;
    case 'placeholder':
      loc = scope.getByPlaceholder(value, { exact });
      break;
    case 'text':
      loc = scope.getByText(value, { exact });
      break;
    case 'alt':
      loc = scope.getByAltText(value, { exact });
      break;
    case 'title':
      loc = scope.getByTitle(value, { exact });
      break;
    case 'css':
      loc = scope.locator(value);
      break;
    case 'xpath':
      loc = scope.locator(`xpath=${value}`);
      break;
    default:
      throw new Error(`unsupported locator kind: ${kind}`);
  }
  return typeof nth === 'number' ? loc.nth(nth) : loc;
}

export function describeRung(rung) {
  if (rung.kind === 'role') {
    return `getByRole('${rung.role}'${rung.name ? `, { name: '${rung.name}' }` : ''})`;
  }
  if (rung.kind === 'css' || rung.kind === 'xpath') return `${rung.kind}=${rung.value}`;
  return `getBy${rung.kind[0].toUpperCase()}${rung.kind.slice(1)}('${rung.value ?? ''}')`;
}

/**
 * Try each rung in order. Returns the first that resolves to exactly one
 * attached element, plus which rung index won (index > 0 means the primary
 * locator has drifted and the App Model should be told, even though the step
 * itself succeeded).
 */
export async function resolve(scope, ladder, { timeout = 5000, requireVisible = true } = {}) {
  const attempts = [];
  for (let i = 0; i < ladder.length; i++) {
    const rung = ladder[i];
    try {
      const locator = buildLocator(scope, rung);
      // A short per-rung budget: falling through six rungs must not cost 6x the
      // step timeout. The first rung gets the full budget, the rest get a slice.
      const budget = i === 0 ? timeout : Math.min(1500, timeout);
      await locator.first().waitFor({
        state: requireVisible ? 'visible' : 'attached',
        timeout: budget,
      });
      const count = await locator.count();
      if (count === 0) {
        attempts.push({ rung: describeRung(rung), outcome: 'no_match' });
        continue;
      }
      if (count > 1 && typeof rung.nth !== 'number') {
        // Ambiguity is a defect in the locator, not a reason to guess - but the
        // first match is still usable when the rung is an explicit fallback.
        attempts.push({ rung: describeRung(rung), outcome: 'ambiguous', count });
        if (i === 0) continue;
      }
      return {
        ok: true,
        locator: locator.first(),
        rungIndex: i,
        rung,
        description: describeRung(rung),
        attempts,
        drifted: i > 0,
      };
    } catch (err) {
      attempts.push({ rung: describeRung(rung), outcome: 'error', detail: String(err.message || err).slice(0, 200) });
    }
  }
  return { ok: false, attempts };
}

/**
 * Harvest scored candidates from the live page for a failed locator.
 *
 * Runs entirely in the page context so it is one round trip regardless of how
 * many elements are on screen. Scoring is deliberately explainable - every
 * component is returned so a reviewer can see *why* a candidate was suggested,
 * rather than being handed an opaque number.
 */
export async function harvestCandidates(page, target, limit = 12) {
  return page.evaluate(
    ({ target, limit, selector }) => {
      const norm = (s) => (s || '').trim().toLowerCase().replace(/\s+/g, ' ');

      function accessibleName(el) {
        const aria = el.getAttribute('aria-label');
        if (aria) return aria;
        const labelledBy = el.getAttribute('aria-labelledby');
        if (labelledBy) {
          const parts = labelledBy.split(/\s+/)
            .map((id) => document.getElementById(id)?.textContent || '')
            .join(' ');
          if (parts.trim()) return parts;
        }
        if (el.labels && el.labels.length) return el.labels[0].textContent || '';
        if (el.getAttribute('placeholder')) return el.getAttribute('placeholder');
        if (el.getAttribute('title')) return el.getAttribute('title');
        if (el.getAttribute('alt')) return el.getAttribute('alt');
        if (el.tagName === 'INPUT' && el.value && el.type === 'submit') return el.value;
        return (el.innerText || el.textContent || '').slice(0, 120);
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
          if (t === 'submit' || t === 'button' || t === 'reset') return 'button';
          if (t === 'range') return 'slider';
          return 'textbox';
        }
        return 'generic';
      }

      function testId(el) {
        for (const attr of ['data-testid', 'data-test', 'data-cy', 'data-qa', 'data-test-id']) {
          const v = el.getAttribute(attr);
          if (v) return v;
        }
        return '';
      }

      function ancestry(el) {
        const path = [];
        let node = el.parentElement;
        for (let i = 0; node && i < 4; i++, node = node.parentElement) {
          const id = node.id ? `#${node.id}` : '';
          const role = node.getAttribute?.('role');
          path.push(`${node.tagName.toLowerCase()}${id}${role ? `[${role}]` : ''}`);
        }
        return path;
      }

      // --- similarity ------------------------------------------------------
      function tokenOverlap(a, b) {
        const A = new Set(norm(a).split(' ').filter(Boolean));
        const B = new Set(norm(b).split(' ').filter(Boolean));
        if (!A.size || !B.size) return 0;
        let hits = 0;
        for (const t of A) if (B.has(t)) hits++;
        return hits / Math.max(A.size, B.size);
      }

      function score(cand) {
        // Weights encode a claim about which signals survive a redesign:
        // what an element *is* and what it is *called* outlive its markup.
        //
        // Crucially, a signal that is not *available* (the stored fingerprint
        // never captured a bounding box, the element has no text) is excluded
        // from the average rather than scored as zero. Counting missing
        // evidence as evidence against makes a sparse fingerprint unhealable
        // no matter how perfect the match is.
        const weights = {
          role: 0.22, name: 0.30, testid: 0.20, tag: 0.05,
          text: 0.10, id: 0.05, position: 0.05, ancestry: 0.03,
        };
        const parts = {};
        const applicable = {};

        applicable.role = !!(target.role && cand.role);
        parts.role = cand.role === target.role ? 1 : 0;

        applicable.name = !!(target.accessible_name && cand.name);
        parts.name = tokenOverlap(cand.name, target.accessible_name || '');

        // A test id only counts when both sides have one. If they differ, that
        // is real evidence against; if the target never had one, it is silence.
        applicable.testid = !!(target.test_id && cand.testId);
        parts.testid = cand.testId === target.test_id ? 1 : 0;

        applicable.tag = !!(target.tag && cand.tag);
        parts.tag = cand.tag === target.tag ? 1 : 0;

        const targetText = target.text || target.accessible_name || '';
        applicable.text = !!(targetText && cand.text);
        parts.text = tokenOverlap(cand.text, targetText);

        applicable.id = !!(target.dom_id && cand.id);
        parts.id = cand.id === target.dom_id ? 1 : 0;

        applicable.position = !!(target.bounding_box && cand.box);
        if (applicable.position) {
          const dx = Math.abs(cand.box.x - target.bounding_box.x);
          const dy = Math.abs(cand.box.y - target.bounding_box.y);
          parts.position = Math.max(0, 1 - Math.hypot(dx, dy) / 600);
        } else parts.position = 0;

        applicable.ancestry = Array.isArray(target.ancestry) && target.ancestry.length > 0;
        if (applicable.ancestry) {
          const overlap = cand.ancestry.filter((a) => target.ancestry.includes(a)).length;
          parts.ancestry = overlap / Math.max(cand.ancestry.length, target.ancestry.length);
        } else parts.ancestry = 0;

        let weighted = 0;
        let available = 0;
        for (const [key, weight] of Object.entries(weights)) {
          if (!applicable[key]) continue;
          weighted += weight * parts[key];
          available += weight;
        }

        // With nothing comparable at all, refuse rather than invent a number.
        const total = available > 0 ? weighted / available : 0;
        return { total, parts, applicable, coverage: available };
      }

      const out = [];
      for (const el of document.querySelectorAll(selector)) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) continue;
        const style = getComputedStyle(el);
        if (style.visibility === 'hidden' || style.display === 'none') continue;

        const cand = {
          tag: el.tagName.toLowerCase(),
          role: impliedRole(el),
          name: accessibleName(el),
          text: (el.innerText || '').slice(0, 120),
          testId: testId(el),
          id: el.id || '',
          classes: (el.className && typeof el.className === 'string'
            ? el.className.split(/\s+/).filter(Boolean).slice(0, 6) : []),
          type: el.getAttribute('type') || '',
          href: el.getAttribute('href') || '',
          disabled: !!el.disabled,
          box: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
          ancestry: ancestry(el),
        };
        const s = score(cand);
        cand.score = Number(s.total.toFixed(4));
        cand.scoreParts = s.parts;
        cand.scoreApplicable = s.applicable;
        // How much of the fingerprint was actually comparable. A perfect score
        // from one weak signal is not the same claim as a perfect score from
        // five, and the server uses this to decide how far to trust it.
        cand.evidenceCoverage = Number(s.coverage.toFixed(3));

        // A suggested locator for each candidate, preferring the most durable form.
        if (cand.testId) cand.suggested = { kind: 'testid', value: cand.testId };
        else if (cand.name && cand.role !== 'generic') cand.suggested = { kind: 'role', role: cand.role, name: cand.name.trim().slice(0, 80) };
        else if (cand.id) cand.suggested = { kind: 'css', value: `#${CSS.escape(cand.id)}` };
        else if (cand.text) cand.suggested = { kind: 'text', value: cand.text.trim().slice(0, 60) };
        else cand.suggested = { kind: 'css', value: cand.tag + (cand.classes[0] ? `.${CSS.escape(cand.classes[0])}` : '') };

        out.push(cand);
      }

      out.sort((a, b) => b.score - a.score);
      return out.slice(0, limit);
    },
    { target, limit, selector: INTERACTIVE },
  );
}

/** Compact fingerprint of a resolved element, stored back into the App Model. */
export async function fingerprint(locator) {
  return locator.evaluate((el) => {
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
      text: (el.innerText || '').trim().slice(0, 120),
      classes: (typeof el.className === 'string' ? el.className.split(/\s+/).filter(Boolean).slice(0, 8) : []),
      box: { x: Math.round(rect.x), y: Math.round(rect.y), w: Math.round(rect.width), h: Math.round(rect.height) },
    };
  });
}

/**
 * A compact description of the screen the page is currently on.
 *
 * Roles only, not the whole tree: the point is a structural fingerprint the
 * server can hash, and shipping the full accessibility snapshot on every
 * navigation would cost far more than it tells us.
 */
export async function describeScreen(page) {
  return page.evaluate(() => {
    function impliedRole(el) {
      const explicit = el.getAttribute('role');
      if (explicit) return explicit;
      const tag = el.tagName.toLowerCase();
      if (tag === 'a') return el.hasAttribute('href') ? 'link' : 'generic';
      if (tag === 'button') return 'button';
      if (tag === 'select') return 'combobox';
      if (tag === 'textarea') return 'textbox';
      if (/^h[1-6]$/.test(tag)) return 'heading';
      if (tag === 'form') return 'form';
      if (tag === 'table') return 'table';
      if (tag === 'nav') return 'navigation';
      if (tag === 'main') return 'main';
      if (tag === 'input') {
        const t = (el.getAttribute('type') || 'text').toLowerCase();
        if (t === 'checkbox') return 'checkbox';
        if (t === 'radio') return 'radio';
        if (['submit', 'button', 'reset'].includes(t)) return 'button';
        return 'textbox';
      }
      return '';
    }

    const roles = [];
    const selector = 'a[href],button,input:not([type=hidden]),select,textarea,form,table,nav,main,h1,h2,h3,h4,h5,h6,[role]';
    for (const el of document.querySelectorAll(selector)) {
      const rect = el.getBoundingClientRect();
      if (rect.width === 0 && rect.height === 0) continue;
      const role = impliedRole(el);
      if (role && role !== 'generic') roles.push(role);
      if (roles.length > 400) break;
    }
    return { url: location.href, title: document.title || '', roles };
  });
}

/** Role and accessible name of a resolved element, for the App Model. */
export async function describeElement(locator) {
  return locator.evaluate((el) => {
    function accessibleName(node) {
      const aria = node.getAttribute('aria-label');
      if (aria) return aria;
      const labelledBy = node.getAttribute('aria-labelledby');
      if (labelledBy) {
        const parts = labelledBy.split(/\s+/)
          .map((id) => document.getElementById(id)?.textContent || '').join(' ');
        if (parts.trim()) return parts.trim();
      }
      if (node.labels && node.labels.length) return (node.labels[0].textContent || '').trim();
      return (node.getAttribute('placeholder') || node.getAttribute('title')
        || node.getAttribute('alt') || node.innerText || '').trim().slice(0, 200);
    }
    function impliedRole(node) {
      const explicit = node.getAttribute('role');
      if (explicit) return explicit;
      const tag = node.tagName.toLowerCase();
      if (tag === 'a') return node.hasAttribute('href') ? 'link' : 'generic';
      if (tag === 'button') return 'button';
      if (tag === 'select') return 'combobox';
      if (tag === 'textarea') return 'textbox';
      if (/^h[1-6]$/.test(tag)) return 'heading';
      if (tag === 'input') {
        const t = (node.getAttribute('type') || 'text').toLowerCase();
        if (t === 'checkbox') return 'checkbox';
        if (t === 'radio') return 'radio';
        if (['submit', 'button', 'reset'].includes(t)) return 'button';
        return 'textbox';
      }
      return 'generic';
    }
    return {
      role: impliedRole(el),
      accessibleName: accessibleName(el),
      tag: el.tagName.toLowerCase(),
    };
  });
}
