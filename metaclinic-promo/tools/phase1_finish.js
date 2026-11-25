const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer');

const CHROME = '/root/.cache/puppeteer/chrome/linux-157.0.8103.78/chrome-linux64/chrome';
const WEB = 'http://127.0.0.1:3100';
const SHOTS = '/cephfs/yujc/metaclinic-promo/phase1/screenshots';
const LOGS = '/cephfs/yujc/metaclinic-promo/phase1/logs';
const EXAMPLE_MD = '/cephfs/yujc/HisCopilot/instruct/example.md';

const realPid = 'sty_01m86p6g46w16wsj4end';
const eid = 'enc_01m86p8mcgjfbmwsva88';

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = { profile_id: realPid, encounter_id: eid, timings: {}, robustness: {}, screenshots: [], errors: [] };

async function shot(page, name) {
  const f = path.join(SHOTS, `${name}.png`);
  await page.screenshot({ path: f, fullPage: false });
  results.screenshots.push(f);
  console.log('SHOT:', name);
  return f;
}

async function newCtxPage(browser) {
  const ctx = await browser.createBrowserContext();
  const page = await ctx.newPage();
  page.ctx = ctx;
  await page.setViewport({ width: 1600, height: 1000, deviceScaleFactor: 2 });
  page.on('pageerror', (e) => results.errors.push(`pageerror: ${e.message}`));
  return page;
}

async function login(page, u, p) {
  await page.goto(`${WEB}/login`, { waitUntil: 'networkidle2', timeout: 60000 });
  await page.waitForSelector('#u');
  await page.type('#u', u);
  await page.type('#p', p);
  await Promise.all([
    page.waitForFunction(() => !location.pathname.startsWith('/login'), { timeout: 30000 }),
    page.click('form button'),
  ]);
  await sleep(800);
}

async function apiCall(page, method, p, body) {
  return page.evaluate(async (method, p, body) => {
    const r = await fetch('/api/v1' + p, {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body: body ? JSON.stringify(body) : undefined,
    });
    return { status: r.status, data: await r.json().catch(() => null) };
  }, method, p, body);
}

async function clickByText(page, selector, text, exact = false) {
  const clicked = await page.evaluate((sel, txt, exact) => {
    const els = Array.from(document.querySelectorAll(sel));
    const el = els.find((e) => {
      if (e.disabled || !e.textContent) return false;
      const t = e.textContent.trim();
      return exact ? t === txt : t.includes(txt);
    });
    if (el) { el.click(); return true; }
    return false;
  }, selector, text, exact);
  if (!clicked) throw new Error(`Button not found/enabled: ${selector} "${text}"`);
}

(async () => {
  const browser = await puppeteer.launch({
    executablePath: CHROME,
    headless: 'new',
    args: ['--no-sandbox', '--disable-gpu', '--disable-dev-shm-usage'],
  });
  try {
    const dr3 = await newCtxPage(browser);
    await login(dr3, 'dr-003', 'demo123');

    await dr3.goto(`${WEB}/encounters/${eid}`, { waitUntil: 'networkidle2' });
    await dr3.waitForFunction(() => document.body.innerText.includes('治疗方案'), { timeout: 15000 });
    const ws = (await apiCall(dr3, 'GET', `/encounters/${eid}`)).data;
    results.ai_analysis_status = ws.analysis?.status;
    results.ai_modules = (ws.analysis?.modules || []).map((m) => ({ module: m.module, status: m.status, latency_ms: m.latency_ms }));
    results.ai_candidates_count = (ws.diagnoses || []).length;

    // Tab 3: 治疗方案 -> already selected profile -> generate plan
    await clickByText(dr3, 'button', '治疗方案');
    await dr3.waitForFunction(() => document.body.innerText.includes('起草整体治疗方案') || document.body.innerText.includes('综合治疗方案'), { timeout: 15000 });
    await sleep(500);

    const t0Plan = Date.now();
    await clickByText(dr3, 'button', 'AI 起草治疗方案');
    await dr3.waitForFunction(
      async (eid) => {
        const r = await fetch(`/api/v1/encounters/${eid}`).then((x) => x.json());
        return r.plan && r.plan.id && r.plan.status !== 'generating';
      },
      { timeout: 180000, polling: 2000 },
      eid
    );
    results.timings.plan_gen_s = ((Date.now() - t0Plan) / 1000).toFixed(1);
    await sleep(1200);
    await shot(dr3, '17_encounter_plan_comparison');

    const wsPlan = (await apiCall(dr3, 'GET', `/encounters/${eid}`)).data;
    const plan = wsPlan.plan;
    results.plan_status = plan?.status;
    results.plan_style_comparison_count = (plan?.style?.comparison || []).length;
    if (plan && (plan.style?.comparison || []).length > 0) {
      await apiCall(dr3, 'POST', `/style/plans/${plan.id}/decision`, {
        index: 0,
        decision: 'adopt',
        note: '符合本患者寒湿痹阻兼肾虚表现，参考采用',
        row_version: plan.row_version,
      });
    }
    const wsPlan2 = (await apiCall(dr3, 'GET', `/encounters/${eid}`)).data;
    if (wsPlan2.plan && wsPlan2.plan.status === 'draft') {
      await apiCall(dr3, 'POST', `/plans/${wsPlan2.plan.id}/confirm`, { expected_version: wsPlan2.plan.row_version });
    }
    await dr3.reload({ waitUntil: 'networkidle2' });
    await clickByText(dr3, 'button', '治疗方案');
    await sleep(800);
    await shot(dr3, '18_encounter_plan_confirmed');

    // Generate prescriptions from confirmed plan & screenshot Rx tab
    const wsPlan3 = (await apiCall(dr3, 'GET', `/encounters/${eid}`)).data;
    if (wsPlan3.plan) {
      const rxGen = await apiCall(dr3, 'POST', `/encounters/${eid}/prescriptions/from-plan`, {
        plan_id: wsPlan3.plan.id,
        kinds: ['western', 'patent', 'herbal'],
        acknowledge_stale: false,
      });
      results.prescriptions_generated = rxGen.status;
    }
    await dr3.reload({ waitUntil: 'networkidle2' });
    await clickByText(dr3, 'button', '处方');
    await sleep(800);
    await shot(dr3, '19_encounter_record_rx');

    // Robustness checks as dr-001
    const dr1 = await newCtxPage(browser);
    await login(dr1, 'dr-001', 'demo123');
    const imps = (await apiCall(dr1, 'GET', `/style/profiles/${realPid}/imports`)).data.imports || [];
    results.first_import_stats = imps[0]?.stats || null;

    // (a) Same-batch duplicate file rejection
    const dupCheck = await dr1.evaluate(async (pid) => {
      const fd = new FormData();
      const blob = new Blob(['a,b\n1,2\n'], { type: 'text/csv' });
      fd.append('files', blob, 'dup1.csv');
      fd.append('files', blob, 'dup2.csv');
      const r = await fetch(`/api/v1/style/profiles/${pid}/imports`, { method: 'POST', body: fd });
      return r.json();
    }, realPid);
    results.robustness.same_batch_duplicate_file = dupCheck.rejected?.map((x) => x.code) || [];
    if (dupCheck.import?.id) await apiCall(dr1, 'POST', `/style/imports/${dupCheck.import.id}/cancel`);

    // (b) Cross-batch exact duplicate exclusion
    const exampleContent = fs.readFileSync(EXAMPLE_MD, 'utf8');
    const crossBatch = await dr1.evaluate(async (pid, content) => {
      const fd = new FormData();
      fd.append('label', '跨批次重复校验');
      fd.append('files', new Blob([content], { type: 'text/markdown' }), 'example_dup.md');
      const r = await fetch(`/api/v1/style/profiles/${pid}/imports`, { method: 'POST', body: fd });
      return r.json();
    }, realPid, exampleContent);
    if (crossBatch.import?.id) {
      const iid2 = crossBatch.import.id;
      await dr1.waitForFunction(
        async (id) => {
          const r = await fetch(`/api/v1/style/imports/${id}`).then((x) => x.json());
          return r.import && ['needs_review', 'failed'].includes(r.import.status);
        },
        { timeout: 60000, polling: 1000 },
        iid2
      );
      const imp2 = (await apiCall(dr1, 'GET', `/style/imports/${iid2}`)).data.import;
      results.robustness.cross_batch_duplicate_stats = {
        cases: imp2.stats?.cases,
        notes: imp2.stats?.notes,
        duplicates_exact: imp2.stats?.duplicates_exact,
        excluded: imp2.stats?.excluded,
        pending: imp2.stats?.pending,
      };
      await apiCall(dr1, 'POST', `/style/imports/${iid2}/cancel`);
    }

    // (c) Active profile category distribution & traceability sample
    const activeEntries = (await apiCall(dr1, 'GET', `/style/profiles/${realPid}/entries?version=active`)).data.entries || [];
    const byCat = {};
    for (const e of activeEntries) byCat[e.category] = (byCat[e.category] || 0) + 1;
    results.robustness.active_entries_total = activeEntries.length;
    results.robustness.active_entries_by_category = byCat;
    const sampleFormula = activeEntries.find((e) => e.category === 'formula') || activeEntries[0];
    if (sampleFormula) {
      const detail = (await apiCall(dr1, 'GET', `/style/entries/${sampleFormula.id}`)).data.entry;
      results.robustness.traceability_sample = {
        entry_id: detail.id,
        category: detail.category,
        title: detail.title,
        evidence_count: (detail.evidence || []).length,
        first_evidence: detail.evidence?.[0] ? {
          quote: detail.evidence[0].quote,
          case_seq: detail.evidence[0].case?.seq,
          source_label: detail.evidence[0].case?.source_label,
        } : null,
      };
    }

    fs.writeFileSync(path.join(LOGS, 'flow_results.json'), JSON.stringify(results, null, 2));
    console.log('FINISH DONE:', JSON.stringify(results.timings), 'entries:', activeEntries.length);
  } finally {
    await browser.close();
  }
})();
