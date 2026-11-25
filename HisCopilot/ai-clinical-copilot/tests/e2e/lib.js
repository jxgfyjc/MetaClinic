// Shared helpers for the real-browser checks (headless Chrome via puppeteer-core). Config via env:
//   WEB=http://127.0.0.1:3100  CHROME=/path/to/chrome  PUPPETEER=/path/to/node_modules/puppeteer-core  SHOTS=/tmp/v3shots
const puppeteer = require(process.env.PUPPETEER || '/home/ma-user/.npm/_npx/110f701c48e68d66/node_modules/puppeteer-core');
const CHROME = process.env.CHROME || '/home/ma-user/.cache/puppeteer/chrome/linux-155.0.8059.39/chrome-linux64/chrome';
const WEB = process.env.WEB || 'http://127.0.0.1:3100';
const SHOTS = process.env.SHOTS || '/tmp/v3shots';
require('fs').mkdirSync(SHOTS, { recursive: true });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function launch() { return puppeteer.launch({ executablePath: CHROME, headless: 'new', args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage', '--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'] }); }

/** a page that records everything that would be a bug: console errors, uncaught exceptions, HTTP >= 400 (minus allow-listed) */
async function newPage(browser, { width = 1440, height = 900, mobile = false, allow = [] } = {}) {
  // each page gets its OWN cookie jar (incognito context) so role sessions never leak into each other
  const ctx = browser.createBrowserContext ? await browser.createBrowserContext() : await browser.createIncognitoBrowserContext();
  const page = await ctx.newPage(); page.ctx = ctx;
  await page.setViewport({ width, height, isMobile: mobile, hasTouch: mobile });
  page.problems = [];
  const ok = (s) => allow.some((a) => (a instanceof RegExp ? a.test(s) : s.includes(a)));
  // "Failed to load resource" console lines carry no URL; the same failures are recorded WITH the URL by the response listener below.
  page.on('console', (m) => { if (m.type() === 'error' && !/^Failed to load resource/.test(m.text())) { const t = `[console.error] ${m.text().slice(0, 240)}`; if (!ok(t)) page.problems.push(t); } });
  // an unexpected "leave site? unsaved changes" prompt means the UI believes there is unsaved work — record it as a problem, then accept so the run continues
  page.on('dialog', async (dlg) => { page.problems.push(`[dialog:${dlg.type()}] ${dlg.message().slice(0, 80)}`); await dlg.accept().catch(() => {}); });
  page.on('pageerror', (e) => page.problems.push(`[pageerror] ${String(e).slice(0, 240)}`));
  page.on('response', (r) => { if (r.status() >= 400) { const t = `[http ${r.status()}] ${r.request().method()} ${r.url().replace(WEB, '').slice(0, 120)}`; if (!ok(t)) page.problems.push(t); } });
  return page;
}
async function shot(page, name) { const f = `${SHOTS}/${name}.png`; await page.screenshot({ path: f }); return f; }

async function login(page, user, pw) {
  await page.goto(`${WEB}/login`, { waitUntil: 'networkidle2', timeout: 90000 });
  await page.waitForSelector('#u');
  await page.type('#u', user); await page.type('#p', pw);
  await Promise.all([page.waitForFunction(() => !location.pathname.startsWith('/login'), { timeout: 30000 }), page.click('form button')]);
  await sleep(800);
}
/** same-origin API call from inside the page (uses the browser's cookie jar, like the UI does) */
async function api(page, method, path, body) {
  return page.evaluate(async (method, path, body) => {
    const r = await fetch('/api/v1' + path, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined });
    let j = null; try { j = await r.json(); } catch {}
    return { status: r.status, body: j };
  }, method, path, body);
}
async function goto(page, path, settle = 1200) { await page.goto(WEB + path, { waitUntil: 'networkidle2', timeout: 90000 }); await sleep(settle); }
async function textOf(page, sel) { return page.$eval(sel, (e) => e.innerText).catch(() => null); }
async function clickText(page, text, tag = 'button') {
  const ok = await page.evaluate((text, tag) => { const el = [...document.querySelectorAll(tag)].find((e) => e.innerText.replace(/\s+/g, '').includes(text.replace(/\s+/g, '')) && !e.disabled && e.offsetParent !== null); if (el) { el.scrollIntoView({ block: 'center' }); el.click(); return true; } return false; }, text, tag);
  if (!ok) throw new Error(`clickText: no enabled <${tag}> containing "${text}"`);
}
async function waitText(page, text, timeout = 30000) { await page.waitForFunction((t) => document.body.innerText.replace(/\s+/g, '').includes(t.replace(/\s+/g, '')), { timeout }, text); }
async function bodyHas(page, text) { return page.evaluate((t) => document.body.innerText.replace(/\s+/g, '').includes(t.replace(/\s+/g, '')), text); }

let passed = 0, failed = 0; const fails = [];
function check(name, cond, extra = '') { if (cond) { passed++; console.log(`  ✔ ${name}`); } else { failed++; fails.push(name); console.log(`  ✘ ${name} ${extra}`); } return !!cond; }
function summary() { console.log(`\n== ${passed} passed, ${failed} failed ==`); if (failed) { console.log(fails.map((f) => ' - ' + f).join('\n')); } return failed; }
module.exports = { launch, newPage, shot, login, api, goto, textOf, clickText, waitText, bodyHas, check, summary, sleep, WEB, SHOTS };
