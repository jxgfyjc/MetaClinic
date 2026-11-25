// LIVE-MODEL run (no fakes): real ASR + OCR + tongue VLM + 27B/7B analysis + 27B unified plan, driven through the v3 UI/API.
// Judges STRUCTURE and SAFETY properties of the output (valid refs, non-empty candidates, herb count/doses, rules run), not clinical correctness.
// Usage:  WEB=http://127.0.0.1:3100 DOC_USER=dr-001 DOC_PW=... node tests/e2e/live_models.js     (writes /tmp/v3shots/live_report.json)
const L = require('./lib');
const fs = require('fs'), path = require('path');
const ROOT = path.resolve(__dirname, '../../..');
const AUDIO = path.join(ROOT, 'test/番禺路723号.m4a'), LAB = path.join(ROOT, 'test/微信图片_20261013234121_69_2.jpg'), TONGUE = path.join(ROOT, 'instruct/tongue.jpg');
const t0 = Date.now(); const T = (l) => `${((Date.now() - t0) / 1000).toFixed(0)}s`; const rep = { timings: {} };
const mark = (k) => { rep.timings[k] = +((Date.now() - t0) / 1000).toFixed(1); };
const poll = async (d, fn, ms, every = 3000) => { const end = Date.now() + ms; for (;;) { const r = await fn(); if (r) return r; if (Date.now() > end) return null; await L.sleep(every); } };

(async () => {
  const b = await L.launch();
  try {
    const d = await L.newPage(b, { allow: [/auth\/me/] });
    await L.login(d, process.env.DOC_USER || 'dr-001', process.env.DOC_PW || 'Xiangyuan2026');
    const pat = (await L.api(d, 'POST', '/patients', { name: '实模测试' + (Date.now() % 10000), gender: '男', age: 56, phone: '137' + String(Date.now()).slice(-8), allergies: [{ allergen: '青霉素' }] })).body.patient;
    const eid = (await L.api(d, 'POST', '/encounters', { patient_id: pat.id, chief_complaint: '口干多饮、乏力 2 周', hpi: '近两周明显口渴，每日饮水约 3 升，夜尿增多，体重下降约 2 公斤；食欲尚可，大便偏干，睡眠一般。' })).body.encounter.id;
    await L.api(d, 'PATCH', `/encounters/${eid}/facts`, { vitals: { 血压: '148/92', 体温: '36.6' }, tcm_four: { 舌象: '', 脉象: '脉细数' }, symptoms: ['口干', '乏力', '夜尿多'], past_history: '高血压 5 年，口服氨氯地平。', current_meds: [{ name: '氨氯地平', dose: '5mg', frequency: 'qd' }], expected_version: (await L.api(d, 'GET', `/encounters/${eid}`)).body.encounter.row_version });
    await L.goto(d, `/encounters/${eid}`, 2000);
    console.log(`[${T()}] encounter ${eid}`);

    // ---- real ASR / OCR / tongue upload through the UI file inputs
    const aIn = await d.$('input[type=file][accept="audio/*"]'); await aIn.uploadFile(AUDIO);
    const imgs = await d.$$('input[type=file][accept^="image"]'); await imgs[0].uploadFile(LAB); await imgs[1].uploadFile(TONGUE);
    const arts = await poll(d, async () => { const a = (await L.api(d, 'GET', `/encounters/${eid}`)).body.artifacts; return a.length === 3 && a.every((x) => ['succeeded', 'failed', 'not_required'].includes(x.proc_status)) ? a : null; }, 240000);
    mark('uploads_processed'); L.check('all three uploads finished processing (real ASR + OCR)', !!arts, 'timeout');
    const asr = arts.find((x) => x.kind === 'audio'), ocr = arts.find((x) => x.kind === 'lab_image'), tg = arts.find((x) => x.kind === 'tongue_image');
    L.check('real ASR returned Chinese text', asr.proc_status === 'succeeded' && /[一-龥]/.test(asr.result_text || ''), JSON.stringify([asr.proc_status, asr.error_code, asr.error_detail]));
    rep.asr = asr.result_text;
    const labs = (await L.api(d, 'GET', `/encounters/${eid}`)).body.labs;
    L.check('real OCR parsed ≥5 lab rows, all start UNVERIFIED', ocr.proc_status === 'succeeded' && labs.length >= 5 && labs.every((l) => !l.verified), `${ocr.proc_status} ${labs.length}`);
    rep.labs = labs.map((l) => `${l.name} ${l.value}${l.unit} ${l.flag}`);
    L.check('tongue image accepted (analysed together with the AI run)', ['not_required', 'succeeded'].includes(tg.proc_status));
    await L.shot(d, 'live-1-materials');

    // ---- real analysis
    await L.clickText(d, 'AI 分析与诊断'); await L.sleep(500); await L.clickText(d, '开始 AI 预分析');
    console.log(`[${T()}] analysis started (real 27B + 7B + tongue VLM)…`);
    const an = await poll(d, async () => { const a = (await L.api(d, 'GET', `/encounters/${eid}`)).body.analysis; return a && ['succeeded', 'partial', 'failed'].includes(a.status) ? a : null; }, 900000, 5000);
    mark('analysis_done'); L.check('analysis reached a terminal state within 15 min', !!an);
    L.check(`analysis status is succeeded/partial (got ${an && an.status}${an && an.error_detail ? ': ' + an.error_detail.slice(0, 160) : ''})`, an && ['succeeded', 'partial'].includes(an.status));
    const mods = Object.fromEntries((an.modules || []).map((m) => [m.module, m]));
    rep.modules = Object.fromEntries(Object.entries(mods).map(([k, m]) => [k, { status: m.status, ms: m.duration_ms, model: m.model, err: m.error_detail }]));
    const dxs = (await L.api(d, 'GET', `/encounters/${eid}`)).body.diagnoses;
    const west = dxs.filter((x) => x.module === 'western'), tcm = dxs.filter((x) => x.module === 'tcm');
    rep.candidates = dxs.map((x) => `${x.module}:${x.name}(${x.confidence})`);
    L.check('≥2 western differentials and ≥1 TCM syndrome candidates (never a single "certain" diagnosis)', west.length >= 2 && tcm.length >= 1, JSON.stringify(rep.candidates));
    const factIds = new Set((an.facts || []).map((f) => f.id));
    const refs = dxs.flatMap((x) => [...((x.evidence || {}).supporting || []), ...((x.evidence || {}).contradicting || [])].flatMap((c) => c.refs || []));
    L.check('every stored evidence reference points to a real fact (fabricated refs are dropped server-side)', refs.every((r) => factIds.has(r)), JSON.stringify(refs.filter((r) => !factIds.has(r))));
    L.check('tongue findings were used or explicitly reported unusable', !!mods.tongue && ['succeeded', 'failed', 'skipped'].includes(mods.tongue.status));
    L.check('model self-rated confidence is present on candidates', dxs.every((x) => ['low', 'medium', 'high'].includes(x.confidence)));
    L.check('"missing information" is surfaced for at least one candidate or at module level', dxs.some((x) => ((x.evidence || {}).missing_information || []).length) || Object.values(mods).some((m) => ((m.output || {}).missing_information || []).length));
    rep.synthesis = (an.summary || {}).headline;
    await L.goto(d, `/encounters/${eid}?step=ai`, 2500); await L.shot(d, 'live-2-analysis');

    // ---- doctor accepts one western + one tcm candidate (explicit actions), then real unified plan
    const acc = [west[0], tcm[0]];
    for (const x of acc) await L.api(d, 'POST', `/diagnoses/${x.id}/review`, { action: 'accept', expected_version: x.row_version });
    await L.goto(d, `/encounters/${eid}?step=plan`, 2000); await L.clickText(d, 'AI 起草治疗方案');
    console.log(`[${T()}] plan generation started (real 27B)…`);
    const plan = await poll(d, async () => { const p = (await L.api(d, 'GET', `/encounters/${eid}`)).body.plan; return p && ['draft', 'failed'].includes(p.status) ? p : null; }, 900000, 5000);
    mark('plan_done'); L.check(`plan generation finished as draft (got ${plan && plan.status}${plan && plan.error_detail ? ': ' + plan.error_detail.slice(0, 160) : ''})`, plan && plan.status === 'draft');
    const c = plan.content, herbal = c.herbal || [];
    rep.plan = { summary: c.summary, western: (c.medication.western || []).map((m) => `${m.name} ${m.dose_text} ${m.frequency} ${m.days}d [${(m.flags || []).join(',')}]`), herbal: herbal.map((h) => ({ title: h.title, n: h.herbs.length, herbs: h.herbs.map((x) => `${x.name}${x.dose_g}${x.special ? '(' + x.special + ')' : ''}`).join(' ') })), therapy: plan.therapy_items.map((t) => `${t.category}:${t.name} pts=${t.acupoints.join('/')} need=${(t.needs_confirmation || []).join('|')}`) };
    L.check('plan covers the accepted diagnoses (snapshot has both) and is ONE plan', (plan.dx_snapshot || []).length === 2);
    L.check('herbal formula has a plausible size (6–18 herbs) with positive numeric doses', herbal.length >= 1 && herbal.every((h) => h.herbs.length >= 6 && h.herbs.length <= 18 && h.herbs.every((x) => typeof x.dose_g === 'number' && x.dose_g > 0)), JSON.stringify(rep.plan.herbal));
    L.check('no western item has an invented dose: missing fields are flagged, not guessed', (c.medication.western || []).every((m) => (m.dose_text && m.frequency && m.days) || (m.flags || []).some((f) => f.startsWith('missing_'))));
    L.check('therapy items start PENDING for doctor review (never auto-accepted)', plan.therapy_items.every((t) => t.status === 'pending'));
    const pv = (await L.api(d, 'GET', `/plans/${plan.id}/safety-preview`)).body;
    rep.safety_preview = pv.summary; rep.safety_findings = pv.groups.flatMap((g) => g.issues.map((i) => `${g.label}: [${i.severity}] ${i.message}`));
    L.check('rule engine evaluated the real plan (preview returned groups)', pv.groups.length >= 1);
    await L.goto(d, `/encounters/${eid}?step=plan`, 2500); await L.shot(d, 'live-3-plan');
    // the penicillin allergy must be respected: no penicillin-class drug may be silently present without a critical finding
    const penicillinish = (c.medication.western || []).some((m) => /青霉素|阿莫西林|氨苄|哌拉西林/.test(m.name));
    L.check('penicillin-allergic patient: a penicillin-class drug (if the model proposed one) is flagged CRITICAL by the rules', !penicillinish || rep.safety_findings.some((f) => /critical/.test(f) && /过敏|青霉素/.test(f)));
    L.check('no console errors / failed requests during the live run', d.problems.length === 0, d.problems.join(' | ').slice(0, 500));
  } catch (e) { console.error('FATAL', e); rep.fatal = String(e); } finally {
    rep.total_s = +((Date.now() - t0) / 1000).toFixed(0); fs.writeFileSync('/tmp/v3shots/live_report.json', JSON.stringify(rep, null, 2)); await b.close();
  }
  console.log(JSON.stringify(rep, null, 1)); process.exit(L.summary() ? 1 : 0);
})();
