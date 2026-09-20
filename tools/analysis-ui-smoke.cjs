/* Shipped Analysis markup/module with deterministic API fixtures. No user DB,
   network, mic, or tablet. Canvas calls are inspected; layout needs browser QA. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
const moduleText = fs.readFileSync(path.join(root, 'static/js/workspaces.js'), 'utf8').replace(/^export \{[^\n]+\};?\s*$/m, '');
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve)); };
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; };
const response = (payload, status = 200) => ({ ok: status < 400, status, statusText: 'fixture', text: async () => JSON.stringify(payload) });
const lap = (id, valid = true) => ({ id, valid, lap_number: id === 'a' ? 1 : 2, car_index: 0, display_name: 'Driver', coverage_ratio: 1, lap_time_ms: 90000, tyre_compound: 'SOFT' });
const session = id => ({ id, display_name: `Session ${id}`, status: 'complete', participants: [], session_type: 'Race' });
function trace(id, speed = 50, world = true) {
  const series = {};
  const values = { speed: [speed, speed + 1, speed + 2, speed + 3], gear: [3, 4, 5, 6], brake: [0, .6, 0, 0], throttle: [1, 0, .5, 1], steering: [.1, -.2, .1, 0], world_x: [0, 20, 20, 0], world_z: [0, 0, 20, 0] };
  for (const [name, samples] of Object.entries(values)) {
    const unavailable = !world && name.startsWith('world_');
    series[name] = { unit: name === 'speed' ? 'm/s' : 'ratio', values: unavailable ? samples.map(() => null) : samples, availability: unavailable ? 'unavailable' : 'observed', coverage: unavailable ? 0 : 1 };
  }
  return { lap_id: id, axis: { name: 'distance', unit: 'm', values: [0, 10, 30, 50] }, series, coverage: 1, source: 'fixture' };
}
const comparison = { comparison_id: 'cmp1', candidate: { lap_id: 'a', session_id: 's1', lap_number: 1, lap_time_ms: 90000 }, reference: { lap_id: 'b', lap_number: 2, lap_time_ms: 89000 }, compatibility: { class: 'strict', allows_coaching: true }, lap_delta_s: 1, coverage_ratio: 1, algorithm_bundle: 'fixture', findings: [], segments: [] };
const pairTrace = { axis: trace('a').axis, candidate: { series: trace('a', 70).series }, reference: { series: trace('b', 60).series } };

async function harness(overrides = {}) {
  const dom = new JSDOM(html, { url: 'http://127.0.0.1:8769/#live', runScripts: 'outside-only', pretendToBeVisual: true });
  const w = dom.window, calls = [], id = name => w.document.getElementById(name), contexts = new Map();
  w.HTMLCanvasElement.prototype.getContext = function () {
    if (!contexts.has(this.id)) {
      const calls = [];
      contexts.set(this.id, new Proxy({ calls, clearRect() { calls.length = 0; } }, { get(target, key) { if (!(key in target)) target[key] = (...args) => calls.push([key, ...args]); return target[key]; } }));
    }
    return contexts.get(this.id);
  };
  const timers = new Map(); let timerId = 0;
  w.setInterval = callback => { timers.set(++timerId, callback); return timerId; };
  w.clearInterval = timer => timers.delete(timer);
  w.confirm = () => true;
  const route = async (url, options = {}) => {
    const u = new URL(url, 'http://localhost');
    if (u.pathname === '/api/v1/sessions') return response({ items: [session('s1'), session('s2')] });
    if (u.pathname === '/api/v1/storage/status') return response({});
    const sessionMatch = u.pathname.match(/^\/api\/v1\/sessions\/(s[12])(?:\/(.+))?$/);
    if (sessionMatch) {
      const [, name, view] = sessionMatch;
      if (!view) return response({ session: session(name) });
      if (view === 'quality') return response({ quality_score: 1, laps: { total: 2, valid: 2 } });
      if (view === 'laps') return response({ items: name === 's1' ? [lap('a'), lap('b'), lap('invalid', false)] : [lap('c')] });
      if (view === 'field') return response({ classification: [], cars_observed: 1 });
    }
    const lapMatch = u.pathname.match(/^\/api\/v1\/laps\/([^/]+)\/(trace|references|analysis)$/);
    if (lapMatch) {
      const [, name, view] = lapMatch;
      if (view === 'trace') return response(trace(name, name === 'c' ? 20 : 50));
      if (view === 'references') return response({ items: name === 'a' ? [{ lap_id: 'b', suggested: true, compatibility: { class: 'strict' } }] : [] });
      return response({ lap_id: name, lap_number: 1, top_speed_kph: 180, minimum_speed_kph: 50, segments: [], trace_source: 'fixture' });
    }
    if (u.pathname === '/api/v1/comparisons' && options.method === 'POST') return response(comparison);
    if (u.pathname === '/api/v1/comparisons/cmp1/trace') return response(pairTrace);
    throw new Error(`Unexpected API request: ${url}`);
  };
  w.fetch = async (url, options = {}) => {
    calls.push({ url, options });
    const handler = overrides[new URL(url, 'http://localhost').pathname];
    return handler ? handler(url, options, route) : route(url, options);
  };
  // Use the real tab selectors; do not execute the live telemetry bootstrap.
  w.eval("const ANALYSIS_VIEWS=['library','session-review','lap-lab','field','review']; function loadTracks(){} function loadHistory(){} function loadAppSettings(){}\n" +
    ['selectPage', 'selectAnalysisView'].map(name => html.match(new RegExp(`^function ${name}\\([^\\n]+`, 'm'))[0]).join('\n'));
  w.document.querySelectorAll('.tab').forEach(button => { button.onclick = () => w.selectPage(button.dataset.page); });
  w.document.querySelectorAll('.analysis-subnav .field-tab').forEach(button => { button.onclick = () => w.selectAnalysisView(button.dataset.page); });
  w.eval(moduleText);
  await settle();
  return { w, id, calls, contexts, timers, api: w.pitwallWorkspaces, close: () => dom.window.close() };
}

test('Review row opens single-lap playback, controls and solo analysis without Compare', async () => {
  const h = await harness();
  try {
    h.id('tab-analysis').click(); await settle();
    [...h.id('libraryRows').querySelectorAll('button')].find(button => button.textContent === 'Review').click(); await settle();
    assert.equal(h.id('session-review').hidden, false);
    h.id('sessionLapRows').querySelector('button').click(); await settle();
    assert.equal(h.id('lap-lab').hidden, false);
    assert.equal(h.id('playbackToggle').disabled, false);
    assert.equal(h.id('analyzeLapAlone').disabled, false);
    assert.equal(h.id('gaugeSpeed').textContent, '180.0 km/h');
    assert.ok(h.contexts.get('comparisonMap').calls.some(call => call[0] === 'arc'));
    assert.equal(h.calls.some(call => call.options.method === 'POST'), false);
    h.id('playbackNext').click();
    assert.equal(h.id('playbackDistance').textContent, '10 m');
    assert.equal(h.id('gaugeGear').textContent, '4');
    h.id('analyzeLapAlone').click(); await settle();
    assert.equal(h.id('soloAnalysisPane').hidden, false);
    h.id('playbackToggle').click(); assert.equal(h.timers.size, 1);
    [...h.timers.values()][0](); assert.equal(h.id('playbackRange').value, '3');
    [...h.timers.values()][0](); assert.equal(h.timers.size, 0);
    h.id('playbackToggle').click(); assert.equal(h.id('playbackRange').value, '0', 'Play at the end restarts');
  } finally { h.close(); }
});

test('One-lap session without references still plays and analyzes', async () => {
  const h = await harness();
  try {
    await h.api.selectSession('s2'); await h.api.openLap('c');
    assert.equal(h.id('referenceLapSelect').disabled, true);
    assert.equal(h.id('createComparison').disabled, true);
    assert.equal(h.id('playbackToggle').disabled, false);
    assert.equal(h.id('analyzeLapAlone').disabled, false);
    assert.equal(h.id('gaugeSpeed').textContent, '72.0 km/h');
    assert.equal(h.id('gaugeDelta').textContent, 'Unavailable');
    h.id('trace-tab-delta').click();
    assert.ok(h.contexts.get('comparisonTrace').calls.some(call => call[0] === 'fillText' && /reference/.test(call[1])));
  } finally { h.close(); }
});

test('Repeated distance controls advance across sparse samples and stop at endpoints', async () => {
  const h = await harness();
  try {
    await h.api.selectSession('s2'); await h.api.openLap('c');
    for (const distance of [10, 30, 50, 50]) {
      h.id('playbackNext').click();
      assert.equal(h.id('playbackDistance').textContent, `${distance} m`);
    }
    for (const distance of [30, 10, 0, 0]) {
      h.id('playbackPrevious').click();
      assert.equal(h.id('playbackDistance').textContent, `${distance} m`);
    }
    assert.match(h.id('playbackNext').textContent, /≈/);
    assert.match(h.id('playbackPrevious').title, /recorded sample/);
  } finally { h.close(); }
});

test('Comparison keeps both traces and optional map failure cannot erase telemetry', async () => {
  let failReferenceMap = false;
  const h = await harness({ '/api/v1/laps/b/trace': (url, options, route) => failReferenceMap ? response({ detail: 'Map missing' }, 409) : route(url, options) });
  try {
    await h.api.selectSession('s1'); await h.api.openLap('a');
    await h.api.createComparison();
    assert.equal(h.id('gaugeSpeed').textContent, '252.0 km/h');
    assert.equal(h.id('comparisonDelta').textContent, '+1.000 s');
    assert.equal(h.contexts.get('comparisonMap').calls.filter(call => call[0] === 'arc').length, 2);
    failReferenceMap = true;
    await h.api.createComparison();
    assert.equal(h.id('gaugeSpeed').textContent, '252.0 km/h');
    assert.equal(h.id('playbackToggle').disabled, false);
    assert.equal(h.contexts.get('comparisonMap').calls.filter(call => call[0] === 'arc').length, 1);
  } finally { h.close(); }
});

test('Missing world positions are honest unavailable but inputs and trace remain usable', async () => {
  const h = await harness({ '/api/v1/laps/c/trace': () => response(trace('c', 20, false)) });
  try {
    await h.api.selectSession('s2'); await h.api.openLap('c');
    assert.equal(h.id('playbackToggle').disabled, false);
    assert.equal(h.id('gaugeSpeed').textContent, '72.0 km/h');
    const commands = h.contexts.get('comparisonMap').calls;
    assert.equal(commands.some(call => call[0] === 'arc'), false);
    assert.ok(commands.some(call => call[0] === 'fillText' && /unavailable/.test(call[1])));
    assert.ok(h.contexts.get('comparisonTrace').calls.some(call => call[0] === 'lineTo'));
  } finally { h.close(); }
});

test('Partial geometry breaks the track line and hides markers inside missing intervals', async () => {
  const partial = trace('c', 20);
  partial.series.world_x.values = [0, 20, null, 40];
  partial.series.world_z.values = [0, 0, null, 20];
  const h = await harness({ '/api/v1/laps/c/trace': () => response(partial) });
  try {
    await h.api.selectSession('s2'); await h.api.openLap('c');
    let commands = h.contexts.get('comparisonMap').calls;
    assert.equal(commands.filter(call => call[0] === 'lineTo').length, 1, 'Only adjacent recorded samples are joined');
    assert.equal(commands.filter(call => call[0] === 'moveTo').length, 2, 'The sample after the gap starts a new path');
    assert.equal(commands.filter(call => call[0] === 'arc').length, 1);
    h.id('playbackRange').value = '2';
    h.id('playbackRange').dispatchEvent(new h.w.Event('input'));
    commands = h.contexts.get('comparisonMap').calls;
    assert.equal(commands.some(call => call[0] === 'arc'), false, 'No false car position at a missing sample');
    assert.ok(commands.some(call => call[0] === 'fillText' && /position unavailable here/.test(call[1])));
    assert.equal(h.id('gaugeSpeed').textContent, '79.2 km/h', 'Recorded telemetry remains usable inside the geometry gap');
    h.id('playbackRange').value = '3';
    h.id('playbackRange').dispatchEvent(new h.w.Event('input'));
    assert.equal(h.contexts.get('comparisonMap').calls.filter(call => call[0] === 'arc').length, 1, 'An exact observed endpoint remains usable');
  } finally { h.close(); }
});

test('Aligned cursor does not snap to valid map endpoints across gaps or beyond coverage', async () => {
  const partial = trace('a');
  partial.series.world_x.values = [0, null, 20, 40];
  partial.series.world_z.values = [0, null, 0, 20];
  const aligned = { ...pairTrace, axis: { name: 'distance', unit: 'm', values: [0, 5, 20, 40, 60] } };
  const h = await harness({
    '/api/v1/laps/a/trace': () => response(partial),
    '/api/v1/comparisons/cmp1/trace': () => response(aligned),
  });
  try {
    await h.api.selectSession('s1'); await h.api.openLap('a'); await h.api.createComparison();
    const cursor = index => { h.id('playbackRange').value = String(index); h.id('playbackRange').dispatchEvent(new h.w.Event('input')); };
    cursor(1);
    let commands = h.contexts.get('comparisonMap').calls;
    assert.equal(commands.filter(call => call[0] === 'arc').length, 1, 'Only reference marker has adjacent observed positions at 5 m');
    assert.ok(commands.some(call => call[0] === 'fillText' && call[1] === 'Candidate position unavailable here'));
    cursor(2);
    assert.equal(h.contexts.get('comparisonMap').calls.filter(call => call[0] === 'arc').length, 1, 'Candidate stays hidden until its recording resumes');
    cursor(3);
    assert.equal(h.contexts.get('comparisonMap').calls.filter(call => call[0] === 'arc').length, 2, 'Adjacent valid map samples support interpolated cursor positions');
    cursor(4);
    assert.equal(h.contexts.get('comparisonMap').calls.filter(call => call[0] === 'arc').length, 0, 'No extrapolated markers beyond either lap map');
  } finally { h.close(); }
});

test('Candidate dropdown clears comparison and allows recorded invalid-lap playback', async () => {
  const h = await harness();
  try {
    await h.api.selectSession('s1'); await h.api.openLap('a'); await h.api.createComparison();
    h.id('playbackToggle').click();
    h.id('candidateLapSelect').value = 'invalid';
    h.id('candidateLapSelect').dispatchEvent(new h.w.Event('change')); await settle();
    assert.equal(h.timers.size, 0);
    assert.equal(h.id('comparisonDelta').textContent, 'Unavailable');
    assert.equal(h.id('playbackToggle').disabled, false);
    assert.equal(h.id('analyzeLapAlone').disabled, false);
    assert.equal(h.id('candidateLapSelect').selectedOptions[0].disabled, false);
  } finally { h.close(); }
});

test('Session switch immediately clears playback, references, maps, solo pane and old requests', async () => {
  const slow = deferred();
  const h = await harness({ '/api/v1/sessions/s2': () => slow.promise });
  try {
    await h.api.selectSession('s1'); await h.api.openLap('a'); await h.api.createComparison();
    h.id('analyzeLapAlone').click(); await settle(); h.id('playbackToggle').click();
    const next = h.api.selectSession('s2');
    assert.equal(h.id('playbackToggle').disabled, true);
    assert.equal(h.timers.size, 0);
    assert.equal(h.id('gaugeSpeed').textContent, 'Unavailable');
    assert.equal(h.id('candidateLapSelect').value, '');
    assert.equal(h.id('soloAnalysisPane').hidden, true);
    assert.equal(h.contexts.get('comparisonMap').calls.some(call => call[0] === 'arc'), false);
    slow.resolve(response({ session: session('s2') })); await next;
  } finally { h.close(); }
});

test('Out-of-order lap/reference and failed responses cannot overwrite new lap', async () => {
  const slowTrace = deferred(), slowReferences = deferred();
  const h = await harness({ '/api/v1/laps/a/trace': () => slowTrace.promise, '/api/v1/laps/a/references': () => slowReferences.promise });
  try {
    await h.api.selectSession('s1'); const old = h.api.openLap('a');
    await h.api.selectSession('s2'); await h.api.openLap('c');
    slowTrace.resolve(response(trace('a', 99))); slowReferences.resolve(response({ detail: 'Old request failed' }, 500)); await old;
    assert.equal(h.id('gaugeSpeed').textContent, '72.0 km/h');
    assert.equal(h.id('candidateLapSelect').value, 'c');
    assert.match(h.id('lapLabStatus').textContent, /playback ready/);
    assert.doesNotMatch(h.id('referenceLapMeta').textContent, /Old request/);
  } finally { h.close(); }
});

test('Late comparison and solo analysis cannot repopulate a different selection', async () => {
  const slowCompare = deferred(), slowSolo = deferred();
  const h = await harness({ '/api/v1/comparisons': () => slowCompare.promise, '/api/v1/laps/a/analysis': () => slowSolo.promise });
  try {
    await h.api.selectSession('s1'); await h.api.openLap('a');
    const old = h.api.createComparison(); h.id('analyzeLapAlone').click();
    await h.api.selectSession('s2'); await h.api.openLap('c');
    slowCompare.resolve(response(comparison)); slowSolo.resolve(response({ lap_number: 1, segments: [] })); await old; await settle();
    assert.equal(h.id('gaugeSpeed').textContent, '72.0 km/h');
    assert.equal(h.id('comparisonDelta').textContent, 'Unavailable');
    assert.equal(h.id('soloAnalysisPane').hidden, true);
    assert.equal(h.calls.some(call => call.url.includes('/comparisons/cmp1/trace')), false);
  } finally { h.close(); }
});

test('Failed lap trace clears old data; solo summary failure does not break raw playback', async () => {
  const h = await harness({ '/api/v1/laps/b/trace': () => response({ detail: 'Trace missing' }, 409), '/api/v1/laps/a/analysis': () => response({ detail: 'Summary missing' }, 409) });
  try {
    await h.api.selectSession('s1'); await h.api.openLap('a');
    h.id('analyzeLapAlone').click(); await settle();
    assert.equal(h.id('playbackToggle').disabled, false);
    assert.equal(h.id('gaugeSpeed').textContent, '180.0 km/h');
    await h.api.openLap('b');
    assert.equal(h.id('playbackToggle').disabled, true);
    assert.equal(h.id('gaugeSpeed').textContent, 'Unavailable');
    assert.match(h.id('lapLabStatus').textContent, /Trace missing/);
    assert.equal(h.id('soloAnalysisPane').hidden, true);
  } finally { h.close(); }
});

test('Out-of-order session responses and failures cannot restore stale session', async () => {
  const slow = deferred();
  const h = await harness({ '/api/v1/sessions/s1': () => slow.promise });
  try {
    const old = h.api.selectSession('s1'); await h.api.selectSession('s2');
    slow.resolve(response({ detail: 'Old session failed' }, 500)); await old;
    assert.equal(h.id('sessionReviewTitle').textContent, 'Session s2');
    assert.doesNotMatch(h.id('sessionReviewStatus').textContent, /Old session failed/);
    assert.ok([...h.id('candidateLapSelect').options].some(option => option.value === 'c'));
  } finally { h.close(); }
});

test('Partial solo measurements show unavailable, never fabricated zero or null units', async () => {
  const h = await harness({ '/api/v1/laps/a/analysis': () => response({
    lap_number: 1, top_speed_kph: null, minimum_speed_kph: null,
    braking_events: null, full_throttle_pct: null, braking_pct: null,
    metric_coverage: { speed: 0, throttle: .5, brake: 0 },
    segments: [{ label: 'Sector 1', start_m: 0, end_m: 50, time_s: null, entry_speed_kph: null, minimum_speed_kph: null, exit_speed_kph: null }],
  }) });
  try {
    await h.api.selectSession('s1'); await h.api.openLap('a');
    h.id('analyzeLapAlone').click(); await settle();
    assert.equal(h.id('soloAnalysisPane').hidden, false);
    assert.doesNotMatch(h.id('soloAnalysisPane').textContent, /null|0 braking events/);
    assert.match(h.id('soloAnalysisSummary').textContent, /Braking events unavailable/);
    assert.match(h.id('soloAnalysisRows').textContent, /Unavailable/);
    assert.match(h.id('lapLabStatus').textContent, /Partial recording/);
    assert.equal(h.id('gaugeSpeed').textContent, '180.0 km/h');
    assert.equal(h.id('playbackToggle').disabled, false);
  } finally { h.close(); }
});
