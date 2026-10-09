/* Session Analysis in real Chromium: layout, readability, touch gestures and
   tooltips with populated data. Serves the shipped static files (inline
   scripts stripped, as analysis-layout-smoke does), mocks /api/v1 and loads
   only the Session Analysis module. No installed app, user data or network.
     NODE_PATH=<dir with playwright>/node_modules node tools/session-analysis-layout-smoke.cjs
   Payloads: the committed synthetic race, a practice session derived from it
   (with one lap missing from the recording) and a 22-car, 60-lap race printed
   by `python tests/session_analysis_fixture.py --large` (PITBOX_PYTHON selects
   the interpreter; src/ is put on PYTHONPATH).
   PITBOX_ANALYSIS_PAYLOADS=<file>[<path delimiter><file>...] adds more payloads
   for local checks. PITBOX_BROWSER_PATH selects an installed Chrome or Edge.
   PITBOX_WIDE_FONT=<family> (for example Verdana) lays the view out in a wider
   font than the app's, as machines without Segoe UI see it, and
   PITBOX_WIDE_SPACING=<length> adds letter spacing on top: .07em reproduces
   the wrapping of the Linux CI runner's fonts on Windows.
   PITBOX_SHOTS=<dir> saves a viewport screenshot per viewport and payload;
   PITBOX_SHOTS_CARDS=<payload name>[,<name>] also saves every card of those
   payloads (names: race, large-race, practice, or an added file's name). */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const staticRoot = path.join(root, 'static');
const shots = process.env.PITBOX_SHOTS
  ? path.resolve(/^(1|true|yes)$/i.test(process.env.PITBOX_SHOTS) ? path.join(os.tmpdir(), 'pitbox-session-analysis-shots') : process.env.PITBOX_SHOTS)
  : null;
const VIEWPORTS = [
  { width: 390, height: 844, deviceScaleFactor: 1 },
  { width: 800, height: 1280, deviceScaleFactor: 1 },
  { width: 1691, height: 879, deviceScaleFactor: 1.75 },
  { width: 1440, height: 900, deviceScaleFactor: 1 },
];
const CHARTS = ['analysisPaceChart', 'analysisTraceChart', 'analysisPositionsChart', 'analysisStrategyChart', 'analysisLapTimesChart', 'analysisHeatmapChart', 'analysisLapseChart', 'analysisFastestChart'];
const clone = (value) => JSON.parse(JSON.stringify(value));

function largeRace() {
  const python = process.env.PITBOX_PYTHON || 'python';
  const env = { ...process.env, PYTHONPATH: [path.join(root, 'src'), process.env.PYTHONPATH].filter(Boolean).join(path.delimiter) };
  const out = execFileSync(python, [path.join(root, 'tests', 'session_analysis_fixture.py'), '--large'], { cwd: root, env, encoding: 'utf8', maxBuffer: 32 * 1024 * 1024 });
  const race = JSON.parse(out);
  // The cases this race exists to lay out, so a generator change cannot
  // quietly drop them.
  const statuses = race.drivers.map((d) => d.status);
  assert.equal(race.schema_version, 2);
  assert.equal(race.drivers.length, 22);
  assert.equal(Math.max(...race.laps.map((l) => l.lap_number)), 60);
  assert.equal(statuses.filter((s) => s === 'lapped').length, 2);
  assert.ok(statuses.includes('retired') && statuses.includes('incomplete_record'));
  assert.ok(race.race_control.vsc_laps.length && race.race_control.safety_car_laps.length);
  assert.ok(race.drivers.some((d) => d.pit_stops === null));
  assert.ok(race.pit_stops.some((s) => s.under_neutralisation && s.kind === 'pit_stop'));
  return race;
}

function payloads() {
  const race = JSON.parse(fs.readFileSync(path.join(root, 'tests/fixtures/session_analysis_race.json'), 'utf8'));
  const practice = clone(race);
  practice.session_id = 'ses_fixture_practice';
  Object.assign(practice.session, { is_race: false, session_type: 'Practice 1', leader_laps: null, winner_laps: null });
  practice.basis.pace_laps = 'excludes pit laps, flag laps, invalid laps, laps excluded in lap notes, laps recorded without telemetry context and laps without a time';
  for (const lap of practice.laps) Object.assign(lap, { position: null, gap_to_leader_ms: null, interval_ms: null, neutralised: null, suspended: false, pace_excluded: lap.lap_time_ms ? null : 'missing_time' });
  for (const d of practice.drivers) Object.assign(d, { status: 'running', finish_position: null, laps_down: null });
  Object.assign(practice, { segments: [], suspended_laps: [], neutralised_laps: [], winner_car_index: null, warnings: [] });
  practice.race_control = { available: false, covered_from_lap: null, neutralisations: [], safety_car_laps: [], vsc_laps: [], red_flag_laps: [] };
  // A lap missing from the recording: its heatmap cell is dashed and empty.
  practice.laps = practice.laps.filter((lap) => !(lap.car_index === 0 && lap.lap_number === 4));
  const list = [
    { name: 'race', payload: race },
    { name: 'large-race', payload: largeRace() },
    { name: 'practice', payload: practice },
  ];
  for (const file of (process.env.PITBOX_ANALYSIS_PAYLOADS || '').split(path.delimiter).filter(Boolean)) {
    const payload = JSON.parse(fs.readFileSync(file, 'utf8'));
    payload.session_id = payload.session_id || `ses_extra_${list.length}`;
    list.push({ name: path.basename(file, '.json'), payload });
  }
  return list;
}

// Static guards on the Session Analysis CSS: nothing sized against the
// viewport height, no gesture lock, no scroll containment.
function cssGuards() {
  const css = fs.readFileSync(path.join(staticRoot, 'css/v42.css'), 'utf8');
  const block = css.slice(css.indexOf('/* Session Analysis: post-session charts.'), css.indexOf('/* Lap Lab: driver, then lap'));
  assert.ok(block.length > 1000, 'the Session Analysis CSS block is found');
  assert.doesNotMatch(block, /\d(?:d|s|l)?vh\b/, 'no vh/dvh sizes');
  assert.doesNotMatch(block, /touch-action:\s*(?:none|pan-y\s*;|pan-x\s*;)/, 'no touch-action lock');
  assert.doesNotMatch(block, /overscroll-behavior/, 'no scroll containment');
  assert.doesNotMatch(block, /position:\s*(?:sticky|fixed)/, 'no sticky or fixed elements');
  // An unpainted cell would only answer a tap on its 1 px outline.
  assert.doesNotMatch(block.match(/\.sa-cell-missing \{[^}]*\}/)?.[0] || 'missing rule', /fill:\s*none|pointer-events:\s*none|missing rule/, 'not-recorded cells take taps');
}

// Scrolls #analysis so the element's top sits `by` px above the top of the
// visible area (negative: below it).
async function revealAt(page, selector, by) {
  await page.evaluate(([target, offset]) => {
    const area = document.getElementById('analysis');
    const r = document.querySelector(target).getBoundingClientRect();
    area.scrollTop += r.top - area.getBoundingClientRect().top + offset;
  }, [selector, by]);
  await page.waitForTimeout(80);
}

// The open reading of a chart: where it is, whether it lies inside the
// visible part of the page and whether it is what a finger would touch there.
async function tipBox(page, chartId) {
  return page.evaluate((id) => {
    const t = document.querySelector(`#${id} > .sa-tip`);
    if (!t || t.hidden) return null;
    const r = t.getBoundingClientRect();
    const a = document.getElementById('analysis').getBoundingClientRect();
    const centre = document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2);
    return {
      left: Math.round(r.left), right: Math.round(r.right), top: Math.round(r.top), bottom: Math.round(r.bottom), area: [Math.round(a.top), Math.round(a.bottom)],
      inArea: r.top >= a.top - 0.5 && r.bottom <= a.bottom + 0.5 && r.left >= -0.5 && r.right <= innerWidth + 0.5,
      onTop: Boolean(centre?.closest('.sa-tip')), text: t.textContent.slice(0, 50),
    };
  }, chartId);
}

// A tap in the page gutter, outside every chart, closes a pinned reading.
async function closeTips(page) {
  const y = await page.evaluate(() => document.getElementById('analysis').getBoundingClientRect().bottom - 6);
  await page.touchscreen.tap(3, y);
  await page.waitForTimeout(60);
}

// Readings stay visible near the top of the scrolled page, follow the
// heatmap sideways, point at the race-pace box and read missing laps.
async function readingChecks(page, cdp, set, viewport, check, charts) {
  await page.evaluate(() => { window.__opened.length = 0; });
  // A cell in the heatmap's first row with the chart at the top of the visible area.
  await closeTips(page);
  await revealAt(page, '#analysisHeatmapChart', -6);
  const topCell = await page.evaluate(() => {
    const scroller = document.querySelector('#analysisHeatmapChart .sa-heat-scroll');
    const box = scroller.getBoundingClientRect();
    const a = document.getElementById('analysis').getBoundingClientRect();
    const cells = [...scroller.querySelectorAll('rect[data-lap-id][data-row="0"]')].map((c) => c.getBoundingClientRect())
      .filter((r) => r.left >= box.left + 2 && r.right <= box.right - 2 && r.top >= a.top && r.bottom <= a.bottom);
    const r = cells[Math.min(3, cells.length - 1)];
    return r ? { x: r.left + r.width / 2, y: r.top + r.height / 2 } : null;
  });
  if (topCell) {
    await page.touchscreen.tap(topCell.x, topCell.y);
    await page.waitForTimeout(80);
    const tip = await tipBox(page, 'analysisHeatmapChart');
    check(set, 'heatmap-reading-near-the-top-is-visible', Boolean(tip?.inArea && tip.onTop), { tip, cell: topCell });
  } else check(set, 'heatmap-reading-near-the-top-is-visible', false, { note: 'no first-row cell in view' });
  // Crosshair readings with the chart at the top, and with its top scrolled away.
  for (const id of ['analysisTraceChart', 'analysisPositionsChart'].filter((c) => charts.includes(c))) {
    for (const by of [-6, 120]) {
      await closeTips(page);
      await revealAt(page, `#${id}`, by);
      const spot = await page.evaluate((cid) => {
        const hit = document.querySelector(`#${cid} .sa-hit`).getBoundingClientRect();
        const a = document.getElementById('analysis').getBoundingClientRect();
        const top = Math.max(hit.top, a.top);
        const bottom = Math.min(hit.bottom, a.bottom);
        return bottom - top < 40 ? null : { x: Math.round(hit.left + hit.width * 0.55), y: Math.round(top + 20) };
      }, id);
      if (!spot) continue;
      await page.touchscreen.tap(spot.x, spot.y);
      await page.waitForTimeout(80);
      const tip = await tipBox(page, id);
      check(set, `crosshair-reading-is-visible-${id}-${by > 0 ? 'scrolled' : 'top'}`, Boolean(tip?.inArea && tip.onTop), { tip, spot });
    }
  }
  // A pinned heatmap reading follows a sideways swipe, and closes once its cell has gone.
  await closeTips(page);
  await page.evaluate(() => { document.querySelector('#analysisHeatmapChart .sa-heat-scroll').scrollLeft = 0; });
  await reveal(page, '#analysisHeatmapChart');
  const strip = await page.evaluate(() => {
    const scroller = document.querySelector('#analysisHeatmapChart .sa-heat-scroll');
    const box = scroller.getBoundingClientRect();
    const a = document.getElementById('analysis').getBoundingClientRect();
    const cells = [...scroller.querySelectorAll('rect[data-lap-id]')].map((c) => ({ c, r: c.getBoundingClientRect() }))
      .filter(({ r }) => r.left >= box.left + box.width * 0.55 && r.right <= box.right - 4 && r.top >= a.top + 120 && r.bottom <= Math.min(box.bottom, a.bottom) - 50);
    const pick = cells[0];
    return { room: scroller.scrollWidth - scroller.clientWidth, width: box.width, swipeY: Math.min(box.bottom, a.bottom) - 30, cell: pick ? { key: pick.c.getAttribute('data-key'), x: pick.r.left + pick.r.width / 2, y: pick.r.top + pick.r.height / 2 } : null };
  });
  if (strip.room > 120 && strip.cell) {
    const follow = async () => page.evaluate((key) => {
      const scroller = document.querySelector('#analysisHeatmapChart .sa-heat-scroll');
      const box = scroller.getBoundingClientRect();
      const m = document.querySelector(`#analysisHeatmapChart [data-key="${key}"]`).getBoundingClientRect();
      const t = document.querySelector('#analysisHeatmapChart > .sa-tip');
      const tip = t && !t.hidden ? t.getBoundingClientRect() : null;
      const centre = (m.left + m.right) / 2;
      return { scrollLeft: Math.round(scroller.scrollLeft), markCentre: Math.round(centre), markVisible: m.right > box.left && m.left < box.right, tip: tip ? [Math.round(tip.left), Math.round(tip.right)] : null, covers: tip ? tip.left <= centre && centre <= tip.right : null };
    }, strip.cell.key);
    await page.touchscreen.tap(strip.cell.x, strip.cell.y);
    await page.waitForTimeout(80);
    const x = strip.cell.x;
    await swipe(page, cdp, { x, y: strip.swipeY }, { x: x - 40, y: strip.swipeY });
    const near = await follow();
    check(set, 'heatmap-reading-follows-its-cell', near.scrollLeft > 10 && near.markVisible && near.covers === true, near);
    await swipe(page, cdp, { x, y: strip.swipeY }, { x: Math.max(8, x - Math.min(260, strip.width - 20)), y: strip.swipeY });
    const far = await follow();
    check(set, 'heatmap-reading-closes-when-its-cell-leaves', far.markVisible ? far.covers === true : far.tip === null, far);
  }
  // Race pace: a tapped box gets its reading at the box, in rows or columns.
  await closeTips(page);
  await reveal(page, '#analysisPaceChart');
  const boxes = await page.evaluate(() => {
    const a = document.getElementById('analysis').getBoundingClientRect();
    return [...document.querySelectorAll('#analysisPaceChart [data-key^="box-"] rect')].map((r) => r.getBoundingClientRect())
      .filter((r) => r.top >= a.top + 8 && r.bottom <= a.bottom - 8)
      .map((r) => ({ x: r.left + r.width / 2, y: r.top + r.height / 2, top: r.top, bottom: r.bottom }));
  });
  for (const box of [boxes[0], boxes[boxes.length - 1]].filter(Boolean)) {
    await page.touchscreen.tap(box.x, box.y);
    await page.waitForTimeout(80);
    const tip = await tipBox(page, 'analysisPaceChart');
    check(set, 'pace-reading-at-its-box', Boolean(tip) && tip.left <= box.x && box.x <= tip.right && (tip.bottom <= box.top + 1 || tip.top >= box.bottom - 1), { tip, box });
  }
  // A cell for a lap missing from the recording answers a tap anywhere on it.
  await closeTips(page);
  const missing = await page.evaluate(() => {
    const cell = document.querySelector('#analysisHeatmapChart .sa-cell-missing');
    if (!cell) return null;
    cell.scrollIntoView({ block: 'center', inline: 'center' });
    const r = cell.getBoundingClientRect();
    const x = r.left + r.width / 2;
    const y = r.top + r.height / 2;
    return { x, y, hit: document.elementFromPoint(x, y) === cell, label: cell.getAttribute('aria-label') };
  });
  if (missing) {
    await page.waitForTimeout(80);
    await page.touchscreen.tap(missing.x, missing.y);
    await page.waitForTimeout(80);
    const tip = await tipBox(page, 'analysisHeatmapChart');
    check(set, 'not-recorded-cell-answers-a-tap', missing.hit && Boolean(tip) && /not recorded/.test(tip.text), { missing, tip });
  } else if (set.name === 'practice') check(set, 'not-recorded-cell-answers-a-tap', false, { note: 'the practice payload has no missing lap' });
  // Every value axis of race pace is readable: at least three labels.
  const ticks = await page.evaluate(() => [...document.querySelectorAll('#analysisPaceChart svg text.sa-tick')].map((t) => t.textContent));
  check(set, 'pace-axis-has-three-labels', ticks.length >= 3, { ticks, width: viewport.width });
  await closeTips(page);
  check(set, 'readings-never-open-a-lap', (await page.evaluate(() => window.__opened.length)) === 0);
}

async function reveal(page, selector) {
  await page.evaluate((target) => {
    const area = document.getElementById('analysis');
    const el = document.querySelector(target);
    const a = area.getBoundingClientRect();
    const r = el.getBoundingClientRect();
    area.scrollTop += r.top - a.top - Math.max(8, (a.height - Math.min(r.height, a.height - 40)) / 2);
  }, selector);
  await page.waitForTimeout(80);
}

async function swipe(page, cdp, from, to, steps = 8) {
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: [{ x: from.x, y: from.y }] });
  for (let i = 1; i <= steps; i += 1) {
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: [{ x: from.x + ((to.x - from.x) * i) / steps, y: from.y + ((to.y - from.y) * i) / steps }] });
    await page.waitForTimeout(16);
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await page.waitForTimeout(300);
}

async function openPage(browser, viewport, sets) {
  const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height }, deviceScaleFactor: viewport.deviceScaleFactor, hasTouch: true });
  await context.addInitScript(() => {
    window.__errors = [];
    window.__opened = [];
    window.addEventListener('error', (event) => window.__errors.push(String(event.message || event)));
    window.addEventListener('pitwall:open-lap', (event) => window.__opened.push(event.detail));
  });
  const page = await context.newPage();
  const problems = [];
  page.on('pageerror', (error) => problems.push(`pageerror: ${error.message}`));
  page.on('console', (message) => { if (message.type() === 'error') problems.push(`console: ${message.text()}`); });
  const byId = new Map(sets.map((s) => [String(s.payload.session_id), s.payload]));
  const items = sets.map((s) => ({ id: String(s.payload.session_id), track_name: s.payload.session?.track_name || 'Track', session_type: s.payload.session?.session_type || 'Session', started_at: s.payload.session?.started_at || null }));
  await page.route('**/*', async (route) => {
    const url = new URL(route.request().url());
    if (url.origin !== 'http://pitbox.test') return route.abort();
    if (url.pathname === '/') return route.fulfill({ contentType: 'text/html', body: fs.readFileSync(path.join(staticRoot, 'index.html'), 'utf8').replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '') });
    const asset = url.pathname.match(/^\/static\/((?:css|js)\/[\w.-]+\.(css|js|mjs))$/);
    if (asset) return route.fulfill({ contentType: asset[2] === 'css' ? 'text/css' : 'text/javascript', body: fs.readFileSync(path.join(staticRoot, asset[1]), 'utf8') });
    const file = path.resolve(staticRoot, `.${url.pathname.slice('/static'.length)}`);
    if (url.pathname.startsWith('/static/') && file.startsWith(staticRoot + path.sep) && fs.existsSync(file)) return route.fulfill({ path: file });
    if (url.pathname === '/api/v1/sessions') return route.fulfill({ contentType: 'application/json', body: JSON.stringify({ items }) });
    const match = url.pathname.match(/^\/api\/v1\/sessions\/([^/]+)\/analysis$/);
    if (match && byId.has(decodeURIComponent(match[1]))) {
      const payload = { ...byId.get(decodeURIComponent(match[1])), hide_outliers: url.searchParams.get('hide_outliers') === 'true' };
      return route.fulfill({ contentType: 'application/json', body: JSON.stringify(payload) });
    }
    return route.abort();
  });
  await page.goto('http://pitbox.test/#session-analysis');
  // What the inline router does on a reload at #session-analysis; the
  // remembered session then reopens when the deferred module loads.
  await page.evaluate((first) => {
    document.getElementById('bootOverlay')?.remove();
    document.querySelectorAll('.page').forEach((el) => { el.hidden = el.id !== 'analysis'; el.classList.toggle('active', !el.hidden); });
    document.querySelectorAll('.analysis-view').forEach((el) => { el.hidden = el.id !== 'session-analysis'; });
    localStorage.setItem('pitwall.analysis.session', first);
  }, String(sets[0].payload.session_id));
  // A wide fallback font, to see the layout as machines without Segoe UI do.
  if (process.env.PITBOX_WIDE_FONT) await page.addStyleTag({ content: `body, body * { font-family: ${process.env.PITBOX_WIDE_FONT}, sans-serif !important; }` });
  if (process.env.PITBOX_WIDE_SPACING) await page.addStyleTag({ content: `body, body * { letter-spacing: ${process.env.PITBOX_WIDE_SPACING} !important; }` });
  await page.addScriptTag({ type: 'module', url: 'http://pitbox.test/static/js/session-analysis.js' });
  return { context, page, problems };
}

async function waitForAnalysis(page, sessionId) {
  await page.waitForFunction((id) => {
    const status = document.getElementById('analysisStatus');
    const view = document.getElementById('session-analysis');
    return view.getAttribute('aria-busy') !== 'true' && document.getElementById('analysisSessionSelect').value === id
      && ['success', 'warning'].includes(status.dataset.tone) && document.querySelector('#analysisPaceChart svg, #analysisPaceChart .empty');
  }, sessionId, { timeout: 15000 });
  await page.evaluate(() => document.fonts.ready);
  // One ResizeObserver redraw happens on the next animation frame.
  await page.evaluate(() => new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(resolve))));
  await page.waitForTimeout(60);
}

async function geometry(page) {
  return page.evaluate((charts) => {
    const view = document.getElementById('session-analysis');
    const area = document.getElementById('analysis');
    const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
    const svgText = [...view.querySelectorAll('svg text')].filter((t) => t.textContent.trim() && visible(t));
    const smallText = svgText.map((t) => {
      const svg = t.ownerSVGElement;
      const k = svg.getBoundingClientRect().width / Number(svg.getAttribute('width') || svg.getBoundingClientRect().width);
      return { text: t.textContent.slice(0, 24), chart: t.closest('.sa-chart')?.id, height: Math.round(t.getBoundingClientRect().height * 10) / 10, size: Math.round(parseFloat(getComputedStyle(t).fontSize) * k * 10) / 10 };
    }).filter((t) => t.height < 10 || t.size < 10.95);
    const htmlText = [...view.querySelectorAll('*')].filter((el) => !el.closest('svg') && !el.closest('.sr-only') && [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim()) && visible(el))
      .map((el) => ({ text: el.textContent.trim().slice(0, 24), size: parseFloat(getComputedStyle(el).fontSize) })).filter((t) => t.size < 10.95);
    const scaled = [...view.querySelectorAll('svg.sa-svg')].filter(visible).map((s) => ({ chart: s.closest('.sa-chart')?.id, attr: Number(s.getAttribute('width')), rendered: Math.round(s.getBoundingClientRect().width) })).filter((s) => Math.abs(s.attr - s.rendered) > 2);
    const overflowing = [...view.querySelectorAll('.sa-chart')].filter((c) => visible(c) && !c.classList.contains('sa-heat') && c.scrollWidth > c.clientWidth + 1).map((c) => c.id);
    const positioned = innerWidth <= 1180 ? [...view.querySelectorAll('*')].filter((el) => ['sticky', 'fixed'].includes(getComputedStyle(el).position)).map((el) => el.id || el.className) : [];
    const gestures = [...view.querySelectorAll('.sa-svg, .sa-heat-scroll, .sa-chart')].map((el) => getComputedStyle(el).touchAction)
      .filter((t) => !(t === 'auto' || t === 'manipulation' || t.includes('pinch-zoom')));
    const stretched = view.querySelectorAll('svg[preserveAspectRatio="none"]').length;
    // Labels inside one chart never sit on top of each other, and none is
    // cut off by the edge of its chart.
    const collisions = [];
    const clipped = [];
    for (const svg of view.querySelectorAll('svg.sa-svg')) {
      const frame = svg.getBoundingClientRect();
      const boxes = [...svg.querySelectorAll('text')].filter((t) => t.textContent.trim() && visible(t)).map((t) => ({ text: t.textContent, r: t.getBoundingClientRect() }));
      for (const { text, r } of boxes) {
        if (r.left < frame.left - 1 || r.right > frame.right + 1 || r.top < frame.top - 1 || r.bottom > frame.bottom + 1) clipped.push({ chart: svg.closest('.sa-chart')?.id, text: text.slice(0, 24) });
      }
      for (let i = 0; i < boxes.length; i += 1) {
        for (let j = i + 1; j < boxes.length; j += 1) {
          const a = boxes[i].r, b = boxes[j].r;
          const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
          const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
          // Glyph boxes include line spacing; a real collision overlaps both ways.
          if (w > 1 && h > Math.min(a.height, b.height) * 0.45) collisions.push({ chart: svg.closest('.sa-chart')?.id, a: boxes[i].text, b: boxes[j].text });
        }
      }
    }
    return {
      docOverflow: document.documentElement.scrollWidth - innerWidth,
      areaOverflow: area.scrollWidth - area.clientWidth,
      viewOverflow: view.scrollWidth - view.clientWidth,
      overflowing, smallText, htmlText, scaled, positioned, gestures, stretched, collisions, clipped,
      charts: charts.filter((id) => { const el = document.getElementById(id); return el && visible(el) && el.querySelector('svg'); }),
    };
  }, CHARTS);
}

async function controlSizes(page) {
  return page.evaluate(() => {
    const view = document.getElementById('session-analysis');
    const details = [...view.querySelectorAll('details')];
    const wasOpen = details.map((d) => d.open);
    details.forEach((d) => { d.open = true; });
    const visible = (el) => { const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
    const small = [...view.querySelectorAll('button, select, input, summary, a[href]')].filter(visible).map((el) => {
      const target = el.type === 'checkbox' ? el.closest('label') : el;
      const r = target.getBoundingClientRect();
      return { control: el.id || el.className || el.tagName, text: (el.textContent || '').trim().slice(0, 24), width: Math.round(r.width), height: Math.round(r.height) };
    }).filter((c) => c.height < 43.5 || c.width < 43.5);
    details.forEach((d, i) => { d.open = wasOpen[i]; });
    return small;
  });
}

// The gesture starts on the chart (that decides who handles it) and runs
// 160 px, past the chart's edge for a short chart.
async function verticalSwipe(page, cdp, id) {
  await reveal(page, `#${id}`);
  const spot = await page.evaluate((chartId) => {
    const el = document.getElementById(chartId);
    const area = document.getElementById('analysis');
    const r = el.getBoundingClientRect();
    const a = area.getBoundingClientRect();
    const start = (Math.max(r.top, a.top) + Math.min(r.bottom, a.bottom)) / 2;
    const down = area.scrollHeight - area.clientHeight - area.scrollTop < 200;
    const to = down ? Math.min(a.bottom - 4, start + 160) : Math.max(a.top + 4, start - 160);
    return { x: r.left + r.width * 0.6, from: start, to, before: area.scrollTop, onChart: start > r.top && start < r.bottom };
  }, id);
  await swipe(page, cdp, { x: spot.x, y: spot.from }, { x: spot.x, y: spot.to });
  const after = await page.evaluate(() => document.getElementById('analysis').scrollTop);
  return { ...spot, after, moved: spot.onChart ? Math.abs(after - spot.before) : 0 };
}

(async () => {
  cssGuards();
  const sets = payloads();
  if (shots) fs.mkdirSync(shots, { recursive: true });
  const browser = await chromium.launch({ headless: true, ...(process.env.PITBOX_BROWSER_PATH ? { executablePath: process.env.PITBOX_BROWSER_PATH } : {}) });
  const results = [];
  const failures = [];
  try {
    for (const viewport of VIEWPORTS) {
      const label = `${viewport.width}x${viewport.height}`;
      const { context, page, problems } = await openPage(browser, viewport, sets);
      const cdp = await context.newCDPSession(page);
      const check = (set, name, pass, detail = {}) => {
        const row = { viewport: label, payload: set.name, check: name, passed: Boolean(pass), ...detail };
        results.push(row);
        if (!pass) failures.push(row);
      };
      try {
        for (const [index, set] of sets.entries()) {
          const sessionId = String(set.payload.session_id);
          if (index > 0) await page.evaluate((id) => window.dispatchEvent(new CustomEvent('pitwall:analyze-session', { detail: { sessionId: id } })), sessionId);
          await waitForAnalysis(page, sessionId);
          await page.evaluate(() => { document.getElementById('analysis').scrollTop = 0; window.__opened.length = 0; });
          const g = await geometry(page);
          check(set, 'no-page-horizontal-overflow', g.docOverflow <= 1 && g.areaOverflow <= 1 && g.viewOverflow <= 1, { doc: g.docOverflow, area: g.areaOverflow, view: g.viewOverflow });
          check(set, 'charts-fit-their-cards', !g.overflowing.length, { overflowing: g.overflowing });
          check(set, 'svg-drawn-at-container-width', !g.scaled.length, { scaled: g.scaled.slice(0, 5) });
          check(set, 'svg-text-at-least-11px', !g.smallText.length, { small: g.smallText.slice(0, 8) });
          check(set, 'html-text-at-least-11px', !g.htmlText.length, { small: g.htmlText.slice(0, 8) });
          check(set, 'no-sticky-or-fixed', !g.positioned.length, { positioned: g.positioned });
          check(set, 'gestures-keep-pan-and-zoom', !g.gestures.length && !g.stretched, { gestures: g.gestures, stretched: g.stretched });
          check(set, 'chart-labels-do-not-collide', !g.collisions.length, { collisions: g.collisions.slice(0, 6) });
          check(set, 'chart-labels-inside-their-chart', !g.clipped.length, { clipped: g.clipped.slice(0, 6) });
          const expected = set.payload.session?.is_race ? CHARTS.length : CHARTS.length - 3;
          check(set, 'every-chart-drawn', g.charts.length === expected, { drawn: g.charts });
          if (shots) await page.screenshot({ path: path.join(shots, `session-analysis-${set.name}-${label}.png`), scale: 'css' });
          if (shots && (process.env.PITBOX_SHOTS_CARDS || '').split(',').includes(set.name)) {
            const cards = await page.locator('#session-analysis .sa-card:visible, #session-analysis .sa-header, #session-analysis .sa-filters, #session-analysis .sa-kpis').all();
            for (const [i, card] of cards.entries()) await card.screenshot({ path: path.join(shots, `card-${set.name}-${label}-${String(i).padStart(2, '0')}.png`), scale: 'css' });
          }
          // Swipes start on each chart and scroll the page; none opens a lap.
          for (const id of g.charts) {
            const gesture = await verticalSwipe(page, cdp, id);
            check(set, `touch-scroll-${id}`, gesture.moved > 10, gesture);
          }
          // A heatmap wider than its card scrolls sideways under a finger; at
          // 390 px the 60-lap race must be such a case.
          await reveal(page, '#analysisHeatmapChart');
          const strip = await page.evaluate(() => {
            const scroller = document.querySelector('#analysisHeatmapChart .sa-heat-scroll');
            const r = scroller.getBoundingClientRect();
            return { x: r.left + r.width * 0.8, y: r.top + Math.min(40, r.height / 3), width: r.width, before: scroller.scrollLeft, room: scroller.scrollWidth - scroller.clientWidth };
          });
          if (viewport.width === 390 && set.name === 'large-race') check(set, 'heatmap-wider-than-a-phone', strip.room > 20, strip);
          if (strip.room > 20) {
            await swipe(page, cdp, { x: strip.x, y: strip.y }, { x: strip.x - Math.min(160, strip.width * 0.6), y: strip.y });
            const after = await page.evaluate(() => document.querySelector('#analysisHeatmapChart .sa-heat-scroll').scrollLeft);
            check(set, 'heatmap-swipes-sideways', after > strip.before + 10, { before: strip.before, after, room: strip.room });
          }
          check(set, 'swipes-never-open-a-lap', (await page.evaluate(() => window.__opened.length)) === 0);
          // A tap on a visible heatmap cell reads it at the mark; a second tap opens it.
          await reveal(page, '#analysisHeatmapChart');
          const cell = await page.evaluate(() => {
            const scroller = document.querySelector('#analysisHeatmapChart .sa-heat-scroll');
            const box = scroller.getBoundingClientRect();
            const cells = [...scroller.querySelectorAll('rect[data-lap-id]')].map((c) => ({ c, r: c.getBoundingClientRect() }))
              .filter(({ r }) => r.left >= box.left + 2 && r.right <= box.right - 2 && r.top >= 0 && r.bottom <= innerHeight);
            const pick = cells[Math.floor(cells.length / 2)];
            return pick ? { x: pick.r.left + pick.r.width / 2, y: pick.r.top + pick.r.height / 2, top: pick.r.top, bottom: pick.r.bottom, key: pick.c.getAttribute('data-key') } : null;
          });
          if (cell) {
            await page.touchscreen.tap(cell.x, cell.y);
            await page.waitForTimeout(80);
            const tip = await page.evaluate(() => {
              const t = document.querySelector('#analysisHeatmapChart > .sa-tip');
              if (!t || t.hidden) return null;
              const r = t.getBoundingClientRect();
              return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, text: t.textContent };
            });
            check(set, 'tap-reads-a-cell-at-the-mark', Boolean(tip) && tip.left >= 0 && tip.right <= viewport.width && tip.left <= cell.x && cell.x <= tip.right && (tip.bottom <= cell.top + 1 || tip.top >= cell.bottom - 1), { tip, cell });
            check(set, 'first-tap-does-not-open', (await page.evaluate(() => window.__opened.length)) === 0);
            await page.touchscreen.tap(cell.x, cell.y);
            await page.waitForTimeout(80);
            check(set, 'second-tap-opens-lap-lab', (await page.evaluate(() => window.__opened.length)) === 1);
          } else check(set, 'tap-reads-a-cell-at-the-mark', false, { note: 'no visible traced cell' });
          // A mouse hover reads a box at the mark.
          if (viewport.width === 1440) {
            await reveal(page, '#analysisPaceChart');
            const box = await page.evaluate(() => {
              const mark = document.querySelector('#analysisPaceChart [data-key^="box-"] rect');
              const r = mark.getBoundingClientRect();
              return { x: r.left + r.width / 2, y: r.top + r.height / 2, top: r.top };
            });
            await page.mouse.move(box.x, box.y);
            await page.waitForTimeout(60);
            const tip = await page.evaluate(() => { const t = document.querySelector('#analysisPaceChart > .sa-tip'); if (!t || t.hidden) return null; const r = t.getBoundingClientRect(); return { left: r.left, right: r.right, bottom: r.bottom, live: t.getAttribute('aria-live') }; });
            check(set, 'hover-reads-a-box-at-the-mark', Boolean(tip) && tip.left <= box.x && box.x <= tip.right && tip.bottom <= box.top + 1 && tip.live === 'off', { tip, box });
            await page.mouse.move(2, 2);
          }
          await readingChecks(page, cdp, set, viewport, check, g.charts);
          const small = await controlSizes(page);
          check(set, 'controls-at-least-44px', !small.length, { small: small.slice(0, 8) });
        }
        // Rotation redraws at the new width without a reload.
        if (viewport.width === 390) {
          await page.setViewportSize({ width: 844, height: 390 });
          await page.waitForTimeout(300);
          const rotated = await page.evaluate(() => {
            const chart = document.getElementById('analysisPaceChart');
            return { svg: Number(chart.querySelector('svg')?.getAttribute('width')), box: Math.floor(chart.clientWidth) };
          });
          check(sets[sets.length - 1], 'rotation-redraws-at-the-new-width', Math.abs(rotated.svg - rotated.box) <= 1, rotated);
          await page.setViewportSize({ width: viewport.width, height: viewport.height });
          await page.waitForTimeout(300);
        }
        const errors = [...problems, ...(await page.evaluate(() => window.__errors))];
        check(sets[0], 'no-console-or-page-errors', !errors.length, { errors: errors.slice(0, 5) });
      } finally { await context.close(); }
    }
  } finally { await browser.close(); }
  const summary = { result: failures.length ? 'failed' : 'passed', checks: results.length, failures, screenshots: shots };
  console.log(JSON.stringify(summary, null, 2));
  assert.equal(failures.length, 0, `${failures.length} layout checks failed`);
})().catch((error) => { console.error(error); process.exitCode = 1; });
