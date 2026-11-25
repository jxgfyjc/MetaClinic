const fs = require('fs');
const path = require('path');
const puppeteer = require('puppeteer');

const CHROME = '/root/.cache/puppeteer/chrome/linux-157.0.8103.78/chrome-linux64/chrome';
const WEB = 'http://127.0.0.1:3100';
const SHOTS = '/cephfs/yujc/metaclinic-promo/phase1/screenshots';
const LOGS = '/cephfs/yujc/metaclinic-promo/phase1/logs';
const EXAMPLE_MD = '/cephfs/yujc/HisCopilot/instruct/example.md';

fs.mkdirSync(SHOTS, { recursive: true });
fs.mkdirSync(LOGS, { recursive: true });

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const results = { timings: {}, robustness: {}, screenshots: [], errors: [] };

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
    // 1. LOGIN SCREENSHOT + ADMIN creates proxy profile
    const adm = await newCtxPage(browser);
    await adm.goto(`${WEB}/login`, { waitUntil: 'networkidle2', timeout: 60000 });
    await adm.waitForSelector('#u');
    await sleep(600);
    await shot(adm, '01_login_metaclinic');

    await adm.type('#u', 'admin');
    await adm.type('#p', 'demo123');
    await Promise.all([
      adm.waitForFunction(() => !location.pathname.startsWith('/login'), { timeout: 30000 }),
      adm.click('form button'),
    ]);
    await sleep(800);

    // Create proxy profile via UI
    await adm.goto(`${WEB}/style`, { waitUntil: 'networkidle2' });
    await sleep(600);
    await clickByText(adm, 'button', '新建画像');
    await sleep(400);
    await adm.evaluate(() => {
      const modal = document.querySelector('[role="dialog"], .fixed.inset-0') || document;
      const inputs = Array.from(modal.querySelectorAll('input.input'));
      const setVal = (el, val) => {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(el, val);
        el.dispatchEvent(new Event('input', { bubbles: true }));
        el.dispatchEvent(new Event('change', { bubbles: true }));
      };
      if (inputs[0]) setVal(inputs[0], '朱老（示例医案）');
      if (inputs[1]) setVal(inputs[1], 'instruct/example.md 演示资料');
      if (inputs[2]) setVal(inputs[2], '中医内科');
    });
    // Select dr-001 as reviewer
    const docsRes = await apiCall(adm, 'GET', '/doctors');
    const dr1Doc = docsRes.data.items.find((d) => d.name.includes('张明')) || docsRes.data.items[0];
    await adm.waitForFunction(() => document.querySelectorAll('select.input option').length > 2, { timeout: 10000 });
    await adm.evaluate((drId) => {
      const selects = Array.from(document.querySelectorAll('select.input'));
      const revSel = selects[selects.length - 1];
      if (revSel) {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, 'value').set;
        setter.call(revSel, drId);
        revSel.dispatchEvent(new Event('change', { bubbles: true }));
      }
    }, dr1Doc.id);
    await sleep(300);
    await clickByText(adm, 'button', '创建');
    await adm.waitForFunction(() => /\/style\/sty_/.test(location.pathname), { timeout: 15000 });
    const realPid = adm.url().split('/style/')[1].split('/')[0].split('#')[0];
    results.profile_id = realPid;
    console.log('Created profile:', realPid);
    await sleep(600);

    // 2. DR-001: import example.md, review cases, extract
    const dr1 = await newCtxPage(browser);
    await login(dr1, 'dr-001', 'demo123');
    await dr1.goto(`${WEB}/style/${realPid}#imports`, { waitUntil: 'networkidle2' });
    await sleep(800);
    await shot(dr1, '02_style_import_input');

    const fileInput = await dr1.$('input[type="file"]');
    await fileInput.uploadFile(EXAMPLE_MD);
    await dr1.evaluate(() => {
      const inputs = Array.from(document.querySelectorAll('input.input'));
      const lbl = inputs.find((i) => i.placeholder && i.placeholder.includes('2019'));
      if (lbl) {
        const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value').set;
        setter.call(lbl, '朱老医案40例（example.md）');
        lbl.dispatchEvent(new Event('input', { bubbles: true }));
      }
    });
    await sleep(300);
    await clickByText(dr1, 'button', '开始导入');
    await dr1.waitForFunction(() => /\/imports\/sti_/.test(location.pathname), { timeout: 20000 });
    const iid = dr1.url().split('/imports/')[1].split('/')[0].split('#')[0];
    results.import_id = iid;

    // Wait for parsing to finish AND UI to render CaseReview
    await dr1.waitForFunction(
      async (iid) => {
        const r = await fetch(`/api/v1/style/imports/${iid}`).then((x) => x.json());
        return r.import && r.import.status === 'needs_review';
      },
      { timeout: 60000, polling: 1000 },
      iid
    );
    await dr1.waitForFunction(() => document.body.innerText.includes('逐份核对病例'), { timeout: 15000 });
    await sleep(600);
    await shot(dr1, '03_style_import_parsed_cases');

    const impDetail = (await apiCall(dr1, 'GET', `/style/imports/${iid}`)).data.import;
    const allCases = (await apiCall(dr1, 'GET', `/style/imports/${iid}/cases?size=100`)).data.cases;
    results.import_stats_parsed = impDetail.stats;
    results.cases_sample = {
      total: allCases.length,
      cases: allCases.filter((c) => c.kind === 'case').length,
      notes: allCases.filter((c) => c.kind === 'note').length,
      redaction_risks: {
        low: allCases.filter((c) => c.redaction?.risk === 'low').length,
        medium: allCases.filter((c) => c.redaction?.risk === 'medium').length,
        high: allCases.filter((c) => c.redaction?.risk === 'high').length,
      },
      case1_text_preview: allCases[0]?.text?.slice(0, 120),
      case1_herbs: allCases[0]?.herbs?.map((h) => `${h.name}${h.dose_g}g`),
      duplicates: allCases.filter((c) => c.dup_of_id).map((c) => ({ seq: c.seq, label: c.source_label, score: c.dup_score })),
    };

    // Confirm clean cases via UI button, then confirm remaining pending flagged cases if any
    await clickByText(dr1, 'button', '确认无警告病例');
    await sleep(1000);
    const hasMorePending = await dr1.evaluate(() => {
      const btn = Array.from(document.querySelectorAll('button')).find((b) => b.textContent && b.textContent.includes('确认全部待确认'));
      return btn && !btn.disabled;
    });
    if (hasMorePending) {
      await clickByText(dr1, 'button', '确认全部待确认');
      await sleep(400);
      await clickByText(dr1, 'button', '全部确认');
      await sleep(1000);
    }

    // Confirm any remaining pending_review (e.g. near-duplicate) individually via API so all 80 are confirmed
    const afterBulk = (await apiCall(dr1, 'GET', `/style/imports/${iid}/cases?status=pending_review&size=100`)).data.cases;
    results.remaining_pending_after_bulk = afterBulk.length;
    for (const c of afterBulk) {
      await apiCall(dr1, 'PATCH', `/style/cases/${c.id}`, { status: 'confirmed', row_version: c.row_version });
    }
    await dr1.reload({ waitUntil: 'networkidle2' });
    await sleep(800);

    // Extract using default local model mode (ExtractCard is promoted to top once pending === 0)
    const t0Ext = Date.now();
    await dr1.waitForFunction(() => document.body.innerText.includes('开始抽取'), { timeout: 10000 });
    await dr1.evaluate(() => {
      const labels = Array.from(document.querySelectorAll('label'));
      const localLbl = labels.find((l) => l.textContent && l.textContent.includes('本机模型（推荐）'));
      const radio = localLbl && localLbl.querySelector('input[type="radio"]');
      if (radio && !radio.checked) radio.click();
    });
    await sleep(300);
    await clickByText(dr1, 'button', '开始抽取');

    await dr1.waitForFunction(
      async (iid) => {
        const r = await fetch(`/api/v1/style/imports/${iid}`).then((x) => x.json());
        return r.import && ['extracted', 'failed'].includes(r.import.status);
      },
      { timeout: 300000, polling: 2000 },
      iid
    );
    results.timings.extract_s = ((Date.now() - t0Ext) / 1000).toFixed(1);
    await dr1.waitForFunction(() => document.body.innerText.includes('抽取完成'), { timeout: 15000 });
    await sleep(600);
    await shot(dr1, '04_style_extract_done');

    const impDone = (await apiCall(dr1, 'GET', `/style/imports/${iid}`)).data.import;
    results.import_stats_extracted = impDone.stats;

    // 3. Review entries: single + batch + critical + refresh check + publish
    await dr1.goto(`${WEB}/style/${realPid}#review`, { waitUntil: 'networkidle2' });
    await sleep(1000);
    await shot(dr1, '05_style_entries_pending');

    // Approve first non-critical entry individually
    const draftRes = (await apiCall(dr1, 'GET', `/style/profiles/${realPid}/entries?version=draft`)).data;
    const entries = draftRes.entries || [];
    const hasCrit = (e) => (e.flags || []).some((f) => f.severity === 'critical');
    const cleanPending = entries.filter((e) => e.review_status === 'pending' && !hasCrit(e));
    const critPending = entries.filter((e) => e.review_status === 'pending' && hasCrit(e));
    results.entries_summary = { total: entries.length, clean: cleanPending.length, critical: critPending.length };

    if (cleanPending.length > 0) {
      await apiCall(dr1, 'POST', `/style/entries/${cleanPending[0].id}/review`, { action: 'approve', row_version: cleanPending[0].row_version });
    }
    await dr1.reload({ waitUntil: 'networkidle2' });
    await dr1.waitForSelector('li input[type="checkbox"]:not(:disabled)', { timeout: 15000 });
    await sleep(400);

    // Select up to 6 clean pending entries via checkboxes in UI
    await dr1.evaluate(() => {
      const cbs = Array.from(document.querySelectorAll('li input[type="checkbox"]:not(:disabled)'));
      for (const cb of cbs.slice(0, 6)) cb.click();
    });
    await dr1.waitForFunction(() => document.body.innerText.includes('已选 6 条待审核经验') || document.body.innerText.includes('批量通过'), { timeout: 5000 });
    await sleep(400);
    await shot(dr1, '06_style_bulk_selected');

    await clickByText(dr1, 'button', '批量通过', true);
    await sleep(400);
    await shot(dr1, '07_style_bulk_confirm');

    await clickByText(dr1, 'button', '确认通过');
    await sleep(1500);
    await shot(dr1, '08_style_bulk_result');
    await clickByText(dr1, 'button', '知道了');
    await sleep(500);

    // Approve remaining clean entries in batch and critical entries as approve_no_ai so we can publish v1
    const restDraft = (await apiCall(dr1, 'GET', `/style/profiles/${realPid}/entries?version=draft`)).data.entries || [];
    const restClean = restDraft.filter((e) => e.review_status === 'pending' && !hasCrit(e));
    if (restClean.length > 0) {
      await apiCall(dr1, 'POST', `/style/profiles/${realPid}/entries/bulk-review`, {
        action: 'approve',
        request_id: 'e2e-rest-clean',
        items: restClean.map((e) => ({ id: e.id, row_version: e.row_version })),
      });
    }
    for (const e of restDraft.filter((e) => e.review_status === 'pending' && hasCrit(e))) {
      await apiCall(dr1, 'POST', `/style/entries/${e.id}/review`, { action: 'approve_no_ai', note: '演示资料含毒性/配伍提示，仅作资料保存' });
    }

    // Reload & verify persistence
    await dr1.reload({ waitUntil: 'networkidle2' });
    await sleep(800);
    await shot(dr1, '09_style_entries_persisted');

    // Publish v1
    await apiCall(dr1, 'POST', `/style/profiles/${realPid}/publish`, { note: 'example.md 40案首版发布' });
    await dr1.reload({ waitUntil: 'networkidle2' });
    await sleep(800);
    await shot(dr1, '10_style_profile_overview');

    // 4. Sharing: dr-001 declares, admin approves
    const decl = await apiCall(dr1, 'POST', `/style/profiles/${realPid}/authorizations`, {
      basis_type: 'publication',
      basis_text: '演示资料：instruct/example.md 40例公开医案',
      authorizer_name: '教研室演示备案',
      scope: 'hospital',
    });
    results.auth_declare_status = decl.status;
    if (decl.data?.authorization?.id) {
      const appAuth = await apiCall(adm, 'POST', `/style/authorizations/${decl.data.authorization.id}/approve`);
      results.auth_approve_status = appAuth.status;
    }

    // 5. DR-003: Library view + full clinical encounter with profile selection
    const dr3 = await newCtxPage(browser);
    await login(dr3, 'dr-003', 'demo123');
    await dr3.goto(`${WEB}/style/${realPid}`, { waitUntil: 'networkidle2' });
    await sleep(800);
    await shot(dr3, '11_style_profile_library');

    // Dashboard / workstation
    await dr3.goto(`${WEB}/`, { waitUntil: 'networkidle2' });
    await sleep(800);
    await shot(dr3, '12_workstation_home');

    // Create demo patient & encounter
    const patRes = await apiCall(dr3, 'POST', '/patients', {
      name: '演示患者张某',
      gender: '女',
      age: 56,
      phone: '13900009999',
    });
    const patId = patRes.data.patient.id;
    const encRes = await apiCall(dr3, 'POST', '/encounters', {
      patient_id: patId,
      chief_complaint: '双手指关节肿痛伴晨僵2年，遇冷加重',
      hpi: '2年前无明显诱因出现双手近端指间关节肿胀疼痛，晨僵约1小时，怯冷，腰膝酸软，舌淡苔白，脉细涩。',
      visit_type: 'initial',
    });
    const eid = encRes.data.encounter.id;
    results.encounter_id = eid;

    await dr3.goto(`${WEB}/encounters/${eid}`, { waitUntil: 'networkidle2' });
    await sleep(1000);
    await shot(dr3, '13_encounter_intake');

    // Switch to Tab 2: AI 分析与诊断, then start AI analysis
    await clickByText(dr3, 'button', 'AI 分析与诊断');
    await sleep(400);
    const t0Ai = Date.now();
    await clickByText(dr3, 'button', '开始 AI 预分析');
    await dr3.waitForFunction(
      async (eid) => {
        const r = await fetch(`/api/v1/encounters/${eid}`).then((x) => x.json());
        return r.analysis && ['succeeded', 'completed', 'partial', 'failed', 'cancelled'].includes(r.analysis.status);
      },
      { timeout: 180000, polling: 2000 },
      eid
    );
    await dr3.waitForFunction(() => document.body.innerText.includes('最终诊断'), { timeout: 15000 });
    results.timings.ai_analyze_s = ((Date.now() - t0Ai) / 1000).toFixed(1);
    await sleep(600);
    await shot(dr3, '14_encounter_ai_analysis');

    // Accept up to 2 pending diagnosis candidates, then click "其余待审标为不采纳" if remaining
    const ws = (await apiCall(dr3, 'GET', `/encounters/${eid}`)).data;
    results.ai_analysis_status = ws.analysis?.status;
    const cands = (ws.diagnoses || []).filter((d) => d.status === 'pending');
    results.ai_candidates_count = cands.length;
    for (const d of cands.slice(0, 2)) {
      await apiCall(dr3, 'POST', `/diagnoses/${d.id}/review`, { action: 'accept', expected_version: d.row_version });
    }
    await dr3.reload({ waitUntil: 'networkidle2' });
    await clickByText(dr3, 'button', 'AI 分析与诊断');
    await sleep(600);
    const hasRejectRest = await dr3.evaluate(() =>
      Array.from(document.querySelectorAll('button')).some((b) => b.textContent && b.textContent.includes('其余待审标为不采纳') && !b.disabled)
    );
    if (hasRejectRest) {
      await clickByText(dr3, 'button', '其余待审标为不采纳');
      await sleep(1000);
    }
    await shot(dr3, '15_encounter_diagnosis_review');

    // Switch to Tab 3: 治疗方案, select the published style profile in StylePanel
    await clickByText(dr3, 'button', '治疗方案');
    await sleep(800);
    await dr3.evaluate(() => {
      const cbs = Array.from(document.querySelectorAll('input[type="checkbox"]'));
      const target = cbs.find((cb) => cb.closest('label')?.textContent?.includes('朱老'));
      if (target && !target.checked) target.click();
    });
    await sleep(1000);
    await shot(dr3, '16_encounter_style_selected');

    // Generate treatment plan
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

    // Accept pending therapy items via segmented sub-tab + batch accept button, adopt style row, and confirm plan
    const wsPlan = (await apiCall(dr3, 'GET', `/encounters/${eid}`)).data;
    const plan = wsPlan.plan;
    results.plan_style_comparison_count = (plan?.style?.comparison || []).length;
    results.plan_therapy_initial_count = (plan?.therapy_items || []).length;
    if ((plan?.therapy_items || []).some((t) => t.status === 'pending')) {
      await clickByText(dr3, 'button', '针灸 / 推拿 / 理疗');
      await sleep(400);
      await clickByText(dr3, 'button', '全部接受待审项目');
      await sleep(1000);
    }
    const wsPlan15 = (await apiCall(dr3, 'GET', `/encounters/${eid}`)).data;
    const plan15 = wsPlan15.plan;
    if (plan15 && (plan15.style?.comparison || []).length > 0) {
      await apiCall(dr3, 'POST', `/style/plans/${plan15.id}/decision`, {
        index: 0,
        decision: 'adopt',
        note: '符合本患者寒湿痹阻兼肾虚表现，参考采用',
        row_version: plan15.row_version,
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
      results.prescriptions_count = (rxGen.data?.prescriptions || []).length;
    }
    await dr3.reload({ waitUntil: 'networkidle2' });
    await clickByText(dr3, 'button', '处方');
    await sleep(800);
    await shot(dr3, '19_encounter_rx_populated');

    // Also inspect Record tab
    await clickByText(dr3, 'button', '病历');
    await sleep(800);
    await shot(dr3, '20_encounter_record');

    // Robustness checks:
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

    // (b) Cross-batch exact duplicate exclusion (re-upload example.md into a new batch on the same profile)
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
        duplicates_exact: imp2.stats?.duplicates_exact,
        excluded: imp2.stats?.excluded,
        pending: imp2.stats?.pending,
      };
      // Cancel this test batch so it doesn't pollute UI
      await apiCall(dr1, 'POST', `/style/imports/${iid2}/cancel`);
    }

    // (c) Active profile category distribution & entry-to-example.md traceability sample
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
    console.log('ALL DONE:', JSON.stringify(results.timings));
  } finally {
    await browser.close();
  }
})();
