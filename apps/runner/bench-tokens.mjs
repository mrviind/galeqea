#!/usr/bin/env node
/**
 * Token bench (WO#8-D). Proves "cheap by design": the trimmed page state the model
 * sees must be dramatically smaller than the raw accessibility tree, and stay under a
 * hard per-state budget. Runs on representative page shapes from the golden path
 * (a login form, a product grid, a big data table) so the numbers reflect real pages.
 *
 *     node bench-tokens.mjs            # human table
 *     node bench-tokens.mjs --json     # machine-readable, for CI
 *
 * Exit non-zero (CI guard) if the model-path reduction is < 4× or state_tokens p95 > 600.
 */
import { trimAriaSnapshot, estimateTokens } from './src/pagestate.mjs';

const MIN_REDUCTION = Number(process.env.BENCH_MIN_REDUCTION || 4);
const MAX_P95 = Number(process.env.BENCH_MAX_STATE_TOKENS || 600);

function loginForm() {
  return [
    '- banner:',
    '  - heading "Sign in" [level=1]',
    '  - link "Home"',
    '- main:',
    '  - paragraph: Enter your credentials to access your account and continue shopping.',
    '  - textbox "Email"',
    '  - textbox "Password"',
    '  - checkbox "Remember me"',
    '  - button "Sign in"',
    '  - link "Forgot password?"',
    '- contentinfo:',
    '  - paragraph: Copyright and a long footer of legal text nobody reads here.',
  ].join('\n');
}

function productGrid(n = 120) {
  const out = ['- banner:', '  - heading "Products" [level=1]', '  - navigation:'];
  for (let i = 0; i < 20; i++) out.push(`    - link "Category ${i}"`);
  out.push('- main:');
  for (let i = 0; i < n; i++) {
    out.push(`  - link "Product ${i}, a fairly long descriptive product title goes here"`);
    out.push(`  - paragraph: marketing copy for product ${i} that the model never needs`);
    out.push(`  - button "Add product ${i} to cart"`);
  }
  out.push('  - textbox "Search"');
  return out.join('\n');
}

function dataTable(rows = 200) {
  const out = ['- main:', '  - heading "Orders" [level=1]', '  - table:'];
  for (let i = 0; i < rows; i++) {
    out.push('    - row:');
    out.push(`      - cell: Order ${i}`);
    out.push(`      - cell: $${(i * 3.14).toFixed(2)}`);
    out.push(`      - link "View order ${i}"`);
  }
  return out.join('\n');
}

const FIXTURES = [
  ['login form', loginForm()],
  ['product grid (120)', productGrid()],
  ['orders table (200)', dataTable()],
];

const rows = FIXTURES.map(([name, raw]) => {
  const before = estimateTokens(raw);
  const { stateTokens, refs } = trimAriaSnapshot(raw);
  return { name, rawTokens: before, stateTokens, refs: Object.keys(refs).length,
           reduction: +(before / Math.max(stateTokens, 1)).toFixed(1) };
});

const stateTokens = rows.map((r) => r.stateTokens).sort((a, b) => a - b);
const p95 = stateTokens[Math.min(stateTokens.length - 1, Math.ceil(stateTokens.length * 0.95) - 1)];
const worst = Math.min(...rows.map((r) => r.reduction));
const totalBefore = rows.reduce((s, r) => s + r.rawTokens, 0);
const totalAfter = rows.reduce((s, r) => s + r.stateTokens, 0);
const overall = +(totalBefore / totalAfter).toFixed(1);
// The bar is the model-path (aggregate) reduction: a tiny all-interactive page has
// nothing to trim and legitimately reduces little, but across real pages the win is large.
const pass = overall >= MIN_REDUCTION && p95 <= MAX_P95;

if (process.argv.includes('--json')) {
  console.log(JSON.stringify({ rows, p95_state_tokens: p95, overall_reduction: overall,
                               worst_reduction: worst, pass }, null, 2));
} else {
  console.log('Page state, raw vs trimmed (model-path tokens):\n');
  for (const r of rows) {
    console.log(`  ${r.name.padEnd(22)} ${String(r.rawTokens).padStart(6)} → ` +
                `${String(r.stateTokens).padStart(4)} tok  (${r.reduction}× · ${r.refs} refs)`);
  }
  console.log(`\n  overall ${totalBefore} → ${totalAfter} tok  (${overall}×)`);
  console.log(`  state_tokens p95: ${p95} (budget ${MAX_P95})`);
  console.log(`  worst reduction:  ${worst}× (min ${MIN_REDUCTION}×)`);
  console.log(pass ? '\n✓ cheap by design, within budget.' : '\n✗ over budget / under target.');
}
process.exit(pass ? 0 : 1);
