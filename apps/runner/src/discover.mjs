/**
 * Browser-driven site discovery.
 *
 * The HTTP-only crawler reads the raw HTML the server sends, which is empty of
 * everything a single-page app renders with JavaScript, so an SPA looks like a
 * one-page site. This loads each page in a real browser, lets it render, and
 * then reads the links, so client-rendered navigation is visible. It also seeds
 * from sitemap.xml / robots.txt, follows same-origin links breadth-first, and
 * (crucially for honest reporting) classifies what it finds rather than
 * crashing on it: a 4xx/5xx page, a redirect off-site, or a login wall become
 * *findings*, not exceptions.
 *
 * Deterministic and model-free: same site in, same pages out. The server still
 * owns every policy decision; this only observes.
 *
 * Output (single JSON object on stdout):
 *   { ok, method:'browser', origin, start, pages:[{url,path,status,authGated,
 *     crossOrigin,error,depth}], findings:[{kind,url,detail}], truncated,
 *     discovered }
 */

const SKIP_EXT =
  /\.(png|jpe?g|gif|svg|webp|ico|css|js|mjs|json|xml|txt|pdf|zip|gz|tgz|rar|7z|mp4|webm|mov|mp3|wav|woff2?|ttf|otf|eot)$/i;

// Never click these while probing for pushState routes: a discovery crawl must
// not log the user out, delete anything, or start a purchase.
const DESTRUCTIVE =
  /\b(delete|remove|logout|log ?out|sign ?out|deactivate|cancel|buy|purchase|pay|checkout|unsubscribe|reset|clear)\b/i;

/** Strip fragment, normalize the trailing slash. (Query-param scrubbing is done
 *  server-side so it stays unit-tested in one place.) */
function canonical(u) {
  try {
    const url = new URL(u);
    url.hash = '';
    if (url.pathname.length > 1) url.pathname = url.pathname.replace(/\/+$/, '') || '/';
    return url.toString();
  } catch {
    return null;
  }
}

/** apex and www are one site. */
function sameOrigin(a, b) {
  const strip = (h) => h.replace(/^www\./i, '');
  try {
    return strip(new URL(a).host) === strip(new URL(b).host);
  } catch {
    return false;
  }
}

function dedupeKey(u) {
  try {
    const url = new URL(u);
    const host = url.host.replace(/^www\./i, '');
    const path = url.pathname.length > 1 ? url.pathname.replace(/\/+$/, '') || '/' : '/';
    return `${host}${path}`;
  } catch {
    return u;
  }
}

async function fetchSeeds(context, origin) {
  const out = [];
  for (const rel of ['/sitemap.xml', '/robots.txt']) {
    try {
      const resp = await context.request.get(new URL(rel, origin).toString(), { timeout: 8000 });
      if (!resp.ok()) continue;
      const body = await resp.text();
      if (rel.endsWith('.xml')) {
        for (const m of body.matchAll(/<loc>\s*([^<\s]+)\s*<\/loc>/gi)) out.push(m[1]);
      } else {
        for (const m of body.matchAll(/Sitemap:\s*(\S+)/gi)) {
          try {
            const s = await context.request.get(m[1], { timeout: 8000 });
            if (s.ok()) for (const mm of (await s.text()).matchAll(/<loc>\s*([^<\s]+)\s*<\/loc>/gi)) out.push(mm[1]);
          } catch { /* a bad sitemap hint is not fatal */ }
        }
      }
    } catch { /* absent sitemap/robots is the common case, not an error */ }
  }
  return out;
}

/** Same-origin hrefs the rendered page exposes: real anchors plus role-based
 *  links/menuitems that carry an href. */
async function collectLinks(page, origin) {
  const hrefs = await page
    .evaluate(() => {
      const sel = 'a[href], [role=link][href], [role=menuitem][href]';
      return Array.from(document.querySelectorAll(sel)).map((el) => el.getAttribute('href'));
    })
    .catch(() => []);
  const base = page.url();
  const out = [];
  for (const h of hrefs) {
    if (!h) continue;
    let abs;
    try {
      abs = new URL(h, base).toString();
    } catch {
      continue;
    }
    const url = new URL(abs);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') continue;
    if (!sameOrigin(abs, origin)) continue;
    if (SKIP_EXT.test(url.pathname)) continue;
    out.push(abs);
  }
  return out;
}

/** Best-effort pushState discovery on the entry page: click nav-like controls
 *  that have no href and see whether they change the URL client-side. Bounded
 *  and guarded: nav/menu context only, never a destructive-looking control,
 *  and the page is restored after each probe. */
async function probePushState(page, start, origin, cap) {
  const found = new Set();
  let handles = [];
  try {
    handles = await page.$$(
      'nav a:not([href]), header a:not([href]), [role=navigation] [role=link], ' +
        '[role=navigation] [role=menuitem], [role=menu] [role=menuitem], [role=menubar] [role=menuitem]',
    );
  } catch {
    return [];
  }
  for (const el of handles.slice(0, 12)) {
    if (found.size >= cap) break;
    let label = '';
    try {
      label = ((await el.innerText().catch(() => '')) || (await el.getAttribute('aria-label').catch(() => '')) || '').trim();
    } catch { /* ignore */ }
    if (DESTRUCTIVE.test(label)) continue;
    try {
      const before = page.url();
      await el.click({ timeout: 1500, trial: false });
      await page.waitForTimeout(250);
      const after = page.url();
      if (after !== before && sameOrigin(after, origin)) found.add(canonical(after));
      if (after !== start) await page.goto(start, { waitUntil: 'domcontentloaded', timeout: 10000 });
    } catch {
      try { await page.goto(start, { waitUntil: 'domcontentloaded', timeout: 10000 }); } catch { /* ignore */ }
    }
  }
  return [...found].filter(Boolean);
}

export async function discover(startUrl, opts = {}) {
  const maxDepth = Number.isFinite(opts.maxDepth) ? opts.maxDepth : 3;
  const maxPages = Number.isFinite(opts.maxPages) ? opts.maxPages : 40;
  const perPageTimeout = Number.isFinite(opts.perPageTimeout) ? opts.perPageTimeout : 15000;

  let origin;
  try {
    origin = new URL(startUrl).origin;
  } catch {
    return { ok: false, error: `invalid url: ${startUrl}` };
  }

  const { chromium } = await import('playwright');
  const browser = await chromium.launch();
  const context = await browser.newContext({
    userAgent: 'GaleQEA/1.0 (+website-discover)',
    ignoreHTTPSErrors: true,
  });

  const pages = [];
  const findings = [];
  const visited = new Set();
  const queue = [{ url: startUrl, depth: 0 }];
  let truncated = false;
  let forms = 0; // count on the entry page, for the plan's form-submit check

  for (const s of await fetchSeeds(context, origin)) {
    if (sameOrigin(s, origin) && !SKIP_EXT.test(new URL(s).pathname)) queue.push({ url: s, depth: 1 });
  }

  while (queue.length) {
    if (pages.length >= maxPages) {
      truncated = true;
      break;
    }
    const { url, depth } = queue.shift();
    const key = dedupeKey(url);
    if (visited.has(key)) continue;
    visited.add(key);

    const page = await context.newPage();
    const rec = { url, path: safePath(url), status: null, authGated: false, crossOrigin: false, error: null, depth };
    try {
      const resp = await page.goto(url, { waitUntil: 'domcontentloaded', timeout: perPageTimeout });
      const finalUrl = page.url();
      rec.url = finalUrl;
      rec.path = safePath(finalUrl);
      rec.status = resp ? resp.status() : null;

      if (!sameOrigin(finalUrl, origin)) {
        rec.crossOrigin = true;
        findings.push({ kind: 'redirect_offsite', url, detail: `redirects off-site to ${new URL(finalUrl).origin}` });
        pages.push(rec);
        await page.close();
        continue;
      }
      if (rec.status != null && rec.status >= 400) {
        findings.push({ kind: 'http_error', url: finalUrl, detail: `HTTP ${rec.status}` });
      }
      // Get the consent banner out of the way so it neither covers the page nor
      // blocks the crawl. Best-effort and non-fatal.
      if (await dismissConsent(page) && depth === 0) {
        findings.push({ kind: 'cookie_banner', url: finalUrl, detail: 'consent banner auto-dismissed' });
      }
      const pwd = await page.locator('input[type=password]').count().catch(() => 0);
      if (pwd > 0 || rec.status === 401 || rec.status === 403) {
        rec.authGated = true;
        // Detect the kind so the Access card can offer the right prompt: a 401 with
        // WWW-Authenticate is basic/digest (httpCredentials); a password field is a
        // form login (fill + storageState).
        let authKind = 'form';
        if (rec.status === 401) {
          const www = ((resp && resp.headers()['www-authenticate']) || '').toLowerCase();
          authKind = www.startsWith('digest') ? 'digest'
            : www.startsWith('basic') ? 'basic'
              : (pwd > 0 ? 'form' : 'basic');
        }
        rec.authKind = authKind;
        findings.push({ kind: 'auth_gated', url: finalUrl, auth_kind: authKind,
          detail: `login required (${authKind}), skipped` });
      }
      pages.push(rec);

      if (depth === 0) {
        forms = await page.locator('form').count().catch(() => 0);
      }
      if (depth < maxDepth && !rec.authGated && (rec.status == null || rec.status < 400)) {
        const links = await collectLinks(page, origin);
        if (depth === 0) {
          for (const r of await probePushState(page, finalUrl, origin, maxPages)) links.push(r);
        }
        for (const l of links) {
          if (!visited.has(dedupeKey(l))) queue.push({ url: l, depth: depth + 1 });
        }
      }
    } catch (err) {
      rec.error = String(err && err.message ? err.message : err).slice(0, 200);
      findings.push({ kind: 'load_error', url, detail: rec.error });
      pages.push(rec);
    }
    await page.close();
  }

  await browser.close();
  return {
    ok: true,
    method: 'browser',
    origin,
    start: startUrl,
    pages,
    forms,
    findings,
    truncated,
    discovered: visited.size,
  };
}

function safePath(u) {
  try {
    return new URL(u).pathname;
  } catch {
    return '/';
  }
}

//: Common consent-banner controls. Reject/decline non-essential is preferred over
//: accept, so the crawl doesn't opt the user into tracking just to see a page.
const CONSENT_SELECTORS = [
  '#onetrust-reject-all-handler', '.cc-deny', '.cc-dismiss',
  '[aria-label*="reject" i]', '[aria-label*="decline" i]',
  'button:has-text("Reject all")', 'button:has-text("Decline")', 'button:has-text("Only necessary")',
  '#onetrust-accept-btn-handler', '.cc-allow',
  'button:has-text("Accept all")', 'button:has-text("Accept")',
  'button:has-text("I agree")', 'button:has-text("Got it")', 'button:has-text("OK")',
];

async function dismissConsent(page) {
  for (const sel of CONSENT_SELECTORS) {
    try {
      const el = page.locator(sel).first();
      if ((await el.count()) && (await el.isVisible())) {
        await el.click({ timeout: 800 });
        return true;
      }
    } catch {
      /* selector engine quirk or the banner vanished; try the next */
    }
  }
  return false;
}
