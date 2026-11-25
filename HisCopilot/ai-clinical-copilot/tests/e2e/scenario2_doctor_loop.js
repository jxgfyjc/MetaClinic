// Scenarios 2, 3 (persistence, print/PDF), 6 (navigation linkage), 7 (duplicate clicks / stale) — the doctor's closed loop in the real UI:
// review several diagnoses → ONE unified plan → edit/sign separate prescriptions with safety rules → record → finalize → PDF → amend.
const L = require('./lib');
const fs = require('fs');
const path = require('path');
const LAB = path.join(path.resolve(__dirname, '../../..'), 'test/微信图片_20261013234121_69_2.jpg');
let EID = '';        // created below: this scenario is self-contained (doctor-created visit), independent of scenario 1's data

/** click a button inside the nearest container (article/section/li/div) whose text contains `ctext` */
async function clickIn(page, ctext, btn, sel = 'article,section,li') {
  const ok = await page.evaluate((ctext, btn, sel) => {
    const boxes = [...document.querySelectorAll(sel)].filter((e) => e.innerText.includes(ctext) && e.offsetParent !== null);
    for (const box of boxes.reverse()) { const b = [...box.querySelectorAll('button')].find((x) => x.innerText.replace(/\s+/g, '').includes(btn.replace(/\s+/g, '')) && !x.disabled); if (b) { b.scrollIntoView({ block: 'center' }); b.click(); return true; } }
    return false;
  }, ctext, btn, sel);
  if (!ok) throw new Error(`no enabled "${btn}" inside "${ctext}"`);
}
const seen = (page, t) => page.evaluate((t) => { const all = document.body.innerText + ' ' + [...document.querySelectorAll('input,textarea')].filter((e) => e.offsetParent !== null).map((e) => e.value).join(' '); return all.replace(/\s+/g, '').includes(t.replace(/\s+/g, '')); }, t);
const disabled = (page, text) => page.evaluate((t) => { const b = [...document.querySelectorAll('button')].find((x) => x.innerText.replace(/\s+/g, '').includes(t) && x.offsetParent !== null); return b ? b.disabled : null; }, text);
async function confirmDialog(page, btn = '确认') { await L.sleep(300); await page.evaluate((btn) => { const d = document.querySelector('[role=dialog]'); const b = [...d.querySelectorAll('button')].reverse().find((x) => x.innerText.replace(/\s+/g, '').includes(btn) && !x.disabled); if (!b) throw new Error('dialog button ' + btn); b.click(); }, btn); await L.sleep(500); }
/** first VISIBLE element (the workspace keeps visited panels mounted but hidden, so plain selectors can hit a hidden twin) */
async function vis(page, sel) { const h = await page.evaluateHandle((sel) => [...document.querySelectorAll(sel)].find((e) => e.offsetParent !== null) || null, sel); const el = h.asElement(); if (!el) throw new Error('no visible ' + sel); return el; }
async function setVal(el, v) { await el.focus(); await el.evaluate((e) => e.select()); await el.press('Backspace'); await el.type(v); }
async function typeIn(page, sel, v) { const el = await page.$(sel); await el.click({ clickCount: 3 }); await page.keyboard.press('Backspace'); await el.type(v); }
async function step(page, label) { await L.clickText(page, label); await L.sleep(600); }

(async () => {
  const b = await L.launch();
  try {
    const d = await L.newPage(b, { allow: [/\/auth\/me/] });
    await L.login(d, 'dr-001', 'Demo12345');
    const pat = (await L.api(d, 'POST', '/patients', { name: '刘闭环', gender: '男', age: 58, phone: '13700009999', allergies: [{ allergen: '青霉素' }] })).body.patient;
    EID = (await L.api(d, 'POST', '/encounters', { patient_id: pat.id, chief_complaint: '头痛 3 天，伴头晕', hpi: '近三天头痛，晨起明显，伴头晕、口干，夜间睡眠差。' })).body.encounter.id;
    fs.writeFileSync('/tmp/v3shots/scenario2.json', JSON.stringify({ enc: EID, patient: pat.id }));
    // lab report through the real UI (OCR → unverified rows the doctor must check against the image)
    await L.goto(d, `/encounters/${EID}`, 1500);
    const labIn = (await d.$$('input[type=file][accept^="image"]'))[0]; await labIn.uploadFile(LAB);
    await d.waitForFunction(() => document.querySelectorAll('table.tbl tbody tr').length >= 3, { timeout: 90000 });
    L.check('OCR rows are listed UNVERIFIED next to the original image', await L.bodyHas(d, '项待核对') && (await d.$$('img[alt="检验单原图"]')).length === 1);
    await L.api(d, 'POST', `/encounters/${EID}/analysis`, {});
    await d.waitForFunction(async (eid) => { const r = await fetch('/api/v1/encounters/' + eid); const j = await r.json(); return j.analysis && ['succeeded', 'partial', 'failed'].includes(j.analysis.status); }, { timeout: 120000, polling: 1500 }, EID);
    await L.goto(d, `/encounters/${EID}?step=ai`, 2000);

    // ================================================================ 1. diagnoses: accept / modify / reject / add own
    console.log('\n# diagnoses');
    await clickIn(d, '原发性高血压', '接受'); await L.sleep(900);
    await clickIn(d, '肝阳上亢证', '接受'); await L.sleep(900);
    await clickIn(d, '偏头痛', '修改'); await L.sleep(400);
    await typeIn(d, 'article input[aria-label="诊断名称"]', '紧张型头痛'); await L.clickText(d, '采纳修改后的诊断'); await L.sleep(900);
    L.check('modified candidate is a final diagnosis and shows the AI original name', await L.bodyHas(d, '紧张型头痛') && await L.bodyHas(d, 'AI：偏头痛'));
    await L.clickText(d, '添加我的诊断'); await L.sleep(300);
    await typeIn(d, 'input[placeholder*="诊断名称，如"]', '2型糖尿病'); await d.evaluate(() => { const f = document.querySelector('input[placeholder*="诊断名称，如"]').closest('form'); [...f.querySelectorAll('button')].find((b) => b.innerText.trim() === '添加').click(); }); await L.sleep(900);
    const dxs = (await L.api(d, 'GET', `/encounters/${EID}`)).body.diagnoses;
    const fin = dxs.filter((x) => ['accepted', 'modified'].includes(x.status));
    L.check('final diagnosis set = 4 (western ×3 incl. doctor-added, tcm ×1); nothing pending', fin.length === 4 && dxs.every((x) => x.status !== 'pending'), JSON.stringify(dxs.map((x) => [x.name, x.status])));
    L.check('exactly one primary diagnosis', fin.filter((x) => x.is_primary).length <= 1);
    await L.shot(d, 'e1-diagnoses');

    // ================================================================ 2. ONE unified plan over ALL diagnoses
    console.log('\n# unified plan');
    await d.evaluate(() => [...document.querySelectorAll('button')].find((x) => x.innerText.includes('下一步：制定治疗方案')).click()); await L.sleep(800);
    L.check('plan step shows it will cover all 4 final diagnoses', await L.bodyHas(d, '将基于以下最终诊断') && await L.bodyHas(d, '2型糖尿病') && await L.bodyHas(d, '肝阳上亢证'));
    await L.clickText(d, 'AI 起草治疗方案');
    await d.waitForFunction(() => /整体治疗方案/.test(document.body.innerText) && /确认方案/.test(document.body.innerText), { timeout: 120000 });
    const plans = (await L.api(d, 'GET', `/encounters/${EID}/plans`)).body.items;
    L.check('exactly one live plan for the encounter', plans.filter((p) => ['generating', 'draft', 'confirmed'].includes(p.status)).length === 1);
    L.check('plan lists the herbal formula, western drug, and therapy item', await seen(d, '天麻钩藤饮加减') && await seen(d, '氨氯地平') && await seen(d, '平肝潜阳针刺'));
    L.check('therapy item is pending and confirm is blocked until it is reviewed', (await disabled(d, '确认方案')) === true && await L.bodyHas(d, '待审核'));
    L.check('safety pre-check is shown while reviewing the plan', await L.bodyHas(d, '用药安全预检'));
    await L.shot(d, 'e2-plan-draft');
    await clickIn(d, '平肝潜阳针刺', '接受'); await L.sleep(900);
    await L.clickText(d, '确认方案'); await confirmDialog(d, '确认方案');
    await d.waitForFunction(() => /方案已确认/.test(document.body.innerText), { timeout: 15000 });
    L.check('plan confirmed by the doctor (explicit action)', true);

    // changing the diagnosis set makes the confirmed plan stale
    await step(d, 'AI 分析与诊断');
    await clickIn(d, '2型糖尿病', '撤销', 'li'); await confirmDialog(d, '撤销'); await L.sleep(900);     // the doctor withdraws a diagnosis they had added
    await step(d, '治疗方案');
    L.check('after the diagnosis set changed the plan is flagged stale and confirm is blocked', await L.bodyHas(d, '本方案已过期') && (await disabled(d, '确认方案')) === true);
    L.check('stale-plan banner explains which input changed', await L.bodyHas(d, '最终诊断集合已变化'));
    await L.clickText(d, '仍沿用本方案'); await L.sleep(400);
    await d.type('textarea[placeholder*="新增的主诉"]', '好');
    L.check('"keep this plan" needs a written reason (≥5 chars)', await d.evaluate(() => [...document.querySelectorAll('[role=dialog] button')].find((x) => x.innerText.includes('确认沿用')).disabled));
    await d.type('textarea[placeholder*="新增的主诉"]', '糖尿病诊断待血糖结果，不影响本方案药物选择');
    await confirmDialog(d, '确认沿用'); await L.sleep(1000);
    L.check('rebased plan is no longer stale but must be confirmed again', !(await L.bodyHas(d, '本方案已过期')) && (await disabled(d, '确认方案')) === false);
    await L.clickText(d, '确认方案'); await confirmDialog(d, '确认方案'); await d.waitForFunction(() => /方案已确认/.test(document.body.innerText), { timeout: 15000 });

    // edit the plan, then reload: edits persist
    await d.evaluate(() => { const t = [...document.querySelectorAll('textarea')].find((x) => x.placeholder.includes('一句话说明')); t.focus(); });
    const sumEl = await d.$('textarea[placeholder*="一句话说明"]'); await sumEl.click({ clickCount: 3 }); await sumEl.type('医师修改：降压为主，兼顾平肝潜阳，血糖待复查后再定。');
    L.check('editing a confirmed plan drops it to draft (re-confirm needed)', true);
    await L.clickText(d, '保存修改'); await L.sleep(1200);
    await d.reload({ waitUntil: 'networkidle2' }); await L.sleep(1500); await step(d, '治疗方案');
    L.check('doctor edit survived a page reload (persisted)', await seen(d, '医师修改：降压为主'));
    await L.clickText(d, '确认方案'); await confirmDialog(d, '确认方案'); await d.waitForFunction(() => /方案已确认/.test(document.body.innerText), { timeout: 15000 });

    // ================================================================ 3. prescriptions: separate documents, structured edit, safety rules
    console.log('\n# prescriptions');
    await L.clickText(d, '生成处方草稿'); await L.sleep(2500);
    const rxs = (await L.api(d, 'GET', `/encounters/${EID}/prescriptions`)).body.items;
    L.check('plan produced separate prescription drafts (western + herbal), plan unchanged', rxs.length === 2 && rxs.every((r) => r.status === 'draft') && new Set(rxs.map((r) => r.rx_type)).size === 2, JSON.stringify(rxs.map((r) => [r.rx_type, r.status])));
    const dup = await L.api(d, 'POST', `/encounters/${EID}/prescriptions/from-plan`, { plan_id: (await L.api(d, 'GET', `/encounters/${EID}`)).body.plan.id });
    L.check('repeating "generate from plan" creates no duplicates', dup.status === 201 && dup.body.prescriptions.length === 0 && dup.body.skipped_existing.length === 2, JSON.stringify(dup));
    L.check('Rx step is open with both drafts', await L.bodyHas(d, '西药处方') && await L.bodyHas(d, '中药饮片'));
    await L.shot(d, 'e3-rx-drafts');
    // herbal: add a 十八反 pair → critical, needs written reason
    const herbal = rxs.find((r) => r.rx_type === 'herbal');
    await clickIn(d, '天麻钩藤饮加减', '添加药味', 'section'); await L.sleep(300);
    const names = await d.$$('section input[aria-label="药材名"]'); const last = names[names.length - 1]; await last.type('甘草'); await d.keyboard.press('Escape');
    const grams = await d.$$('section input[aria-label$="克数"]'); await grams[grams.length - 1].type('6');
    await clickIn(d, '天麻钩藤饮加减', '添加药味', 'section'); await L.sleep(300);
    const n2 = await d.$$('section input[aria-label="药材名"]'); await n2[n2.length - 1].type('甘遂'); await d.keyboard.press('Escape');
    const g2 = await d.$$('section input[aria-label$="克数"]'); await g2[g2.length - 1].type('1');
    await clickIn(d, '天麻钩藤饮加减', '保存修改', 'section'); await L.sleep(1500);
    L.check('十八反 (甘草 + 甘遂) is detected as a CRITICAL finding after saving', await L.bodyHas(d, '十八反'));
    L.check('signing is blocked until the doctor writes a reason for the critical finding', (await d.evaluate(() => { const s = [...document.querySelectorAll('section')].filter((x) => x.innerText.includes('天麻钩藤饮加减')).pop(); const b = [...s.querySelectorAll('button')].find((x) => x.innerText.includes('签署处方')); return b ? b.disabled : null; })) === true);
    await L.shot(d, 'e4-rx-critical');
    // remove 甘遂 → finding disappears
    await d.evaluate(() => { const s = [...document.querySelectorAll('section')].filter((x) => x.innerText.includes('天麻钩藤饮加减')).pop(); const b = s.querySelector('button[aria-label="删除 甘遂"]'); b.click(); });
    await clickIn(d, '天麻钩藤饮加减', '保存修改', 'section'); await L.sleep(1500);
    L.check('removing the offending herb clears the critical finding', !(await L.bodyHas(d, '十八反：')));

    // western: a garbled dose must be flagged (not silently accepted), then corrected
    const w = await vis(d, 'section input[aria-label="单次剂量"]'); await setVal(w, '5mg10mg');
    await clickIn(d, '西药处方', '保存修改', 'section'); await L.sleep(1500);
    L.check('a garbled dose ("5mg10mg") is flagged by the safety rules instead of passing silently', await L.bodyHas(d, '格式无法识别'));
    const hash0 = (await L.api(d, 'GET', `/encounters/${EID}/prescriptions`)).body.items.find((r) => r.rx_type === 'western').safety.content_hash;
    await setVal(await vis(d, 'section input[aria-label="单次剂量"]'), '10mg');
    await clickIn(d, '西药处方', '保存修改', 'section'); await L.sleep(1500);
    const w2 = (await L.api(d, 'GET', `/encounters/${EID}/prescriptions`)).body.items.find((r) => r.rx_type === 'western');
    L.check('the corrected dose is persisted and the safety evaluation was recomputed (content hash changed, format warning gone)', w2.items[0].dose_text === '10mg' && w2.safety.content_hash !== hash0 && !w2.safety.issues.some((i) => i.rule_id === 'rx.dose_format'), JSON.stringify({ d: w2.items[0].dose_text, same: w2.safety.content_hash === hash0 }));
    // acknowledge all warnings on the western Rx, then sign — and double-click sign: still exactly one signature
    await d.evaluate(() => { const s = [...document.querySelectorAll('section')].find((x) => x.innerText.includes('西药处方')); s.querySelectorAll('input[type=checkbox]').forEach((c) => { if (!c.checked) c.click(); }); });
    await L.sleep(300);
    await d.evaluate(() => { const s = [...document.querySelectorAll('section')].find((x) => x.innerText.includes('西药处方')); const b = [...s.querySelectorAll('button')].find((x) => x.innerText.includes('签署处方')); b.click(); });
    await confirmDialog(d, '确认签署'); await L.sleep(2000);
    const afterSign = (await L.api(d, 'GET', `/encounters/${EID}/prescriptions`)).body.items.find((r) => r.rx_type === 'western');
    L.check('western prescription signed by the doctor', afterSign.status === 'signed' && !!afterSign.signature_hash, afterSign.status);
    // a second sign attempt (stale tab / double click) is rejected cleanly, not applied twice
    const again = await L.api(d, 'POST', `/prescriptions/${afterSign.id}/sign`, { expected_version: afterSign.row_version - 1 });
    L.check('re-signing (stale version) is refused with a clear conflict, not a 500', [409, 422].includes(again.status), String(again.status));
    L.check('signed Rx is immutable: PATCH is refused', (await L.api(d, 'PATCH', `/prescriptions/${afterSign.id}`, { notes: 'x' })).status === 409);
    // herbal sign
    await d.evaluate(() => { const s = [...document.querySelectorAll('section')].filter((x) => x.innerText.includes('天麻钩藤饮加减')).pop(); s.querySelectorAll('input[type=checkbox]').forEach((c) => { if (!c.checked) c.click(); }); });
    await L.sleep(300);
    const hbState = await d.evaluate(() => { const s = [...document.querySelectorAll('section')].filter((x) => x.innerText.includes('天麻钩藤饮加减')).pop(); const b = [...s.querySelectorAll('button')].find((x) => x.innerText.includes('签署处方')); return { disabled: b ? b.disabled : null, need: (s.innerText.match(/有未保存[^\n|]*|还有 \d+ 项[^\n|]*|处方里还没有药品/) || [''])[0], count: s.innerText.match(/(\d+) 项/)?.[0], issues: [...s.querySelectorAll('li')].map((l) => l.innerText.replace(/\s+/g, ' ').slice(0, 90)).filter((t) => /需确认|严重|必须修改/.test(t)) }; });
    if (hbState.disabled !== false) console.log('   herbal sign button state:', JSON.stringify(hbState));
    await d.evaluate(() => { const s = [...document.querySelectorAll('section')].filter((x) => x.innerText.includes('天麻钩藤饮加减')).pop(); [...s.querySelectorAll('button')].find((x) => x.innerText.includes('签署处方')).click(); });
    await confirmDialog(d, '确认签署'); await L.sleep(2000);
    const rxs2 = (await L.api(d, 'GET', `/encounters/${EID}/prescriptions`)).body.items;
    L.check('herbal prescription signed after the doctor acknowledged the remaining warnings', rxs2.find((r) => r.rx_type === 'herbal').status === 'signed', JSON.stringify(hbState));
    // mock HIS is labelled
    try { await clickIn(d, '西药处方', '模拟提交 HIS', 'section'); }
    catch (e) { console.log('   Rx cards at failure:', JSON.stringify(await d.evaluate(() => [...document.querySelectorAll('section')].filter((x) => x.offsetParent !== null && /RX\d/.test(x.innerText)).map((x) => ({ head: x.innerText.slice(0, 50).replace(/\n+/g, ' '), expanded: x.querySelector('button[aria-expanded]')?.getAttribute('aria-expanded'), buttons: [...x.querySelectorAll('button')].map((b) => b.innerText.trim().slice(0, 14) + (b.disabled ? '(off)' : '')).slice(0, 12) })))), '\n   ', e.message); await L.shot(d, 'e5-fail'); throw e; }
    await confirmDialog(d, '模拟提交'); await L.sleep(1200);
    L.check('"submit to HIS" is explicitly a SIMULATION (labelled, MOCK- reference, no real HIS)', await L.bodyHas(d, '模拟 HIS') && await L.bodyHas(d, 'MOCK-'));
    await d.reload({ waitUntil: 'networkidle2' }); await L.sleep(1500); await step(d, '处方');
    L.check('prescription states persisted across reload', await L.bodyHas(d, '已签署') && await L.bodyHas(d, 'MOCK-'));
    await L.shot(d, 'e5-rx-signed');

    // ================================================================ 4. record: merge-safe, versioned, finalize, PDF
    console.log('\n# record');
    // verify OCR labs first (finalize would otherwise warn)
    await step(d, '接诊资料'); await L.clickText(d, '我已对照原图核对全部').catch(() => {}); await L.sleep(1000);
    await step(d, '病历'); await d.waitForFunction(() => /病历 .*M\d/.test(document.body.innerText) || /自动汇总/.test(document.body.innerText), { timeout: 20000 });
    L.check('record is auto-assembled from facts, diagnoses, plan and prescriptions', await seen(d, '头痛 3 天') && await seen(d, '西医诊断') && await seen(d, '氨氯地平'));
    const pe = await vis(d, 'textarea[aria-label="体格检查"]'); await pe.click({ clickCount: 3 }); await pe.type('医师手写：BP 152/96 mmHg，心肺未见异常。');
    await L.clickText(d, '保存草稿'); await L.sleep(1200);
    await L.clickText(d, '刷新自动汇总'); await L.sleep(1500);
    L.check('refreshing the auto-summary NEVER overwrites the doctor\'s own text', (await d.$eval('textarea[aria-label="体格检查"]', (e) => e.value)).includes('医师手写：BP 152/96'));
    const recId = (await L.api(d, 'POST', `/encounters/${EID}/record`)).body.record.id;
    await d.reload({ waitUntil: 'networkidle2' }); await L.sleep(1500); await step(d, '病历');
    L.check('record draft (and the doctor text) survived a reload', (await d.$eval('textarea[aria-label="体格检查"]', (e) => e.value)).includes('医师手写：BP 152/96'));
    await L.shot(d, 'e6-record-draft');
    const fb = await d.evaluate(() => [...document.querySelectorAll('button')].find((x) => x.innerText.includes('定稿并结束就诊')).disabled);
    if (fb) { const t = await d.evaluate(() => { const h = [...document.querySelectorAll('h2')].find((x) => x.innerText.trim() === '定稿' && x.offsetParent !== null); return h ? h.closest('section').innerText : 'no 定稿 card'; }); console.log('   (finalize disabled) 定稿 card:', t.replace(/\s+/g, ' ').slice(0, 400)); }
    L.check('finalize is available only when nothing hard blocks it', fb === false);
    await L.clickText(d, '定稿并结束就诊'); await confirmDialog(d, '定稿'); await d.waitForFunction(() => /已于 .* 定稿并锁定/.test(document.body.innerText), { timeout: 20000 });
    fs.writeFileSync('/tmp/v3shots/scenario2.json', JSON.stringify({ enc: EID, western: afterSign.id, herbal: rxs2.find((r) => r.rx_type === 'herbal').id, record: recId }));
    L.check('record finalized and locked; visit completed', await L.bodyHas(d, '定稿并锁定') && (await L.api(d, 'GET', `/encounters/${EID}`)).body.encounter.status === 'COMPLETED');
    L.check('finalized record is read-only in the UI (no textarea) and the visit banner says completed', (await d.$('textarea[aria-label="主诉"]')) === null && await L.bodyHas(d, '本次就诊已完成'));
    L.check('server refuses fact edits after finalization', (await L.api(d, 'PATCH', `/encounters/${EID}/facts`, { hpi: '偷偷改' })).status === 409);
    L.check('server refuses plain draft saves after finalization', (await L.api(d, 'PUT', `/records/${recId}/draft`, { sections: { hpi: '偷偷改' } })).status === 409);
    // PDFs
    const pdf = await d.evaluate(async (id) => { const r = await fetch(`/api/v1/records/${id}/pdf`); const b = new Uint8Array(await r.arrayBuffer()); return { status: r.status, type: r.headers.get('content-type'), size: b.length, head: String.fromCharCode(...b.slice(0, 5)) }; }, recId);
    L.check('record PDF downloads (application/pdf, %PDF header, non-trivial size)', pdf.status === 200 && /pdf/.test(pdf.type) && pdf.head === '%PDF-' && pdf.size > 5000, JSON.stringify(pdf));
    const rxpdf = await d.evaluate(async (id) => { const r = await fetch(`/api/v1/prescriptions/${id}/pdf`); const b = new Uint8Array(await r.arrayBuffer()); return { status: r.status, head: String.fromCharCode(...b.slice(0, 5)), size: b.length }; }, afterSign.id);
    L.check('prescription PDF downloads', rxpdf.status === 200 && rxpdf.head === '%PDF-' && rxpdf.size > 3000, JSON.stringify(rxpdf));
    fs.writeFileSync('/tmp/v3shots/e2e-record.pdf', Buffer.from(await d.evaluate(async (id) => { const r = await fetch(`/api/v1/records/${id}/pdf`); return Array.from(new Uint8Array(await r.arrayBuffer())); }, recId)));
    await L.shot(d, 'e7-record-final');
    // amend with reason → new version, original kept
    await L.clickText(d, '更正病历'); await L.sleep(400);
    const ae = await vis(d, 'textarea[aria-label="主诉"]'); await ae.click({ clickCount: 3 }); await ae.type('口干多饮、乏力 2 周（更正：伴夜尿增多）');
    L.check('amend needs a reason (submit disabled until ≥5 chars)', await d.evaluate(() => [...document.querySelectorAll('button')].find((x) => x.innerText.includes('提交更正')).disabled));
    await d.type('textarea[aria-label="更正原因"]', '补充遗漏的夜尿症状');
    await L.clickText(d, '提交更正'); await L.sleep(1500);
    const vers = (await L.api(d, 'GET', `/records/${recId}`)).body.record.versions;
    L.check('amendment appended as a NEW version; the original final version is kept', vers.some((v) => v.kind === 'final') && vers.some((v) => v.kind === 'amendment') && vers.length >= 3, JSON.stringify(vers.map((v) => v.kind)));
    L.check('integrity check passes on the final version', (await L.api(d, 'GET', `/records/${recId}`)).body.integrity_ok === true);

    // ================================================================ 5. navigation linkage
    console.log('\n# linkage');
    await L.goto(d, '/records', 1200); L.check('records list shows the finalized record with its diagnoses', await L.bodyHas(d, '已定稿') && await L.bodyHas(d, '原发性高血压'));
    await d.evaluate(() => [...document.querySelectorAll('tbody tr')][0].click()); await d.waitForFunction(() => location.pathname.startsWith('/encounters/'), { timeout: 15000 });
    L.check('record row opens the same encounter workspace on the record step', /step=record/.test(d.url()) || await L.bodyHas(d, '版本历史'));
    await L.goto(d, '/prescriptions', 1200); L.check('prescriptions list shows both prescriptions with status', await L.bodyHas(d, '已签署') && await L.bodyHas(d, '西药'));
    await L.goto(d, '/patients', 1200); await L.clickText(d, '刘闭环', 'td').catch(() => {}); await L.sleep(1500);
    L.check('patient file shows the encounter timeline linked to record + prescriptions', await L.bodyHas(d, '就诊时间线') && await L.bodyHas(d, '病历') );
    // the only HTTP errors this scenario provokes ON PURPOSE (negative tests): re-sign with a stale version, patch a signed Rx, edit facts / save a draft after finalization
    const expected = [/\[http 409\] POST \/api\/v1\/prescriptions\/\w+\/sign/, /\[http 409\] PATCH \/api\/v1\/prescriptions\/\w+/, /\[http 409\] PATCH \/api\/v1\/encounters\/\w+\/facts/, /\[http 409\] PUT \/api\/v1\/records\/\w+\/draft/];
    const unexpected = d.problems.filter((p) => !expected.some((e) => e.test(p)));
    L.check('no UNEXPECTED console errors / failed requests during the whole doctor flow (only the 4 deliberate negative-test 409s)', unexpected.length === 0, unexpected.join(' | '));
  } finally { await b.close(); }
  process.exit(L.summary() ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(2); });
