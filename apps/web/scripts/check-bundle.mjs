#!/usr/bin/env node
/**
 * Bundle budget guard: the initial-load JavaScript must stay under a gzip ceiling,
 * so the app keeps opening fast as it grows. Run after `npm run build`.
 *
 *     node scripts/check-bundle.mjs           # default 200 KB gzip
 *     BUDGET_KB=200 node scripts/check-bundle.mjs
 */
import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { gzipSync } from 'node:zlib';
import { resolve, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const BUDGET_KB = Number(process.env.BUDGET_KB || 200);
const distRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..', 'dist');
const dist = resolve(distRoot, 'assets');

if (!existsSync(dist)) {
  console.error('No dist/assets. Run `npm run build` first.');
  process.exit(2);
}

// Initial-load JS = only what index.html pulls up front: the entry <script> plus any
// <link rel="modulepreload"> chunks. Lazily imported route chunks (React.lazy) are
// fetched on navigation and do NOT count against the initial budget.
const html = readFileSync(resolve(distRoot, 'index.html'), 'utf8');
const initial = new Set();
for (const m of html.matchAll(/(?:src|href)="\/assets\/([^"]+\.js)"/g)) initial.add(m[1]);

const jsFiles = readdirSync(dist).filter((f) => f.endsWith('.js') && initial.has(f));
if (jsFiles.length === 0) { console.error('Could not find the entry chunk in index.html'); process.exit(2); }
let totalGzip = 0;
const rows = [];
for (const f of jsFiles) {
  const raw = readFileSync(resolve(dist, f));
  const gz = gzipSync(raw).length;
  totalGzip += gz;
  rows.push([f, raw.length, gz]);
}

rows.sort((a, b) => b[2] - a[2]);
const kb = (n) => (n / 1024).toFixed(1) + ' KB';
console.log('JS chunks (raw → gzip):');
for (const [f, raw, gz] of rows) console.log(`  ${f.padEnd(32)} ${kb(raw).padStart(10)} → ${kb(gz).padStart(9)}`);

const totalKb = totalGzip / 1024;
console.log(`\nInitial JS (gzip): ${totalKb.toFixed(1)} KB  ·  budget ${BUDGET_KB} KB`);
if (totalKb > BUDGET_KB) {
  console.error(`✗ Over budget by ${(totalKb - BUDGET_KB).toFixed(1)} KB. Split a route or trim a dependency.`);
  process.exit(1);
}
console.log(`✓ Under budget (${(BUDGET_KB - totalKb).toFixed(1)} KB headroom).`);
