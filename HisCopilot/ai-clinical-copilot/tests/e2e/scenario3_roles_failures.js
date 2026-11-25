// Scenarios 4 (no cross-role / cross-patient access), 5 (ASR / OCR / LLM failures are visible and recoverable),
// 7 (duplicate clicks, concurrent writers, stale versions), pharmacy hand-off — against the fake-AI stack with injected failures.
const L = require('./lib');
const fs = require('fs'), path = require('path');
const ROOT = path.resolve(__dirname, '../../..');
const AUDIO = path.join(ROOT, 'test/番禺路723号.m4a'), LAB = path.join(ROOT, 'test/微信图片_20261013234121_69_2.jpg');
const S1 = JSON.parse(fs.readFileSync(fs.existsSync('/tmp/v3shots/scenario2.json') ? '/tmp/v3shots/scenario2.json' : '/tmp/v3shots/scenario1.json', 'utf8'));   // a dr-001 visit (scenario 2's, with signed prescriptions, when available)
const PW = 'Demo12345';

/** run N identical API calls truly in parallel inside the browser */
const parallel = (page, method, p, body, n, headers) => page.evaluate(async (method, p, body, n, headers) => Promise.all(Array.from({ length: n }, () => fetch('/api/v1' + p, { method, headers: { 'Content-Type': 'application/json', ...(headers || {}) }, body: body ? JSON.stringify(body) : undefined }).then(async (r) => ({ s: r.status, j: await r.json().catch(() => null) })))), method, p, body, n, headers);
const sess = async (b, user) => { const p = await L.newPage(b, { allow: [/\/auth\/me/] }); await L.login(p, user, PW); return p; };
async function typeIn(page, sel, v) { const el = await page.$(sel); await el.click({ clickCount: 3 }); await page.keyboard.press('Backspace'); await el.type(v); }
async function confirmDialog(page, btn = '确认') { await L.sleep(300); await page.evaluate((btn) => { const d = document.querySelector('[role=dialog]'); [...d.querySelectorAll('button')].reverse().find((x) => x.innerText.replace(/\s+/g, '').includes(btn) && !x.disabled).click(); }, btn); await L.sleep(500); }

(async () => {
  const b = await L.launch();
  try {
    const d1 = await sess(b, 'dr-001'), d2 = await sess(b, 'dr-002'), nu = await sess(b, 'nu-001'), ph = await sess(b, 'ph-001'), ad = await sess(b, 'admin');
    const anon = await L.newPage(b, { allow: [/.*/] });
    await anon.goto(L.WEB + '/login', { waitUntil: 'networkidle2' });

    // ================================================================ 4. access control — enforced on the SERVER (UI hiding is only cosmetic)
    console.log('\n# access control');
    const enc = (await L.api(d1, 'GET', `/encounters/${S1.enc}`)).body;
    const rxList = (await L.api(d1, 'GET', `/encounters/${S1.enc}/prescriptions`)).body.items;
    for (const [what, call] of [['patients', () => L.api(anon, 'GET', '/patients')], ['encounter bundle', () => L.api(anon, 'GET', `/encounters/${S1.enc}`)], ['prescription', () => L.api(anon, 'GET', `/prescriptions/${rxList[0].id}`)],
      ['record PDF', () => L.api(anon, 'POST', `/encounters/${S1.enc}/record`)], ['audit log', () => L.api(anon, 'GET', '/admin/audit')], ['artifact file', () => L.api(anon, 'GET', `/artifacts/${enc.artifacts[0].id}/file`)]]) {
      const r = await call(); L.check(`anonymous → ${what}: 401`, r.status === 401, String(r.status));
    }
    const o = await d2.evaluate(async (e) => { const r = await fetch('/api/v1/encounters/' + e); return { s: r.status, j: await r.json() }; }, S1.enc);
    L.check("another doctor cannot open dr-001's encounter (403 not_your_encounter)", o.s === 403 && o.j.error.code === 'not_your_encounter', JSON.stringify(o));
    L.check("another doctor's artifact file is refused", (await L.api(d2, 'GET', `/artifacts/${enc.artifacts[0].id}/file`)).status === 403);
    L.check("another doctor cannot read dr-001's prescriptions", (await L.api(d2, 'GET', `/prescriptions/${rxList[0].id}`)).status === 403);
    const q2 = (await L.api(d2, 'GET', '/encounters?scope=all&limit=100')).body;
    L.check("dr-002's queue contains none of dr-001's visits", !q2.items.some((e) => e.id === S1.enc));
    await L.goto(d2, `/encounters/${S1.enc}`, 1500);
    L.check('UI shows a clear "belongs to another doctor" error instead of data', await L.bodyHas(d2, '属于其他医师') && !(await L.bodyHas(d2, '陈测试')));
    await L.shot(d2, 'f1-other-doctor');
    const perm = async (page, method, p, body) => { const r = await L.api(page, method, p, body); return [r.status, r.body && r.body.error && r.body.error.code]; };
    const dxId = enc.diagnoses[0].id, rx0 = rxList[0].id;
    L.check('nurse cannot review diagnoses', (await perm(nu, 'POST', `/diagnoses/${dxId}/review`, { action: 'accept' }))[0] === 403);
    L.check('nurse cannot sign prescriptions', (await perm(nu, 'POST', `/prescriptions/${rx0}/sign`, {}))[0] === 403);
    L.check('nurse cannot finalize records', (await perm(nu, 'POST', `/records/${(await L.api(d1, 'POST', `/encounters/${S1.enc}/record`)).body.record.id}/finalize`, {}))[0] === 403);
    L.check('nurse cannot generate plans or run AI analysis', (await perm(nu, 'POST', `/encounters/${S1.enc}/plan/generate`, {}))[0] === 403 && (await perm(nu, 'POST', `/encounters/${S1.enc}/analysis`, {}))[0] === 403);
    L.check('admin cannot sign prescriptions or finalize records (separation of duties)', (await perm(ad, 'POST', `/prescriptions/${rx0}/sign`, {}))[0] === 403 && (await perm(ad, 'POST', `/records/x/finalize`, {}))[0] === 403);
    L.check('admin cannot make diagnoses', (await perm(ad, 'POST', `/diagnoses/${dxId}/review`, { action: 'accept' }))[0] === 403);
    L.check('pharmacist cannot list patients or encounters', (await perm(ph, 'GET', '/patients'))[0] === 403 && (await perm(ph, 'GET', `/encounters/${S1.enc}`))[0] === 403);
    L.check('doctor cannot touch admin endpoints (users / audit / jobs)', (await perm(d1, 'GET', '/admin/users'))[0] === 403 && (await perm(d1, 'GET', '/admin/audit'))[0] === 403 && (await perm(d1, 'GET', '/admin/jobs'))[0] === 403);
    L.check('nobody but admin can create accounts', (await perm(d1, 'POST', '/admin/users', { username: 'x1', display_name: 'x', role: 'ADMIN', password: 'Abcdef12' }))[0] === 403);
    const phQ = (await L.api(ph, 'GET', '/prescriptions?limit=100')).body.items;
    L.check('pharmacist sees only signed-or-later prescriptions (no drafts)', phQ.every((r) => !['draft', 'voided'].includes(r.status)));
    await L.goto(nu, '/pharmacy', 1200); L.check('UI: a nurse cannot see the pharmacy / admin menus', !(await nu.evaluate(() => [...document.querySelectorAll('aside a')].some((a) => /\/pharmacy|\/admin/.test(a.getAttribute('href'))))));
    // patient tokens: scoped, and useless on staff endpoints
    const link = (await L.api(d1, 'POST', '/intake-links', { hours: 1 })).body.url.split('#t=')[1];
    const sessTok = (await anon.evaluate(async (t) => (await (await fetch('/api/v1/p/intake/sessions', { method: 'POST', headers: { 'X-XY-Token': t, 'Content-Type': 'application/json' }, body: '{}' })).json()).token, link));
    const asTok = (p, tok, m = 'GET') => anon.evaluate(async (p, tok, m) => (await fetch('/api/v1' + p, { method: m, headers: { 'X-XY-Token': tok } })).status, p, tok, m);
    L.check('a patient session token cannot read staff data (patients / encounters)', [401, 403].includes(await asTok('/patients', sessTok)) && [401, 403].includes(await asTok(`/encounters/${S1.enc}`, sessTok)));
    L.check('an intake LINK token cannot read a draft (needs the session token)', [401, 403].includes(await asTok('/p/intake/draft', link)));
    L.check("a session token cannot fetch another visit's artifact", [401, 403, 404].includes(await asTok(`/p/intake/files/${enc.artifacts[0].id}`, sessTok)));
    L.check('forged / garbage tokens are rejected', (await asTok('/p/intake/draft', 'not-a-real-token')) === 401);

    // ================================================================ 5. failures are visible, explained and recoverable
    console.log('\n# failure visibility & recovery');
    const pid = (await L.api(d1, 'POST', '/patients', { name: '故障测试', phone: '13600003333', allergies: [{ allergen: '青霉素' }] })).body.patient.id;
    const eid = (await L.api(d1, 'POST', '/encounters', { patient_id: pid, chief_complaint: '头痛三天 FAKE_FAIL_LLM' })).body.encounter.id;
    await L.goto(d1, `/encounters/${eid}`, 1500);
    // ASR: the service goes down → failure is visible with the reason and a retry; the service heals → one click on retry recovers
    const FLAGS = '/tmp/xy_e2e/fake_flags'; fs.mkdirSync(FLAGS, { recursive: true });
    fs.writeFileSync(`${FLAGS}/asr_fail`, '1');
    const aIn = await d1.$('input[type=file][accept="audio/*"]'); await aIn.uploadFile(AUDIO);
    await d1.waitForFunction(() => /处理失败/.test(document.body.innerText), { timeout: 60000 });
    L.check('ASR failure is shown on the recording with the reason and a retry button', await L.bodyHas(d1, '处理失败') && await L.bodyHas(d1, '重试处理') && await L.bodyHas(d1, '测试'));
    await L.shot(d1, 'f2-asr-failed');
    await L.clickText(d1, '重试处理'); await L.sleep(4000);
    L.check('retry while the service is still down: failure stays visible and the attempt counter increases', await L.bodyHas(d1, '已尝试 2 次'));
    fs.rmSync(`${FLAGS}/asr_fail`);                                                  // the service heals…
    await L.clickText(d1, '重试处理');
    await d1.waitForFunction(() => document.querySelectorAll('textarea[aria-label="语音转写文本"]').length === 1, { timeout: 60000 });
    L.check('RECOVERY: after the service is back, "重试处理" turns the failed recording into an editable transcript', true);
    await L.shot(d1, 'f3-asr-recovered');
    // OCR: same pattern
    fs.writeFileSync(`${FLAGS}/ocr_fail`, '1');
    const imgIn = (await d1.$$('input[type=file][accept^="image"]'))[0]; await imgIn.uploadFile(LAB);
    await d1.waitForFunction(() => /OCR/.test(document.body.innerText) && /处理失败/.test(document.body.innerText), { timeout: 90000 });
    L.check('OCR failure is shown with a hint to enter results by hand (and a retry)', await L.bodyHas(d1, '手工录入') && await L.bodyHas(d1, '重试处理'));
    await L.shot(d1, 'f3b-ocr-failed');
    fs.rmSync(`${FLAGS}/ocr_fail`);
    await L.clickText(d1, '重试处理');
    await d1.waitForFunction(() => document.querySelectorAll('table.tbl tbody tr').length >= 3, { timeout: 90000 });
    L.check('RECOVERY: OCR retry after the service is back produces unverified lab rows next to the image', await L.bodyHas(d1, '项待核对'));
    // LLM failure → analysis fails visibly; fix the data → retry succeeds
    await L.clickText(d1, 'AI 分析与诊断'); await L.sleep(500); await L.clickText(d1, '开始 AI 预分析');
    await d1.waitForFunction(() => /AI 预分析失败/.test(document.body.innerText), { timeout: 120000 });
    L.check('AI analysis failure is explained (code + message + "retry" and "do it by hand" are both offered)', await L.bodyHas(d1, '重试分析') && await L.bodyHas(d1, '手工添加诊断'));
    await L.shot(d1, 'f4-analysis-failed');
    L.check('no fake diagnoses were invented after the failure', (await L.api(d1, 'GET', `/encounters/${eid}`)).body.diagnoses.length === 0);
    await L.clickText(d1, '接诊资料'); await L.sleep(500);
    await typeIn(d1, 'input[maxlength="500"]', '头痛三天'); await d1.waitForFunction(() => /已保存/.test(document.body.innerText), { timeout: 15000 });
    await L.clickText(d1, 'AI 分析与诊断'); await L.sleep(500); await L.clickText(d1, '重试分析');
    await d1.waitForFunction(() => /西医诊断候选/.test(document.body.innerText) && /接受/.test(document.body.innerText), { timeout: 120000 });
    L.check('recovery: after fixing the input a retry produces results', true);
    // stale analysis banner after a fact changes
    await L.clickText(d1, '接诊资料'); await L.sleep(400);
    const hpi = await d1.$('textarea[placeholder*="发病经过"]'); await hpi.click(); await hpi.type('新增：昨晚出现恶心');
    await d1.waitForFunction(() => /已保存/.test(document.body.innerText), { timeout: 15000 });
    await L.clickText(d1, 'AI 分析与诊断'); await L.sleep(800);
    L.check('changing a clinical fact marks the existing AI result as based on older data (stale banner + re-run)', await L.bodyHas(d1, '临床资料在分析之后有变化') && await L.bodyHas(d1, '用最新资料重新分析'));
    const hist0 = (await L.api(d1, 'GET', `/encounters/${eid}/analysis/history`)).body.items.length;
    const rr = await parallel(d1, 'POST', `/encounters/${eid}/analysis`, { force: false }, 4);
    await L.sleep(300); const hist1 = (await L.api(d1, 'GET', `/encounters/${eid}/analysis/history`)).body.items.length;
    L.check('4 simultaneous "analyze" requests start exactly ONE new analysis', hist1 - hist0 === 1 && rr.every((x) => x.s === 202), `${hist0}→${hist1} ${rr.map((x) => x.s)}`);

    // ================================================================ 7. duplicate clicks / concurrent writers
    console.log('\n# duplicates & concurrency');
    const k = 'dup-' + Date.now();
    const pid2 = (await L.api(d1, 'POST', '/patients', { name: '并发建诊', phone: '13500004444' })).body.patient.id;
    const cr = await parallel(d1, 'POST', '/encounters', { patient_id: pid2, chief_complaint: '测试' }, 6, { 'Idempotency-Key': k });
    const ids = new Set(cr.filter((x) => x.s < 300).map((x) => x.j.encounter.id));
    L.check('6 parallel "create encounter" calls with one Idempotency-Key → one encounter, no 5xx', ids.size === 1 && cr.every((x) => x.s < 500), JSON.stringify(cr.map((x) => x.s)));
    const cr2 = await parallel(d1, 'POST', '/encounters', { patient_id: (await L.api(d1, 'POST', '/patients', { name: '无键并发', phone: '13500005555' })).body.patient.id, chief_complaint: '测试' }, 6);
    const ids2 = new Set(cr2.filter((x) => x.s < 300).map((x) => x.j.encounter.id));
    L.check('even WITHOUT a key, a burst of identical creates yields one open visit per patient/doctor and no 5xx', ids2.size === 1 && cr2.every((x) => x.s < 500), JSON.stringify(cr2.map((x) => x.s)) + ' ids=' + ids2.size);
    const e0 = (await L.api(d1, 'GET', `/encounters/${eid}`)).body;
    const fp = await parallel(d1, 'PATCH', `/encounters/${eid}/facts`, { expected_version: e0.encounter.row_version, hpi: '并发写入测试' }, 5);
    L.check('5 writers with the same row_version: exactly one wins, the rest get a stale_version conflict (never a silent overwrite)', fp.filter((x) => x.s === 200).length === 1 && fp.filter((x) => x.s === 409 && x.j.error.code === 'stale_version').length === 4, JSON.stringify(fp.map((x) => x.s)));

    // pharmacy hand-off on scenario 2's signed prescriptions (if that scenario ran)
    console.log('\n# pharmacy');
    const signed = rxList.filter((r) => r.status === 'signed' || r.status === 'pharmacy_review');
    if (signed.length) {
      const r0 = signed[0];
      await L.goto(ph, '/pharmacy', 1500); L.check('pharmacist queue lists the signed prescription', await L.bodyHas(ph, '待接单') && await L.bodyHas(ph, '刘闭环'));
      await L.shot(ph, 'f5-pharmacy');
      await L.goto(ph, `/prescriptions/${r0.id}`, 1500);
      L.check('pharmacist sees allergy, items and the doctor\'s safety acknowledgements', await L.bodyHas(ph, '过敏史') && await L.bodyHas(ph, '青霉素') && await L.bodyHas(ph, '签署时的安全校验'));
      const st = await parallel(ph, 'POST', `/pharmacy/prescriptions/${r0.id}/start`, {}, 4);
      L.check('4 simultaneous "start review" clicks → no 5xx, exactly one pharmacy_review state', st.every((x) => x.s < 500) && (await L.api(ph, 'GET', `/prescriptions/${r0.id}`)).body.prescription.status === 'pharmacy_review', JSON.stringify(st.map((x) => x.s)));
      await L.goto(ph, `/prescriptions/${r0.id}`, 1200); await L.clickText(ph, '退回医师'); await L.sleep(400);
      L.check('returning a prescription requires a written reason', await ph.evaluate(() => [...document.querySelectorAll('[role=dialog] button')].reverse().find((x) => x.innerText.includes('退回')).disabled));
      await ph.type('[role=dialog] textarea', '剂量与适应证不符，请复核'); await confirmDialog(ph, '退回'); await L.sleep(1200);
      L.check('returned with reason; status is now "returned"', (await L.api(ph, 'GET', `/prescriptions/${r0.id}`)).body.prescription.status === 'returned');
      await L.goto(d1, `/encounters/${S1.enc}?step=rx`, 1800);
      L.check('the doctor sees the pharmacy return and its reason on the prescription', await L.bodyHas(d1, '药房退回了这张处方') && await L.bodyHas(d1, '剂量与适应证不符'));
      await L.shot(d1, 'f6-rx-returned');
    } else console.log('  (no signed prescription from scenario 2 — pharmacy part skipped)');
    // 4xx responses in this scenario are deliberate negative tests; what must NEVER appear is a page crash, a console error or a server error (5xx)
    const bad = d1.problems.concat(d2.problems, nu.problems, ph.problems, ad.problems).filter((p) => /\[pageerror\]|\[console\.error\]|\[http 5\d\d\]/.test(p));
    L.check('no page crashes, console errors or 5xx responses on any role during the access / failure / concurrency tests', bad.length === 0, bad.join(' | ').slice(0, 600));
  } finally { await b.close(); }
  process.exit(L.summary() ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(2); });
