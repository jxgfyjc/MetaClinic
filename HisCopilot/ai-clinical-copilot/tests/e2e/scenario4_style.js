// Scenario 4 — 医师经验画像 in the real browser:
//  A  doctor imports a case table → PII redacted → confirms cases → extraction → reviews entries (evidence) → publishes
//  B  shared master library: declare authorization → admin files it → a different doctor sees it read-only with source + disclaimer
//  C  attending doctor picks profiles for an encounter → the plan shows the four-column comparison → doctor records decisions
const L = require('./lib');
const fs = require('fs');
const os = require('os');
const path = require('path');

const NAMES = ['张子一', '李华二', '王强三', '赵军一', '钱伟二', '孙子三', '周华一', '吴强二', '郑军三', '陈伟一', '张华二', '李强三', '王军一', '赵伟二'];   // 虚构
const GAN = [['天麻', 10], ['钩藤', 15], ['石决明', 30], ['栀子', 9], ['黄芩', 9], ['川牛膝', 12], ['杜仲', 10], ['桑寄生', 15], ['首乌藤', 15], ['茯神', 12], ['甘草', 6]];
const XIN = [['黄芪', 20], ['党参', 15], ['白术', 12], ['茯神', 15], ['酸枣仁', 15], ['远志', 6], ['当归', 10], ['木香', 6], ['甘草', 6]];
function csv() {
  const rows = ['姓名,病历号,日期,主诉,舌脉,证型,中医治法,处方,备注'];
  for (let i = 0; i < 14; i++) {
    const gan = i < 10; const herbs = (gan ? GAN : XIN).map(([n, g]) => `${n}${g + (i % 3 === 0 ? 2 : 0)}g`).join(' ');
    rows.push(`${NAMES[i]},MZ${20230000 + i},2024-0${1 + (i % 9)}-1${i % 9},${gan ? '头晕头痛' : '失眠多梦'},${gan ? '舌红苔薄黄脉弦数' : '舌淡苔薄白脉细弱'},${gan ? '肝阳上亢' : '心脾两虚'},${gan ? '平肝潜阳' : '补益心脾'},"${gan ? '天麻钩藤饮' : '归脾汤'}加减：${herbs}",电话1380013${String(i).padStart(4, '0')}`);
  }
  return rows.join('\n');
}
const CSV_PATH = path.join(os.tmpdir(), 'xy_style_cases.csv');

async function clickInDialog(page, text) {
  await L.sleep(300);
  const ok = await page.evaluate((text) => { const d = [...document.querySelectorAll('[role=dialog]')].pop(); if (!d) return false; const b = [...d.querySelectorAll('button')].reverse().find((x) => x.innerText.replace(/\s+/g, '').includes(text) && !x.disabled); if (!b) return false; b.click(); return true; }, text);
  if (!ok) throw new Error(`no enabled "${text}" button in dialog`);
  await L.sleep(500);
}
async function uploadCsvViaApi(page, pid, text, name = 'cases.csv') {
  return page.evaluate(async (pid, text, name) => {
    const fd = new FormData(); fd.append('files', new File([text], name, { type: 'text/csv' })); fd.append('label', 'e2e');
    const r = await fetch('/api/v1/style/profiles/' + pid + '/imports', { method: 'POST', body: fd }); return { status: r.status, body: await r.json() };
  }, pid, text, name);
}
async function until(fn, ms = 90000, every = 1200) { const t0 = Date.now(); while (Date.now() - t0 < ms) { const v = await fn(); if (v) return v; await L.sleep(every); } throw new Error('timeout waiting for condition'); }
/** a full API-side pipeline for a profile (used to build the shared master quickly; the UI path is exercised in part A) */
async function buildPublished(page, pid, text) {
  const up = await uploadCsvViaApi(page, pid, text);
  const iid = up.body.import.id;
  await until(async () => (await L.api(page, 'GET', `/style/imports/${iid}`)).body.import.status === 'needs_review');
  await L.api(page, 'POST', `/style/imports/${iid}/cases/bulk`, { action: 'confirm', only_clean: false });
  await L.api(page, 'POST', `/style/imports/${iid}/extract`, { mode: 'rules' });
  await until(async () => (await L.api(page, 'GET', `/style/imports/${iid}`)).body.import.status === 'extracted');
  const es = (await L.api(page, 'GET', `/style/profiles/${pid}/entries?version=draft`)).body.entries;
  for (const e of es) if (e.review_status === 'pending') await L.api(page, 'POST', `/style/entries/${e.id}/review`, { action: 'approve', ack_reason: e.flags.some((f) => f.severity === 'critical') ? '测试环境：已知风险，仅演示放行' : '' });
  return (await L.api(page, 'POST', `/style/profiles/${pid}/publish`, {})).status;
}

(async () => {
  fs.writeFileSync(CSV_PATH, csv());
  const b = await L.launch();
  try {
    // ======================================================================== A. 个人画像：导入 → 校验 → 抽取 → 审核 → 发布
    console.log('\n# A. import → validate → extract → review → publish (dr-001, personal profile)');
    const d = await L.newPage(b, { allow: [/\/auth\/me/] }); globalThis.__page = d;
    await L.login(d, 'dr-001', 'Demo12345');
    await L.goto(d, '/style', 2500);
    L.check('sidebar has the 经验画像 entry and the page explains it', await d.evaluate(() => [...document.querySelectorAll('aside a')].some((a) => a.innerText.includes('经验画像'))) && await L.bodyHas(d, '医师经验画像') && await L.bodyHas(d, '不代表该医师本人审核或认可'));
    await L.shot(d, 's1-home');
    await L.clickText(d, '新建画像'); await L.sleep(500);
    L.check('create dialog offers personal profile with the doctor name prefilled', await L.bodyHas(d, '我的个人诊疗习惯') && (await d.$eval('[role=dialog] input.input', (e) => e.value)).length > 0);
    await clickInDialog(d, '创建');
    await d.waitForFunction(() => /^\/style\/sty_/.test(location.pathname), { timeout: 30000 });
    const PID = location_pid(await d.evaluate(() => location.pathname));
    await L.waitText(d, '建立画像的步骤');
    L.check('new profile opens with the guided steps', await L.bodyHas(d, '导入历史病历') && await L.bodyHas(d, '尚未发布'));

    await L.clickText(d, '导入资料'); await L.sleep(600);
    const input = await d.$('input[type=file]'); await input.uploadFile(CSV_PATH); await L.sleep(500);
    L.check('chosen file is listed before upload and the privacy notice is shown', await L.bodyHas(d, 'xy_style_cases.csv') && await L.bodyHas(d, '抽取完成后，原始文件会自动从服务器删除'));
    await L.clickText(d, '开始导入');
    await d.waitForFunction(() => /\/imports\/sti_/.test(location.pathname), { timeout: 60000 });
    await L.waitText(d, '逐份核对病例', 60000);
    L.check('parsing finished: 14 cases, columns recognised, status 待校验', await L.bodyHas(d, '待校验') && await L.bodyHas(d, '列识别') && await L.bodyHas(d, '主诉'));
    const body1 = await d.evaluate(() => document.body.innerText);
    L.check('NO patient name / phone / record number is visible anywhere on the page', !NAMES.some((n) => body1.includes(n)) && !body1.includes('1380013') && !body1.includes('MZ2023'), NAMES.filter((n) => body1.includes(n)).join(','));
    L.check('placeholders and the redaction counters are shown', body1.includes('【姓名】') && body1.includes('已自动遮盖'));
    L.check('parsed herbs + doses are shown for verification', body1.includes('天麻 10g') && body1.includes('系统识别的饮片与克数'));
    L.check('same prescription on different dates is reported as 复诊, not dropped as duplicate', body1.includes('同方复诊'));
    L.check('extraction cannot start before any case is confirmed', await d.evaluate(() => { const b = [...document.querySelectorAll('button')].find((x) => x.innerText.includes('开始抽取')); return !!b && b.disabled; }));
    await L.shot(d, 's2-validate');

    await L.clickText(d, '确认无警告病例'); await L.sleep(1500);
    const IID = await d.evaluate(() => location.pathname.split('/').pop());
    const st = (await L.api(d, 'GET', `/style/imports/${IID}`)).body.import.stats;
    // cases #0 and #9 are the same visit entered twice (same day, identical prescription) → the exact duplicate is auto-excluded, never counted twice
    L.check('bulk-confirm confirmed 13 cases; the exact duplicate was auto-excluded (not silently counted twice)', st.confirmed === 13 && st.excluded === 1 && st.duplicates_exact === 1 && st.pending === 0, JSON.stringify(st));
    await L.clickText(d, '开始抽取'); await L.sleep(800);
    await L.waitText(d, '抽取完成', 120000);
    const done = await d.evaluate(() => document.body.innerText);
    L.check('extraction summary says candidates are NOT effective until reviewed, and sources were deleted', done.includes('候选条目不会自动生效') && done.includes('原始上传文件已从服务器删除'));
    await L.shot(d, 's3-extracted');

    await L.clickText(d, '去审核候选经验', 'a'); await d.waitForFunction(() => /\/style\/sty_[^/]+$/.test(location.pathname), { timeout: 30000 });
    await L.waitText(d, '审核的是什么', 30000);
    await d.waitForFunction(() => document.body.innerText.includes('查看证据'), { timeout: 30000 });      // SWR shows the cached (empty) draft first, then revalidates
    L.check('review tab lists candidates with support counts and the data-is-computed notice', await L.bodyHas(d, '由程序计算') && await L.bodyHas(d, '9/9 例') && await L.bodyHas(d, '证据可信度'));
    L.check('publishing is blocked while candidates are pending', await d.evaluate(() => { const b = [...document.querySelectorAll('button')].find((x) => x.innerText.includes('发布版本')); return !!b && b.disabled; }));
    await clickText2(d, '查看证据'); await L.sleep(1000);
    L.check('evidence dialog shows verbatim quotes', await L.bodyHas(d, '逐字出现') && (await d.$$('[role=dialog] blockquote')).length > 0);
    await d.keyboard.press('Escape'); await L.sleep(400);
    await L.shot(d, 's4-review');
    // approve the well-supported candidates through the UI, the rest through the API (same endpoint the buttons call)
    await L.clickText(d, '批量通过证据充分的候选'); await clickInDialog(d, '批量通过'); await L.sleep(2500);
    const left = (await L.api(d, 'GET', `/style/profiles/${PID}/entries?version=draft`)).body;
    const stillPending = left.entries.filter((x) => x.review_status === 'pending');
    L.check('bulk-approve only took the well-supported entries; thinly-supported ones (4 cases) still need a human decision', stillPending.length > 0 && stillPending.every((x) => x.support_count < 5), JSON.stringify(stillPending.map((x) => [x.title, x.support_count])));
    L.check('publish stays disabled while some candidates are unreviewed', await d.evaluate(() => { const b = [...document.querySelectorAll('button')].find((x) => x.innerText.includes('发布版本')); return !!b && b.disabled; }));
    // the doctor reviews the remaining thin-evidence entries ONE BY ONE through the real UI buttons (the page must refresh its own counters)
    for (let i = 0; i < stillPending.length; i++) {
      await d.evaluate(() => { const b = [...document.querySelectorAll('li button')].find((x) => x.innerText.replace(/\s+/g, '') === '通过' && !x.disabled && x.offsetParent !== null); if (!b) throw new Error('no 通过 button'); b.click(); });
      await L.sleep(1200);
    }
    await d.waitForFunction(() => { const b = [...document.querySelectorAll('button')].find((x) => x.innerText.includes('发布版本')); return !!b && !b.disabled; }, { timeout: 20000 });
    L.check('after the last candidate is approved in the UI, the page refreshes itself and publish becomes available', true);
    await L.clickText(d, '发布版本'); await L.sleep(500);
    L.check('publish dialog explains immutability', await L.bodyHas(d, '版本内容不可再修改'));
    await clickInDialog(d, '发布'); await L.sleep(1500);
    const prof = (await L.api(d, 'GET', `/style/profiles/${PID}`)).body.profile;
    L.check('profile is published as v1 (immutable snapshot hash present)', prof.active_version && prof.active_version.version_no === 1 && prof.active_version.snapshot_hash.length === 64);
    await L.waitText(d, '已发布 v1', 15000);
    await L.clickText(d, '版本'); await L.sleep(700);
    L.check('versions tab shows v1 as 当前使用 with the hash', await L.bodyHas(d, '当前使用') && await L.bodyHas(d, '不可变快照'));
    await L.shot(d, 's5-published');
    L.check('doctor page had no unexpected console/HTTP problems (A)', d.problems.length === 0, d.problems.join(' | '));

    // ======================================================================== B. 共享名医库：声明授权 → 管理员备案 → 其他医师只读查看
    console.log('\n# B. shared master library: authorization → admin files it → another doctor reads it');
    const m = (await L.api(d, 'POST', '/style/profiles', { kind: 'master', subject_name: '周老', subject_title: '国医大师（虚构）', subject_dept: '脑病科', intro: '虚构的名老中医，擅长眩晕与失眠。' })).body.profile;
    const pub = await buildPublished(d, m.id, csv());
    L.check('master library built & published through the API pipeline', pub === 200, String(pub));
    await L.goto(d, `/style/${m.id}#sharing`, 1500);
    L.check('sharing tab explains that nothing is visible to others until a filed authorization exists', await L.bodyHas(d, '管理员已备案') && await L.bodyHas(d, '未共享'));
    const decl = await L.api(d, 'POST', `/style/profiles/${m.id}/authorizations`, { basis_type: 'self', basis_text: '本人书面同意（文件编号 SQ-2026-001）', authorizer_name: '周老', scope: 'hospital' });
    L.check('declaration accepted as pending', decl.status === 201 && decl.body.authorization.status === 'pending');

    const young = await L.newPage(b, { allow: [/\/auth\/me/] }); globalThis.__page = young;
    await L.login(young, 'dr-003', 'Demo12345');
    await L.goto(young, '/style', 2000);
    L.check('before filing, the other doctor does NOT see the master library', !(await L.bodyHas(young, '周老')));

    const adm = await L.newPage(b, { allow: [/\/auth\/me/] });
    await L.login(adm, 'admin', 'Demo12345');
    await L.goto(adm, '/style/admin', 2500);
    L.check('admin sees the pending authorization with source + sidebar badge entry', await L.bodyHas(adm, '周老') && await L.bodyHas(adm, 'SQ-2026-001') && await adm.evaluate(() => [...document.querySelectorAll('aside a')].some((a) => a.innerText.includes('画像授权与外发'))));
    L.check('external-model switch is OFF by default and says how to configure', await L.bodyHas(adm, '未允许（默认）') && await L.bodyHas(adm, 'XY_STYLE_EXT_BASE_URL'));
    await L.shot(adm, 's6-admin');
    await L.clickText(adm, '备案'); await L.sleep(500);
    L.check('filing dialog warns the admin to verify the source', await L.bodyHas(adm, '备案表示您已核查'));
    await clickInDialog(adm, '确认备案'); await L.sleep(1500);
    L.check('pending list is empty after filing', await L.bodyHas(adm, '没有待备案的授权'));
    // the admin can open the profile but NOT review clinical content
    await L.goto(adm, `/style/${m.id}#review`, 2000);
    await adm.waitForFunction(() => document.body.innerText.includes('审核的是什么') || document.body.innerText.includes('只读'), { timeout: 30000 });
    L.check('admin sees the review tab read-only (cannot approve/publish clinical content)', await L.bodyHas(adm, '只读') && await adm.evaluate(() => ![...document.querySelectorAll('button')].some((x) => x.innerText.trim() === '通过')));

    await L.goto(young, '/style', 2000);
    L.check('after filing, the other doctor sees the master card with 可在接诊时选用', await L.bodyHas(young, '周老') && await L.bodyHas(young, '可在接诊时选用'));
    await young.evaluate(() => [...document.querySelectorAll('a')].find((a) => a.innerText.includes('周老')).click());
    await young.waitForFunction(() => /\/style\/sty_/.test(location.pathname), { timeout: 20000 }); await L.waitText(young, '共享授权来源', 30000);
    await young.waitForFunction(() => document.body.innerText.includes('查看证据片段'), { timeout: 30000 });       // entries load after the shell
    const yb = await young.evaluate(() => document.body.innerText);
    L.check('read-only library: authorization source, disclaimer, entries with support counts', yb.includes('SQ-2026-001') && yb.includes('不代表该医师本人审核或认可') && yb.includes('9/9 例') && yb.includes('肝阳上亢'));
    L.check('the viewer has NO management tabs and cannot see drafts', !yb.includes('审核经验条目') && !yb.includes('导入资料') && !yb.includes('共享与授权'));
    await clickText2(young, '查看证据片段'); await L.sleep(1000);
    const ev = await young.evaluate(() => document.querySelector('[role=dialog]')?.innerText || '');
    L.check('viewer sees verbatim evidence quotes but NOT the full case text', ev.includes('逐字出现') && !ev.includes('查看整份病例'));
    await L.shot(young, 's7-library');
    const draftTry = await L.api(young, 'GET', `/style/profiles/${m.id}/entries?version=draft`);
    L.check('API: the viewer is refused the draft version', draftTry.status === 403);

    // ======================================================================== C. 接诊选用 → 方案四栏 → 医师决策
    console.log('\n# C. encounter: pick profiles → plan shows the four-column comparison → doctor decides');
    const pat = (await L.api(d, 'POST', '/patients', { name: '刘画像', gender: '男', age: 55, phone: '13700001234', allergies: [{ allergen: '青霉素' }] })).body.patient;
    const EID = (await L.api(d, 'POST', '/encounters', { patient_id: pat.id, chief_complaint: '头晕头痛 5 天', hpi: '近五天头晕头痛，烦躁易怒，口苦，夜寐不安，晨起明显。' })).body.encounter.id;
    await L.api(d, 'POST', `/encounters/${EID}/analysis`, {});
    await until(async () => { const j = (await L.api(d, 'GET', `/encounters/${EID}`)).body; return j.analysis && ['succeeded', 'partial'].includes(j.analysis.status); }, 120000, 1500);
    const dx = (await L.api(d, 'GET', `/encounters/${EID}`)).body.diagnoses.filter((x) => x.status === 'pending');
    for (const x of dx) await L.api(d, 'POST', `/diagnoses/${x.id}/review`, { action: 'accept' });
    await L.goto(d, `/encounters/${EID}?step=plan`, 2500);
    L.check('plan step offers both profiles (master + own personal) as optional references', await L.bodyHas(d, '参考经验画像（可选）') && await L.bodyHas(d, '周老') && await L.bodyHas(d, '名医经验库') && await L.bodyHas(d, '个人习惯'));
    L.check('picker states that safety and current facts take priority over history', await L.bodyHas(d, '患者当前事实与安全规则始终优先'));
    const boxes = await d.$$('input[type=checkbox]');
    const picks = await d.evaluate(() => [...document.querySelectorAll('label')].filter((l) => l.innerText.includes('名医经验库') || l.innerText.includes('个人习惯')).map((l) => l.querySelector('input').id || ''));
    await d.evaluate(() => { for (const l of [...document.querySelectorAll('label')].filter((l) => l.innerText.includes('名医经验库') || l.innerText.includes('个人习惯'))) l.querySelector('input').click(); });
    await until(async () => (await L.api(d, 'GET', `/style/encounters/${EID}/profiles`)).body.chosen.length === 2, 15000, 500);
    const chosen = (await L.api(d, 'GET', `/style/encounters/${EID}/profiles`)).body.chosen;
    L.check('both profiles were attached to THIS encounter only', chosen.length === 2, JSON.stringify(chosen));
    await L.clickText(d, 'AI 起草治疗方案');
    await d.waitForFunction(() => document.body.innerText.includes('经验参考对照'), { timeout: 150000 });
    await L.sleep(800);
    const pb = await d.evaluate(() => document.body.innerText);
    L.check('four columns are present, in order', ['名医经验参考', '当前患者分析', '个性化建议与异同', '医师最终决策'].every((t) => pb.includes(t)));
    L.check('column ① shows entries from BOTH layers with their source note', pb.includes('名医经验库') && pb.includes('个人习惯') && pb.includes('SQ-2026-001'));
    L.check('column ② shows a verdict and column ③ a stance', /符合|部分符合|不符合|无法判断/.test(pb) && /采纳|调整后采纳|未采纳|仅供参考/.test(pb));
    L.check('disclaimer is shown on the comparison', pb.includes('不代表该医师本人审核或认可'));
    // the plan editor keeps its values in <input>/<textarea>, which innerText does not include → read form values too
    const formText = await d.evaluate(() => [...document.querySelectorAll('input,textarea')].map((e) => e.value).join(' '));
    L.check('the normal plan (herbal formula, medication, safety pre-check) is still produced', (pb + ' ' + formText).includes('天麻钩藤饮加减') && (pb + ' ' + formText).includes('氨氯地平') && pb.includes('用药安全预检'));
    L.check('nothing is decided for the doctor: 0 rows processed at first', /您已处理\s*0\s*\//.test(pb));
    const fchip = await d.evaluate(() => { const b = [...document.querySelectorAll('section button.kbd')].find((x) => /^F\d+$/.test(x.innerText.trim())); if (!b) return null; b.click(); return b.innerText.trim(); });
    await L.sleep(400);
    L.check('clicking an F-reference opens the original patient fact stored in the snapshot', !!fchip && await L.bodyHas(d, fchip) && await d.evaluate(() => document.body.innerText.includes('患者自述') || document.body.innerText.includes('医师录入')));
    await L.shot(d, 's8-plan-four-columns');
    await clickText2(d, '采纳'); await L.sleep(1500);
    L.check('doctor decision is recorded and counted', await d.evaluate(() => /您已处理\s*1\s*\//.test(document.body.innerText)) && await L.bodyHas(d, '已采纳'));
    const listed = (await L.api(d, 'GET', `/encounters/${EID}/plans`)).body.items.find((p) => p.status === 'draft');
    const planNow = (await L.api(d, 'GET', `/plans/${listed.id}`)).body.plan;           // the list endpoint is a summary; the snapshot lives on the detail
    L.check('decision is persisted in the plan snapshot (and audited server-side)', planNow && planNow.style && Object.keys(planNow.style.decisions).length === 1, JSON.stringify(planNow && planNow.style && planNow.style.decisions));
    L.check('the snapshot references BOTH layers and keeps the cited patient facts verbatim', planNow.style.profiles.length === 2 && Object.keys(planNow.style.facts).length >= 1, JSON.stringify(planNow.style.profiles.map((p) => p.layer)));
    const nurse = await L.newPage(b, { allow: [/\/auth\/me/, /403/] });
    await L.login(nurse, 'nu-001', 'Demo12345');
    const nres = await L.api(nurse, 'GET', `/style/encounters/${EID}/profiles`);
    L.check('a nurse cannot see or attach profiles to the encounter (403)', nres.status === 403);
    L.check('no unexpected console/HTTP problems (C: attending doctor)', d.problems.filter((p) => !/403|404/.test(p)).length === 0, d.problems.join(' | '));
    L.check('no unexpected console/HTTP problems (admin, other doctor)', adm.problems.length === 0 && young.problems.filter((p) => !/403|404/.test(p)).length === 0, [...adm.problems, ...young.problems].join(' | '));
  } catch (e) { console.log('SCENARIO ERROR:', e.stack || e); try { await L.shot(globalThis.__page, 'error'); } catch {} L.check('scenario ran to completion', false, String(e.message)); }
  finally { await b.close(); }
  process.exit(L.summary() ? 1 : 0);
})();

function location_pid(p) { return p.split('/')[2]; }
async function clickText2(page, text) { // first VISIBLE button/link containing text
  const ok = await page.evaluate((text) => { const el = [...document.querySelectorAll('button,a')].find((e) => e.offsetParent !== null && !e.disabled && e.innerText.replace(/\s+/g, '').includes(text)); if (!el) return false; el.scrollIntoView({ block: 'center' }); el.click(); return true; }, text);
  if (!ok) throw new Error(`clickText2: nothing visible with "${text}"`);
}
