/**
 * Token-efficient page state (WO#8-A).
 *
 * The accessibility tree is far cheaper than raw HTML, but a full `ariaSnapshot()`
 * is still 8–12K characters of mostly-decorative structure. The model doesn't need
 * the whole tree; it needs the things a user can act on, plus enough landmark
 * structure to orient. This trimmer keeps exactly that: interactive roles + headings
 * and landmarks, repeated siblings collapsed, text capped, and a stable `ref=eN`
 * handle on every actionable node so the model returns a ref rather than inventing a
 * selector. The result is budgeted to ~500 tokens.
 *
 * Deterministic and pure, so it unit-tests against a fixture and a re-run of the same
 * page produces byte-identical state (which is what makes the step cache and the
 * prompt cache stable).
 */

const INTERACTIVE = new Set([
  'button', 'link', 'textbox', 'searchbox', 'checkbox', 'radio', 'combobox',
  'menuitem', 'menuitemcheckbox', 'menuitemradio', 'tab', 'option', 'switch',
  'slider', 'spinbutton',
]);
const STRUCTURAL = new Set([
  'heading', 'banner', 'navigation', 'main', 'complementary', 'contentinfo',
  'region', 'form', 'search', 'dialog', 'alertdialog', 'alert', 'tablist',
]);

const TEXT_CAP = 80;
const DEFAULT_BUDGET_TOKENS = 500;

/** A cheap, deterministic token estimate (~4 chars/token). Good enough to budget. */
export function estimateTokens(text) {
  return Math.ceil((text || '').length / 4);
}

const LINE = /^(\s*)-\s+([a-zA-Z]+)(?:\s+"((?:[^"\\]|\\.)*)")?\s*(\[[^\]]*\])?\s*:?\s*$/;

/**
 * @param {string} raw  output of Playwright `ariaSnapshot()` (or `{mode:'ai'}`)
 * @param {{budgetTokens?:number}} [opts]
 * @returns {{ text:string, refs:Record<string,{role:string,name:string}>, stateTokens:number, truncated:boolean }}
 */
export function trimAriaSnapshot(raw, opts = {}) {
  const budget = opts.budgetTokens ?? DEFAULT_BUDGET_TOKENS;
  const lines = String(raw || '').split('\n');
  const refs = {};
  let refCount = 0;
  const out = [];

  // Sibling-collapse state, keyed by "indent|role".
  let run = null; // { key, indent, role, shown, extra }

  const flushRun = () => {
    if (run && run.extra > 0) {
      out.push(`${' '.repeat(run.indent)}- … ×${run.extra} more ${run.role}`);
    }
    run = null;
  };

  for (const rawLine of lines) {
    const m = rawLine.match(LINE);
    if (!m) continue;
    const [, indentStr, role, nameRaw, attrs] = m;
    const indent = indentStr.replace(/\t/g, '  ').length;
    const lower = role.toLowerCase();
    const isInteractive = INTERACTIVE.has(lower);
    const isStructural = STRUCTURAL.has(lower);
    if (!isInteractive && !isStructural) continue;

    const name = (nameRaw || '').replace(/\\"/g, '"').slice(0, TEXT_CAP);

    // Collapse consecutive interactive siblings of the same role at the same depth.
    if (isInteractive) {
      const key = `${indent}|${lower}`;
      if (run && run.key === key) {
        if (run.shown >= 3) { run.extra += 1; continue; }
        run.shown += 1;
      } else {
        flushRun();
        run = { key, indent, role: `${lower}s`, shown: 1, extra: 0 };
      }
    } else {
      flushRun();
    }

    const pad = ' '.repeat(indent);
    if (isInteractive) {
      const ref = `e${++refCount}`;
      refs[ref] = { role: lower, name };
      const extra = (attrs && /disabled|checked|expanded|selected/.test(attrs)) ? ` ${attrs}` : '';
      out.push(`${pad}- ${lower} "${name}" [ref=${ref}]${extra}`);
    } else {
      const level = attrs && /level=(\d)/.exec(attrs);
      const suffix = lower === 'heading' && level ? ` [h${level[1]}]` : '';
      out.push(name ? `${pad}- ${lower} "${name}"${suffix}` : `${pad}- ${lower}`);
    }
  }
  flushRun();

  let text = out.join('\n');
  let truncated = false;
  const maxChars = budget * 4;
  if (text.length > maxChars) {
    text = text.slice(0, maxChars).replace(/\n[^\n]*$/, '') + '\n- … (state truncated to budget)';
    truncated = true;
  }
  return { text, refs, stateTokens: estimateTokens(text), truncated };
}

/**
 * Produce a page-state block for a prompt from a Playwright page. Prefers the
 * AI-optimised snapshot when the engine supports it. Falls back to plain aria.
 */
export async function pageState(page, opts = {}) {
  let raw = '';
  try {
    raw = await page.locator('body').ariaSnapshot({ mode: 'ai' });
  } catch {
    try { raw = await page.locator('body').ariaSnapshot(); }
    catch { raw = ''; }
  }
  return trimAriaSnapshot(raw, opts);
}
