/* Isolated real Chromium: shipped DOM/CSS/Analysis module, local API fixtures.
   No installed app, user browser, network or tablet is used.
   NODE_PATH=<temp>/node_modules node tools/analysis-layout-smoke.cjs
   PITBOX_BROWSER_PATH optionally selects installed Edge instead of Chromium.
   --baseline verifies both scrolling regressions against 4.12.2 source. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');
const root = path.resolve(__dirname, '..');
const staticRoot = process.env.PITBOX_STATIC_ROOT || path.join(root, 'static');
const baseline = process.argv.includes('--baseline');
const sizes = [[390,844], [844,390], [800,1280], [1024,768], [1680,1040], [1440,900]];
const old = name => execFileSync('git', ['show', `90d3c72:static/${name}`], { cwd: root, encoding: 'utf8' });
const series = (values, unit = 'ratio') => ({ values, unit, availability: 'observed', coverage: 1 });
const axis = Array.from({ length: 64 }, (_, i) => i * 10);
const lapTrace = { lap_id: 'lap-a', coverage: 1, source: 'fixture', axis: { name: 'distance', unit: 'm', values: axis }, series: {
  speed: series(axis.map((_, i) => 20 + i / 2), 'm/s'), gear: series(axis.map(() => 4)),
  throttle: series(axis.map(() => .8)), brake: series(axis.map(() => 0)), steering: series(axis.map(() => .1)),
  world_x: series(axis.map((_, i) => Math.cos(i / 63 * Math.PI * 2) * 100), 'm'),
  world_z: series(axis.map((_, i) => Math.sin(i / 63 * Math.PI * 2) * 50), 'm'),
} };
const fixtures = {
  '/api/v1/sessions': { items: [{ id: 'session-a', display_name: 'Recorded race', session_type: 'Race' }] },
  '/api/v1/storage/status': {},
  '/api/v1/sessions/session-a': { session: { id: 'session-a', display_name: 'Recorded race', participants: [] } },
  '/api/v1/sessions/session-a/quality': { quality_score: 1, laps: { total: 1, valid: 1 } },
  '/api/v1/sessions/session-a/laps': { items: [{ id: 'lap-a', valid: true, lap_number: 1, lap_time_ms: 90000, display_name: 'Driver' }] },
  '/api/v1/sessions/session-a/field': { classification: [], cars_observed: 1 },
  '/api/v1/laps/lap-a/trace': lapTrace,
  '/api/v1/laps/lap-a/references': { items: [] },
};

async function showView(page, view) {
  await page.evaluate(name => {
    const workspace = document.getElementById('analysis');
    document.querySelectorAll('.analysis-view').forEach(el => { el.hidden = el.id !== name; });
    if (name === 'field') {
      document.querySelectorAll('.field-panel').forEach(el => { el.hidden = el.id !== 'field-panel-positions'; });
      // Bounded below-chart fixture content ensures room to consume a swipe,
      // independent of how much an empty field session happens to occupy.
      if (!document.getElementById('layoutFixtureTail')) {
        const tail = document.createElement('div');
        tail.id = 'layoutFixtureTail'; tail.style.height = '600px';
        tail.setAttribute('aria-hidden', 'true');
        document.getElementById('field-panel-positions').append(tail);
      }
    }
    workspace.scrollTop = 0;
  }, view);
}

async function expose(page, selector, target = 'center') {
  await page.evaluate(({ selector, target }) => {
    const workspace = document.getElementById('analysis');
    const element = document.querySelector(selector);
    const area = workspace.getBoundingClientRect(), box = element.getBoundingClientRect();
    const offset = target === 'top' ? 24 : Math.max(8, (area.height - Math.min(box.height, area.height - 20)) / 2);
    workspace.scrollTop += box.top - area.top - offset;
  }, { selector, target });
  await page.waitForTimeout(80);
}

async function touchSwipe(page, cdp, selector) {
  await expose(page, selector);
  const geometry = await page.locator(selector).evaluate(el => {
    const r = el.getBoundingClientRect(), workspace = document.getElementById('analysis'), area = workspace.getBoundingClientRect();
    const top = Math.max(r.top, area.top) + 10, bottom = Math.min(r.bottom, area.bottom) - 10;
    // A tall viewport can already be at the bottom when the trace is centered.
    // Swipe downward there; an upward swipe cannot consume any more scroll.
    const down = workspace.scrollHeight - workspace.clientHeight - workspace.scrollTop < 30;
    return { x: r.left + r.width * .65, startY: down ? top : bottom, endY: down ? Math.min(bottom, top + 180) : Math.max(top, bottom - 180),
      before: workspace.scrollTop, maxScroll: workspace.scrollHeight - workspace.clientHeight,
      cursor: document.getElementById('playbackRange').value, touchAction: getComputedStyle(el).touchAction };
  });
  assert.ok(Math.abs(geometry.startY - geometry.endY) >= 25, `${selector}: enough visible canvas for real gesture`);
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: geometry.x, y: geometry.startY }] });
  for (let step = 1; step <= 8; step++) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: geometry.x, y: geometry.startY + (geometry.endY - geometry.startY) * step / 8 }] });
    await page.waitForTimeout(25);
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await page.waitForTimeout(300);
  return { ...geometry, ...await page.evaluate(() => ({ after: document.getElementById('analysis').scrollTop, cursorAfter: document.getElementById('playbackRange').value })) };
}

(async () => {
  const browser = await chromium.launch({ headless: true, ...(process.env.PITBOX_BROWSER_PATH ? { executablePath: process.env.PITBOX_BROWSER_PATH } : {}) });
  const results = [], failures = [];
  try {
    for (const [width, height] of sizes) {
      const context = await browser.newContext({ viewport: { width, height }, hasTouch: true });
      const page = await context.newPage(), pageErrors = [];
      page.on('pageerror', error => pageErrors.push(error.message));
      await page.route('**/*', async route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'http://pitbox.test') return route.abort();
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(staticRoot, 'index.html'), 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '') });
        if (/^\/static\/(css|js)\/[\w.-]+\.(css|js)$/.test(url.pathname)) {
          const name = url.pathname.slice('/static/'.length);
          const body = baseline && ['css/v42.css', 'js/workspaces.js'].includes(name) ? old(name) : fs.readFileSync(path.join(staticRoot, name), 'utf8');
          return route.fulfill({ contentType: name.endsWith('.css') ? 'text/css' : 'text/javascript', body });
        }
        if (Object.hasOwn(fixtures, url.pathname)) return route.fulfill({ contentType: 'application/json', body: JSON.stringify(fixtures[url.pathname]) });
        return route.abort();
      });
      await page.goto('http://pitbox.test/#lap-lab');
      await page.evaluate(() => {
        document.querySelectorAll('.page').forEach(el => { el.hidden = el.id !== 'analysis'; el.classList.toggle('active', !el.hidden); });
        document.getElementById('bootOverlay')?.remove();
      });
      await showView(page, 'lap-lab');
      await page.addScriptTag({ type: 'module', url: 'http://pitbox.test/static/js/workspaces.js' });
      await page.waitForFunction(() => window.pitwallWorkspaces);
      await page.evaluate(async () => { await window.pitwallWorkspaces.selectSession('session-a'); await window.pitwallWorkspaces.openLap('lap-a'); });
      const label = `${width}x${height}`;
      const check = (name, pass, detail) => { results.push({ viewport: [width,height], check: name, passed: pass, ...detail }); if (!pass) failures.push({ viewport: [width,height], check: name, ...detail }); };
      const header = await page.evaluate(() => {
        const workspace = document.getElementById('analysis'), header = document.querySelector('.comparison-header');
        workspace.scrollTop = 0;
        const before = header.getBoundingClientRect().top;
        workspace.scrollTop = 350;
        return { position: getComputedStyle(header).position, scrolled: workspace.scrollTop, moved: before - header.getBoundingClientRect().top };
      });
      check('header-normal-flow', header.position === 'static' && Math.abs(header.scrolled - header.moved) < 1, header);
      for (const selector of ['#playbackToggle', '#gaugeSpeed']) {
        await expose(page, selector, 'top');
        const hit = await page.locator(selector).evaluate(el => { const r = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.left+r.width/2, r.top+r.height/2)); });
        check(`unobscured-${selector}`, hit, {});
        if (selector === '#playbackToggle') {
          let playsAndPauses = false;
          if (hit) {
            await page.locator(selector).tap();
            await page.waitForTimeout(180);
            const playing = await page.locator(selector).getAttribute('aria-pressed');
            const moved = Number(await page.locator('#playbackRange').inputValue()) > 0;
            await page.locator(selector).tap();
            playsAndPauses = playing === 'true' && moved && await page.locator(selector).getAttribute('aria-pressed') === 'false';
            await page.locator('#playbackRange').evaluate(el => { el.value = '0'; el.dispatchEvent(new Event('input')); });
          }
          check('play-advances-and-pauses', playsAndPauses, {});
        }
      }
      const cdp = await context.newCDPSession(page);
      for (const selector of ['#comparisonMap', '#comparisonTrace', '#fieldPositionChart']) {
        await showView(page, selector === '#fieldPositionChart' ? 'field' : 'lap-lab');
        const gesture = await touchSwipe(page, cdp, selector);
        check(`touch-scroll-${selector}`, Math.abs(gesture.after - gesture.before) > 10, gesture);
        if (selector === '#comparisonTrace') check('swipe-does-not-seek', gesture.cursor === gesture.cursorAfter, {});
      }
      await showView(page, 'lap-lab');
      await expose(page, '#comparisonTrace');
      const tap = await page.locator('#comparisonTrace').evaluate(el => {
        const r = el.getBoundingClientRect(), area = document.getElementById('analysis').getBoundingClientRect();
        return { x: r.left + r.width * .7, y: (Math.max(r.top, area.top) + Math.min(r.bottom, area.bottom)) / 2 };
      });
      await page.touchscreen.tap(tap.x, tap.y);
      const cursor = await page.locator('#playbackRange').inputValue();
      check('tap-seeks', Number(cursor) > 0, { cursor });
      assert.deepEqual(pageErrors, [], `${label}: no JavaScript errors`);
      if (process.env.PITBOX_LAYOUT_SCREENSHOTS) {
        fs.mkdirSync(process.env.PITBOX_LAYOUT_SCREENSHOTS, { recursive: true });
        await page.screenshot({ path: path.join(process.env.PITBOX_LAYOUT_SCREENSHOTS, `analysis-${baseline ? 'baseline-' : ''}${width}x${height}.png`) });
      }
      await context.close();
    }
    if (baseline) {
      assert.ok(failures.some(item => item.check === 'header-normal-flow'), '4.12.2 sticky-header bug must reproduce');
      assert.ok(failures.some(item => item.check === 'touch-scroll-#comparisonMap'), '4.12.2 canvas touch trap must reproduce');
      console.log(JSON.stringify({ result: 'baseline-regressions-reproduced', checks: results.length, failures }, null, 2));
    } else {
      console.log(JSON.stringify({ result: failures.length ? 'failed' : 'passed', checks: results.length, cases: results }, null, 2));
      assert.equal(failures.length, 0, JSON.stringify(failures));
    }
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
