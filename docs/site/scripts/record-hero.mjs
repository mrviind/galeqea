#!/usr/bin/env node
/**
 * Record the landing-page hero: a real capture of the chat journey
 * (URL → plan card → run board → report card) against a running GaleQEA.
 *
 * It never goes stale because it drives the actual product. Re-run with:
 *
 *     make hero                     # uses BASE_URL (default http://localhost:8080)
 *     BASE_URL=http://localhost:8080 node docs/site/scripts/record-hero.mjs
 *
 * Output: docs/site/hero.webm (+ hero.gif and hero-poster.png if ffmpeg is present).
 * Requires: a running GaleQEA with the demo app, and `playwright` installed
 * (the runner already depends on it: `npm --prefix apps/runner exec playwright install chromium`).
 */
import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import { existsSync, mkdirSync, renameSync, rmSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const BASE = process.env.BASE_URL || 'http://localhost:8080';
const TARGET = process.env.DEMO_URL || 'http://localhost:8765';
const outDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const vidDir = resolve(outDir, '.rec');

function ffmpeg(args) {
  try { execFileSync('ffmpeg', args, { stdio: 'ignore' }); return true; }
  catch { return false; }
}

const step = (page, ms = 900) => page.waitForTimeout(ms);

(async () => {
  mkdirSync(vidDir, { recursive: true });
  const browser = await chromium.launch();
  const context = await browser.newContext({
    viewport: { width: 1280, height: 800 }, deviceScaleFactor: 2,
    recordVideo: { dir: vidDir, size: { width: 1280, height: 800 } },
  });
  const page = await context.newPage();

  await page.goto(BASE, { waitUntil: 'networkidle' });
  await step(page, 1200);

  // Type the target URL into the chat and send it.
  const box = page.getByPlaceholder(/type|message|ask|url/i).first();
  await box.click();
  for (const ch of `test ${TARGET}`) { await box.type(ch, { delay: 45 }); }
  await step(page, 500);
  await box.press('Enter');

  // Let the journey render: exploring → plan card → run board → report card.
  // Best-effort waits keyed on visible cards; fall back to time so a missing
  // selector never aborts the capture.
  for (const rx of [/plan/i, /run/i, /report/i]) {
    await page.getByText(rx).first().waitFor({ timeout: 20000 }).catch(() => {});
    await step(page, 1600);
  }
  await step(page, 1400);

  await context.close();
  await browser.close();

  // The webm Playwright wrote has a random name; move it to hero.webm.
  const { readdirSync } = await import('node:fs');
  const rec = readdirSync(vidDir).find((f) => f.endsWith('.webm'));
  if (!rec) { console.error('No recording produced.'); process.exit(1); }
  const webm = resolve(outDir, 'hero.webm');
  renameSync(resolve(vidDir, rec), webm);
  rmSync(vidDir, { recursive: true, force: true });
  console.log('Wrote', webm);

  // Re-encode to a compact VP9 WebM + H.264 MP4, and a poster in AVIF/WebP/JPEG,
  // the exact assets index.html references. Keeps the hero light (Lighthouse ≥95).
  const raw = resolve(outDir, '.raw.webm');
  renameSync(webm, raw);
  const mp4 = resolve(outDir, 'hero.mp4');
  const jpg = resolve(outDir, 'hero-poster.jpg');
  const webp = resolve(outDir, 'hero-poster.webp');
  const avif = resolve(outDir, 'hero-poster.avif');
  if (!ffmpeg(['-y', '-i', raw, '-c:v', 'libvpx-vp9', '-b:v', '0', '-crf', '34', '-an',
               '-pix_fmt', 'yuv420p', '-vf', 'fps=12,scale=1000:-2', webm])) {
    renameSync(raw, webm);  // ffmpeg missing → keep the raw recording as-is
    console.log('ffmpeg not found. Kept hero.webm (install ffmpeg to shrink + add mp4/posters).');
    return;
  }
  ffmpeg(['-y', '-i', raw, '-c:v', 'libx264', '-crf', '26', '-an', '-pix_fmt', 'yuv420p',
          '-movflags', '+faststart', '-vf', 'fps=12,scale=1000:-2', mp4]);
  ffmpeg(['-y', '-i', raw, '-frames:v', '1', '-vf', 'scale=1000:-2', jpg]);       // poster (jpeg)
  ffmpeg(['-y', '-i', jpg, '-c:v', 'libwebp', '-quality', '82', webp]);           // poster (webp)
  ffmpeg(['-y', '-i', jpg, '-c:v', 'libaom-av1', '-still-picture', '1', '-crf', '30', avif]); // poster (avif)
  rmSync(raw, { force: true });
  console.log('Wrote hero.webm, hero.mp4, hero-poster.{jpg,webp,avif}');
})().catch((e) => { console.error(e); process.exit(1); });
