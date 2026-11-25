// Scenario 1/3/7 — patient self-intake on a phone-sized browser → async AI pre-analysis → the doctor finds the saved result.
// Runs against the fake-AI stack (API 8012 / web 3101) so it is deterministic; asset files are the real audio / lab report / tongue photo.
const L = require('./lib');
const path = require('path');
const ROOT = path.resolve(__dirname, '../../..');   // HisCopilot/
const AUDIO = path.join(ROOT, 'test/番禺路723号.m4a'), LAB = path.join(ROOT, 'test/微信图片_20261013234121_69_2.jpg'), TONGUE = path.join(ROOT, 'instruct/tongue.jpg');

async function field(page, label) {   // input/textarea that follows a <label> containing `label`
  const h = await page.evaluateHandle((label) => { const l = [...document.querySelectorAll('label')].find((x) => x.innerText.replace(/\s+/g, '').includes(label)); return l ? l.parentElement.querySelector('input:not([type=checkbox]),textarea') : null; }, label);
  const el = h.asElement(); if (!el) throw new Error('no field ' + label); return el;
}
async function fill(page, label, v) { const el = await field(page, label); await el.click({ clickCount: 3 }); await el.type(v); }
const next = (page) => L.clickText(page, '下一步');

(async () => {
  const b = await L.launch(); const out = { ok: true };
  try {
    // ---------------------------------------------------------------- doctor creates the QR link
    const doc = await L.newPage(b, { allow: [/\/auth\/me/] });
    await L.login(doc, 'dr-001', 'Demo12345');
    const link = (await L.api(doc, 'POST', '/intake-links', { hours: 2 })).body;
    L.check('doctor can create an intake link (QR payload has token only, no PHI)', /\/p\/intake#t=/.test(link.url) && !/[一-龥]/.test(link.url));
    const url = link.url.replace(/^https?:\/\/[^/]+/, process.env.WEB || 'http://127.0.0.1:3101');

    // ---------------------------------------------------------------- patient on a phone
    const ph = await L.newPage(b, { width: 390, height: 844, mobile: true, allow: [/\/auth\/me/, /Failed to load resource.*(404|401)/] });
    await ph.goto(url, { waitUntil: 'networkidle2', timeout: 90000 }); await L.sleep(1500);
    L.check('token is removed from the address bar after load', !ph.url().includes('#t='), ph.url());
    L.check('intake page greets with clinic + doctor', await L.bodyHas(ph, '就诊前问诊') && await L.bodyHas(ph, '接诊医生'));
    await L.shot(ph, 'p1-step1');

    await next(ph); await L.sleep(300);
    L.check('step 1 validates the required name', await L.bodyHas(ph, '请填写姓名'));
    await fill(ph, '姓名', '陈测试'); await L.clickText(ph, '男'); await fill(ph, '年龄', '52'); await fill(ph, '手机号', '13900001234');
    await next(ph); await L.sleep(300);
    await next(ph); await L.sleep(300);
    L.check('step 2 validates the chief complaint', await L.bodyHas(ph, '请简单写一下哪里不舒服'));
    await fill(ph, '主要不适', '口干多饮、乏力 2 周'); await fill(ph, '详细经过', '近两周明显口渴，每天饮水约三升，夜尿增多，体重下降约两公斤。');
    const chip = await ph.$('input[placeholder*="添加"]'); await chip.type('视物模糊'); await ph.keyboard.press('Enter');
    await L.shot(ph, 'p2-step2');
    await next(ph); await L.sleep(300);
    await next(ph); await L.sleep(300);
    L.check('step 3 will not let the patient skip the allergy question', await L.bodyHas(ph, '请填写过敏史'));
    const al = await ph.$('input[placeholder*="过敏原"]'); await al.type('青霉素'); await ph.keyboard.press('Enter');
    await L.shot(ph, 'p3-step3'); await next(ph); await L.sleep(500);

    // ---- uploads: live MediaRecorder path (fake mic) AND file path
    L.check('voice capture offers real recording on a secure context', await L.bodyHas(ph, '语音录入'));
    await L.clickText(ph, '语音录入'); await L.sleep(2600); await L.clickText(ph, '停止并识别');
    await ph.waitForFunction(() => /转成文字|已识别/.test(document.body.innerText), { timeout: 30000 });
    await ph.waitForFunction(() => /语音转文字内容|已识别/.test(document.body.innerText) && !/正在转成文字/.test(document.body.innerText), { timeout: 60000 });
    L.check('recorded voice was transcribed and shown for correction', (await ph.$('textarea[aria-label="语音转文字内容"]')) !== null);
    const audioInput = await ph.$('input[type=file][accept="audio/*"]'); await audioInput.uploadFile(AUDIO);
    await ph.waitForFunction(() => document.querySelectorAll('textarea[aria-label="语音转文字内容"]').length >= 2, { timeout: 60000 });
    L.check('uploading the real m4a also works (2 voice items)', true);
    const imgs = await ph.$$('input[type=file][accept^="image"]'); await imgs[0].uploadFile(LAB);
    await ph.waitForFunction(() => /已识别 \d+ 项/.test(document.body.innerText), { timeout: 90000 });
    L.check('lab report OCR result count is shown to the patient', true);
    await imgs[1].uploadFile(TONGUE); await ph.waitForFunction(() => /已收到/.test(document.body.innerText), { timeout: 30000 });
    L.check('tongue photo accepted', true);
    await L.shot(ph, 'p4-uploads');

    // ---- persistence: reload mid-way, nothing lost
    await ph.reload({ waitUntil: 'networkidle2' }); await L.sleep(1500);
    L.check('after a reload the draft (step, fields, uploaded files) is restored', await L.bodyHas(ph, '上传资料') && (await ph.$$('textarea[aria-label="语音转文字内容"]')).length >= 2);
    await next(ph); await L.sleep(400);
    L.check('review shows what was typed', await L.bodyHas(ph, '陈测试') && await L.bodyHas(ph, '口干多饮') && await L.bodyHas(ph, '青霉素') && await L.bodyHas(ph, '2 段录音'));
    await L.clickText(ph, '提交给医生'); await L.sleep(500);
    L.check('submission is blocked until consent is ticked', await L.bodyHas(ph, '请先勾选同意'));
    await ph.click('input[type=checkbox]');
    // double-tap the submit button: must create exactly ONE encounter
    await ph.evaluate(() => { const b = [...document.querySelectorAll('button')].find((x) => x.innerText.includes('提交给医生')); b.click(); b.click(); });
    await ph.waitForFunction(() => /提交成功/.test(document.body.innerText), { timeout: 60000 });
    const encNo = await ph.evaluate(() => (document.body.innerText.match(/E\d{6,}\w*|ENC[-\w]+|\d{4}-?\d{4,}/) || [''])[0]);
    L.check('patient sees a success page with a visit number', await L.bodyHas(ph, '就诊号'), await ph.evaluate(() => document.body.innerText.slice(0, 200)));
    await L.shot(ph, 'p5-done');
    await ph.reload({ waitUntil: 'networkidle2' }); await L.sleep(1200);
    L.check('reloading after submit still shows the submitted state (not an empty form)', await L.bodyHas(ph, '提交成功'));

    // ---------------------------------------------------------------- doctor: the saved result
    await L.sleep(1500);
    const list = (await L.api(doc, 'GET', '/encounters?scope=open&limit=50&q=陈测试')).body;
    L.check('exactly one encounter was created despite the double tap', list.items.length === 1, JSON.stringify(list.items.map((e) => e.encounter_no)));
    const enc = list.items[0]; out.enc = enc.id; out.encNo = enc.encounter_no;
    await L.goto(doc, '/encounters', 1500);
    L.check('queue lists the patient with the "患者自填" tag', await L.bodyHas(doc, '陈测试') && await L.bodyHas(doc, '患者自填'));
    await L.shot(doc, 'd1-queue');
    await L.goto(doc, `/encounters/${enc.id}`, 1500);
    L.check('workspace facts are prefilled from the patient submission', (await (await field(doc, '主诉')).evaluate((e) => e.value)).includes('口干多饮'));
    L.check('allergy is visible in the always-on patient header', await L.bodyHas(doc, '青霉素'));
    L.check('patient-reported original text is kept for reference', await L.bodyHas(doc, '患者自述原文'));
    L.check('voice items + lab + tongue attached to the encounter', await L.bodyHas(doc, '语音记录') && (await doc.$$('textarea[aria-label="语音转写文本"]')).length >= 2);
    await L.shot(doc, 'd2-workspace-facts');
    await L.clickText(doc, 'AI 分析与诊断'); await L.sleep(500);
    await doc.waitForFunction(() => /候选诊断|已用时|AI 预分析失败|西医诊断候选/.test(document.body.innerText), { timeout: 60000 });
    await doc.waitForFunction(() => /西医诊断候选/.test(document.body.innerText) && /接受/.test(document.body.innerText), { timeout: 120000 });
    L.check('async pre-analysis result was saved and is shown without the doctor clicking anything', true);
    L.check('uncertainty is explicit: AI self-rated confidence + evidence refs + "先看这个" layer', await L.bodyHas(doc, 'AI 自评置信度') && await L.bodyHas(doc, '先看这个'));
    await L.shot(doc, 'd3-ai');
    // leave + come back: opening the visit must NOT re-run the model
    const before = (await L.api(doc, 'GET', `/encounters/${enc.id}/analysis/history`)).body.items.length;
    await L.goto(doc, `/encounters/${enc.id}?step=ai`, 1500);
    const after = (await L.api(doc, 'GET', `/encounters/${enc.id}/analysis/history`)).body.items.length;
    L.check('re-opening the visit does not trigger a new analysis (saved result is reused)', before === after && before >= 1, `${before} → ${after}`);
    L.check('candidates are still there after navigation', await L.bodyHas(doc, '西医诊断候选'));
    out.problems = { patient: ph.problems, doctor: doc.problems };
    L.check('no console errors / failed requests on patient pages', ph.problems.length === 0, ph.problems.join(' | '));
    L.check('no console errors / failed requests on doctor pages', doc.problems.length === 0, doc.problems.join(' | '));
  } finally { await b.close(); }
  require('fs').writeFileSync('/tmp/v3shots/scenario1.json', JSON.stringify(out));
  process.exit(L.summary() ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(2); });
