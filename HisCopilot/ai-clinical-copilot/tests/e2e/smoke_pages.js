// Render every staff page for each role against the running stack; fail on console errors / uncaught exceptions / unexpected HTTP errors.
const L = require('./lib');
const USERS = { doctor: [process.env.DOC_USER || 'dr-001', process.env.DOC_PW || 'Demo12345'], admin: ['admin', 'Demo12345'], pharm: ['ph-001', 'Demo12345'], nurse: ['nu-001', 'Demo12345'] };
(async () => {
  const b = await L.launch();
  try {
    for (const [role, [u, p]] of Object.entries(USERS)) {
      console.log(`\n# ${role} (${u})`);
      const page = await L.newPage(b, { allow: [/\/auth\/me/] });
      await L.login(page, u, p);
      const nav = await page.evaluate(() => [...document.querySelectorAll('aside a')].map((a) => a.getAttribute('href')));
      console.log('  sidebar:', nav.join(' '));
      const routes = [...new Set(nav)];
      const pats = (await L.api(page, 'GET', '/patients?limit=1')).body, encs = (await L.api(page, 'GET', '/encounters?scope=all&limit=1')).body;
      if (pats?.items?.[0]) routes.push(`/patients/${pats.items[0].id}`);
      if (encs?.items?.[0]) routes.push(`/encounters/${encs.items[0].id}`);
      if (role === 'doctor') routes.push('/encounters/new', '/account');
      for (const r of routes) {
        page.problems.length = 0;
        await L.goto(page, r, 1500);
        const h = await page.evaluate(() => (document.querySelector('main h1, main h2') || {}).innerText || '');
        const blank = await page.evaluate(() => document.querySelector('main')?.innerText.trim().length < 20);
        L.check(`${role} ${r} renders ("${h.slice(0, 20)}")`, !blank && page.problems.length === 0, page.problems.join(' | '));
        await L.shot(page, `${role}${r.replace(/\//g, '_') || '_home'}`);
      }
      await page.ctx.close();
    }
  } finally { await b.close(); }
  process.exit(L.summary() ? 1 : 0);
})().catch((e) => { console.error('FATAL', e); process.exit(2); });
