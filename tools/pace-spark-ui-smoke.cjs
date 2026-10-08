/* Exercise the real Drive markup, styles and pace renderer without a database. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require('playwright');

const root = path.resolve(__dirname, '..');
const output = path.resolve(process.env.PITBOX_PACE_EVIDENCE || path.join(root, '.codex-ui-test-data/pace-spark'));
const html = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
const start = html.indexOf('function drawPace(');
const renderer = html.slice(start, html.indexOf('\nfunction ', start + 1));
assert.ok(start > 0 && renderer.length > 100);
const carStart = html.indexOf('function renderLiveCar(');
const carRenderer = html.slice(carStart, html.indexOf('function renderRaceControl(', carStart));
const damageStart = html.indexOf('function damageText(');
const damageRenderer = html.slice(damageStart, html.indexOf('\nfunction ', damageStart + 1));
const restartRenderer = html.slice(html.indexOf('function redFlagRestartDisplay('), html.indexOf('function renderTyreDegradation('));
const strategyRenderer = html.slice(html.indexOf('function renderStrategy38('), html.indexOf('/* ---- Pre-race plan panel'));
const strategyModule = fs.readFileSync(path.join(root, 'static/js/strategy.js'), 'utf8');

(async () => {
  fs.mkdirSync(output, { recursive: true });
  const browser = await chromium.launch({ headless: true,
    ...(process.env.PITBOX_BROWSER_CHANNEL ? { channel: process.env.PITBOX_BROWSER_CHANNEL } : {}) });
  const results = [];
  try {
    for (const [width, height, deviceScaleFactor] of [[390, 844, 1], [800, 1280, 1], [390, 844, 2]]) {
      const context = await browser.newContext({ viewport: { width, height }, deviceScaleFactor });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin !== 'http://pitbox.test') return route.abort();
        // Exclude application startup and services; the actual markup, CSS
        // cascade and production renderer are the subject of this regression.
        if (url.pathname === '/') return route.fulfill({ contentType: 'text/html',
          body: html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/gi, '') });
        const file = path.resolve(root, '.' + url.pathname);
        if (!file.startsWith(path.join(root, 'static') + path.sep) || !fs.existsSync(file)) return route.abort();
        return route.fulfill({ path: file });
      });
      await page.goto('http://pitbox.test/');
      await page.evaluate(() => document.getElementById('bootOverlay')?.remove());
      await page.addScriptTag({ content: `const $ = id => document.getElementById(id); const esc = v => String(v).replaceAll('&','&amp;').replaceAll('<','&lt;');\n${renderer}\n${damageRenderer}\n${carRenderer}\n${restartRenderer}\n${strategyRenderer}` });
      await page.addScriptTag({ type: 'module', content: `${strategyModule}\nwindow.strategyQA={renderCall,renderPlans,drawTimeline};` });
      await page.waitForFunction(() => window.strategyQA);
      for (const [scenario, laps] of [['empty', []], ['recorded', [90500, 90300, 90100, 90200, 89900]]]) {
        await page.evaluate(values => drawPace(values.map(lap_time_ms => ({ valid: true, lap_time_ms })), values.length ? 90000 : 0), laps);
        await page.locator('#paceCard').scrollIntoViewIfNeeded();
        const dimensions = await page.locator('#paceSpark').evaluate(canvas => {
          const bounds = canvas.getBoundingClientRect();
          const parent = canvas.parentElement.getBoundingClientRect();
          return { width: bounds.width, height: bounds.height, cardWidth: parent.width,
            backingWidth: canvas.width, backingHeight: canvas.height,
            clientWidth: canvas.clientWidth, clientHeight: canvas.clientHeight,
            font: canvas.getContext('2d').font,
            aspectRatio: getComputedStyle(canvas).aspectRatio };
        });
        await page.locator('#paceCard').screenshot({ path: path.join(output, `${scenario}-${width}-${deviceScaleFactor}x.png`) });
        results.push({ width, scenario, deviceScaleFactor, ...dimensions, errors });
        assert.equal(dimensions.height, 55, `Pace spark must keep its compact 55px height at ${width}px; got ${dimensions.height}`);
        assert.ok(dimensions.width < dimensions.cardWidth, 'Spark stays inside the padded card');
        assert.equal(dimensions.backingWidth, Math.round(dimensions.clientWidth * deviceScaleFactor), 'Bitmap width follows the rendered width and screen density');
        assert.equal(dimensions.backingHeight, Math.round(dimensions.clientHeight * deviceScaleFactor), 'Bitmap height follows the rendered height and screen density');
        assert.match(dimensions.font, /^12px /, 'Empty-state text remains legible at every width');
        assert.deepEqual(errors, []);
      }
      // Car availability uses an explicit 101000ms renderer clock below, so
      // these observed packet timestamps are one second old, not wall time.
      const current = { connected: true, packet_group_freshness: { '1': 100, '6': 100, '7': 100, '10': 100 },
        tyre: { compound: 'MEDIUM', age_laps: 4, wear: [10, 12, 9, 11], inner_temps_c: [94, 95, 93, 94] },
        fuel_laps_delta: 1.2, ers_pct: 67, speed_kph: 200, gear: 5, weather: 'Clear', rain_next_15_pct: 0,
        weather_forecast: [{ time_offset_min: 15, rain_pct: 0 }] };
      for (const [scenario, state] of [['live', current], ['partial', { ...current, packet_group_freshness: { '1': 100, '6': 100, '7': 100 } }], ['stale', { ...current, connected: false }]]) {
        await page.evaluate(state => renderLiveCar(state, 101000), state);
        const car = await page.evaluate(() => Object.fromEntries(['carDataStatus', 'fl', 'flTemp', 'fuel', 'ers', 'speed', 'damageRow'].map(id => [id, document.getElementById(id).textContent])));
        assert.equal(car.fl, scenario === 'live' ? '10%' : '—');
        assert.equal(car.flTemp, scenario === 'stale' ? '—' : '94°C');
        assert.equal(car.speed, scenario === 'stale' ? '—' : '200 km/h · 5');
        assert.equal(car.fuel, scenario === 'stale' ? '—' : '+1.2 laps');
        assert.equal(car.damageRow, scenario === 'live' ? 'No recorded damage' : 'Damage data unavailable');
        assert.match(car.carDataStatus, scenario === 'live' ? /^Live telemetry$/ : /unavailable/);
        await page.locator('#carCard').screenshot({ path: path.join(output, `car-${scenario}-${width}-${deviceScaleFactor}x.png`) });
        results.push({ width, deviceScaleFactor, scenario: `car-${scenario}`, car, errors });
        assert.deepEqual(errors, []);
      }
      for (const [scenario, weather_forecast, expected] of [
        ['observed-zero', [{ time_offset_min: 15, rain_pct: 0 }], 'Clear · rain at 15 min 0%'],
        ['absent', [], 'Clear · 15-min rain forecast unavailable'],
        ['different-horizon', [{ time_offset_min: 0, rain_pct: 0 }, { time_offset_min: 30, rain_pct: 70 }], 'Clear · 15-min rain forecast unavailable']
      ]) {
        await page.evaluate(state => renderLiveCar(state, 101000), { ...current, weather_forecast });
        const weather = await page.locator('#weather').textContent();
        assert.equal(weather, expected);
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
        await page.locator('#weather').locator('xpath=..').screenshot({ path: path.join(output, `forecast-${scenario}-${width}-${deviceScaleFactor}x.png`) });
        results.push({ width, deviceScaleFactor, scenario: `forecast-${scenario}`, weather, errors });
        assert.deepEqual(errors, []);
      }
      // The restart renderers use their normal wall clock, so deliver this
      // independent frame with current observed packet timestamps.
      const red = { ...current, packet_group_freshness: Object.fromEntries(Object.keys(current.packet_group_freshness).map(id => [id, Date.now() / 1000])),
        race_control_phase: 'red_flag', current_lap: 12, total_laps: 20,
        strategy_intent: { active: true, direction: 'stay_out', intent: 'overcut' },
        strategy: { available: true, confidence: 'medium', recommended: { instruction: 'BOX NOW for HARD', box_lap: 12 },
          red_flag_restart: { active: true, instruction: 'Fit fresh HARD during suspension for the restart.',
            primary: { compound: 'HARD', instruction: 'Fit fresh HARD during suspension; run to the finish.' },
            alternative: { compound: 'MEDIUM', instruction: 'Fit fresh MEDIUM during suspension; stop lap 17 for SOFT.' },
            alternative_reason: 'Medium warms up faster; another stop is needed.' } } };
      await page.evaluate(state => renderStrategy38(state), red);
      assert.match(await page.locator('#strategyMain').textContent(), /Fit fresh HARD during suspension/);
      assert.doesNotMatch(await page.locator('#strategyCard').textContent(), /BOX NOW|staying out|pit entry|rejoin/);
      await page.locator('#strategyCard').screenshot({ path: path.join(output, `restart-drive-${width}-${deviceScaleFactor}x.png`) });
      await page.evaluate(state => {
        document.getElementById('strategy').hidden = false;
        document.getElementById('strategy').classList.add('active');
        document.getElementById('carCard').closest('main').hidden = true;
        document.getElementById('carCard').closest('main').classList.remove('active');
        strategyQA.renderCall(state); strategyQA.renderPlans(state); strategyQA.drawTimeline(state);
        document.getElementById('stratPlansHeading').closest('details').open = true;
      }, red);
      assert.equal(await page.locator('#stratPlanRows button').count(), 0);
      assert.equal(await page.locator('#stratPlanCount').textContent(), '2 restart options');
      assert.match(await page.locator('#stratChange').textContent(), /Alternative:.*MEDIUM/);
      await page.locator('#stratInstruction').evaluate(el => el.closest('section').scrollIntoView());
      await page.locator('#stratInstruction').locator('xpath=..').screenshot({ path: path.join(output, `restart-strategy-${width}-${deviceScaleFactor}x.png`) });
      await page.locator('#stratPlansHeading').locator('xpath=../..').screenshot({ path: path.join(output, `restart-options-${width}-${deviceScaleFactor}x.png`) });
      assert.deepEqual(errors, []);
      results.push({ width, deviceScaleFactor, scenario: 'red-flag-restart', errors });
      await context.close();
    }
  } finally {
    await browser.close();
    fs.writeFileSync(path.join(output, 'results.json'), JSON.stringify(results, null, 2));
  }
  console.log(JSON.stringify({ result: 'passed', cases: results.length, output }, null, 2));
})().catch(error => { console.error(error); process.exitCode = 1; });
