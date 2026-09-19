/* Real Chromium layout, isolated from the user's browser, backend and data.
   npm install --prefix <temp> --no-save playwright@1.58.2
   NODE_PATH=<temp>/node_modules node tools/strategy-layout-smoke.cjs
   CI uses Playwright Chromium; PITBOX_BROWSER_PATH can select installed Edge.
   --baseline loads the pre-fix CSS from git to demonstrate the regression. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {execFileSync} = require('node:child_process');
const {chromium} = require('playwright');
const root = path.resolve(__dirname, '..');
const staticRoot = process.env.PITBOX_STATIC_ROOT || path.join(root, 'static');
const baseline = process.argv.includes('--baseline');
const sizes = [[390,844], [428,926], [844,390], [960,800], [961,800],
  [1024,768], [1180,820], [1181,820], [1440,900], [1440,450]];

(async () => {
  const browser = await chromium.launch({headless: true,
    ...(process.env.PITBOX_BROWSER_PATH ? {executablePath: process.env.PITBOX_BROWSER_PATH} : {})});
  const results = [];
  try {
    for (const [width, height] of sizes) {
      const context = await browser.newContext({viewport: {width, height}});
      const page = await context.newPage();
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'http://pitbox.test') return route.abort();
        if (url.pathname === '/') {
          // Keep the shipped DOM/styles; suppress background APIs and startup dialogs.
          const html = fs.readFileSync(path.join(staticRoot, 'index.html'), 'utf8')
            .replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '');
          return route.fulfill({contentType: 'text/html', body: html});
        }
        if (/^\/static\/css\/[\w.-]+\.css$/.test(url.pathname)) {
          const css = baseline && url.pathname.endsWith('/v42.css')
            ? execFileSync('git', ['show', '904124d:static/css/v42.css'], {cwd: root, encoding: 'utf8'})
            : fs.readFileSync(path.join(staticRoot, url.pathname.slice('/static/'.length)), 'utf8');
          return route.fulfill({contentType: 'text/css', body: css});
        }
        return route.abort();
      });
      await page.goto('http://pitbox.test/');
      await page.evaluate(() => {
        document.querySelectorAll('.page').forEach(el => {
          el.hidden = el.id !== 'strategy'; el.classList.toggle('active', !el.hidden);
        });
        document.getElementById('bootOverlay')?.remove();
      });
      for (const populated of [false, true]) {
        if (populated) await page.evaluate(() => {
          document.getElementById('stratRadio').textContent = 'Engineer: Hold the plan.\n'.repeat(80);
          document.getElementById('stratLog').innerHTML = Array.from({length: 30}, (_, i) =>
            `<div class="decision-entry"><span class="lap">Lap ${i+1}</span><span>Hold the plan</span></div>`).join('');
        });
        const layout = await page.evaluate(() => {
          const box = el => { const r = el.getBoundingClientRect(); return {top:r.top,bottom:r.bottom,left:r.left,right:r.right}; };
          const workspace = document.getElementById('strategy');
          workspace.scrollTop = 0;
          const rail = document.querySelector('.strategy-side');
          const style = getComputedStyle(rail);
          return {position: style.position, row: style.gridRow, overflow: style.overflowY,
            columns: getComputedStyle(document.querySelector('.strategy-workspace')).gridTemplateColumns.split(' ').length,
            main: [...document.querySelectorAll('.strategy-main-col')].map(box), rail: box(rail),
            page: box(workspace), scrollHeight:workspace.scrollHeight, clientHeight:workspace.clientHeight,
            scrollWidth:workspace.scrollWidth, clientWidth:workspace.clientWidth};
        });
        const label = `${width}x${height} ${populated ? 'populated' : 'empty'}`;
        assert.ok(layout.scrollHeight > layout.clientHeight, `${label}: page must scroll`);
        assert.ok(layout.scrollWidth <= layout.clientWidth + 1, `${label}: no horizontal overflow`);
        if (width <= 1180) {
          assert.equal(layout.columns, 1, `${label}: exactly one column`);
          assert.equal(layout.position, 'static', `${label}: rail must not stick`);
          assert.equal(layout.row, 'auto', `${label}: rail must not span mobile rows`);
          assert.equal(layout.overflow, 'visible', `${label}: no nested rail scroller`);
          const cards = [...layout.main, layout.rail];
          for (let i=1; i<cards.length; i++) assert.ok(cards[i].top >= cards[i-1].bottom,
            `${label}: cards must retain DOM order without overlap`);
        } else {
          assert.equal(layout.columns, 2, `${label}: desktop keeps two columns`);
          assert.ok(layout.rail.left >= layout.main[0].right, `${label}: radio beside plans`);
          assert.equal(layout.position, height >= 600 ? 'sticky' : 'static', label);
          if (height >= 600) assert.ok(layout.rail.bottom-layout.rail.top <= layout.clientHeight-24+1,
            `${label}: sticky rail height must fit between shell header and footer`);
        }
        const scroll = await page.evaluate(() => {
          const workspace = document.getElementById('strategy');
          const hero = document.querySelector('.strategy-workspace > header');
          const rail = document.querySelector('.strategy-side');
          const before = {hero:hero.getBoundingClientRect().top, rail:rail.getBoundingClientRect().top};
          workspace.scrollTop = 200;
          return {delta:workspace.scrollTop, hero:before.hero-hero.getBoundingClientRect().top,
            rail:before.rail-rail.getBoundingClientRect().top};
        });
        assert.equal(Math.round(scroll.hero), scroll.delta, `${label}: hero scrolls away`);
        if (width <= 1180) assert.equal(Math.round(scroll.rail), scroll.delta, `${label}: rail scrolls with page`);
        await page.locator('#stratLogRefresh').scrollIntoViewIfNeeded();
        assert.ok(await page.locator('#stratLogRefresh').isVisible(), label);
        const hit = await page.locator('#stratLogRefresh').evaluate(el => {
          const r = el.getBoundingClientRect();
          return el.contains(document.elementFromPoint(r.x+r.width/2, r.y+r.height/2));
        });
        assert.equal(hit, true, `${label}: Decision log reachable and not covered`);
        results.push({viewport: [width,height], populated, result:'passed'});
      }
      if (process.env.PITBOX_LAYOUT_SCREENSHOTS) {
        fs.mkdirSync(process.env.PITBOX_LAYOUT_SCREENSHOTS, {recursive:true});
        await page.screenshot({path:path.join(process.env.PITBOX_LAYOUT_SCREENSHOTS, `strategy-${width}x${height}.png`)});
      }
      await context.close();
    }
    console.log(JSON.stringify({result:'passed', cases:results}, null, 2));
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
