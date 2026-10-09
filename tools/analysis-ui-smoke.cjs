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
      // Solo analysis carries canonical whole-lap coverage independently of
      // the finite-sample fraction in /trace. This fixture is a complete lap.
      return response({ lap_id: name, lap_number: 1, coverage_ratio: 1, top_speed_kph: 180, minimum_speed_kph: 50, segments: [], trace_source: 'fixture' });
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
  w.eval("const ANALYSIS_VIEWS=['test-engineer','library','session-analysis','session-review','lap-lab','field','review']; function loadTracks(){} function loadHistory(){} function loadAppSettings(){}\n" +
    ['selectPage', 'selectAnalysisView'].map(name => html.match(new RegExp(`^function ${name}\\([^\\n]+`, 'm'))[0]).join('\n'));
  w.document.querySelectorAll('.tab').forEach(button => { button.onclick = () => w.selectPage(button.dataset.page); });
  w.document.querySelectorAll('.analysis-subnav .field-tab').forEach(button => { button.onclick = () => w.selectAnalysisView(button.dataset.page); });
  w.eval(moduleText);
  await settle();
  return { w, id, calls, contexts, timers, api: w.pitwallWorkspaces, close: () => dom.window.close() };
}

for (const owners of [[], ['restart-one'], ['restart-one', 'restart-two']]) {
  test(`Deletion confirmation describes retained history for ${owners.length} other recordings and respects cancellation`, async () => {
    const preview = { confirmation_token: 'exact-preview-token', impact: {
      records: { laps: 2, comparisons: 1 }, artifacts: [{ relative_path: 'trace.bin' }],
      retained_shared_legacy_session_ids: owners,
      retained_shared_legacy_tables: owners.length ? { radio_messages: 3, laps: 1 } : {},
    } };
    const h = await harness({ '/api/v1/sessions/s1': (_url, options, route) => options.method === 'DELETE'
      ? response(options.headers?.['X-Pitwall-Delete-Token'] ? { deleted: true } : preview)
      : route(_url, options) });
    try {
      let prompt;
      h.w.confirm = message => { prompt = message; return false; };
      h.id('tab-analysis').click(); await settle();
      const remove = () => [...h.id('libraryRows').querySelectorAll('button')].find(button => button.textContent === 'Delete').click();
      remove(); await settle();
      assert.match(prompt, /This removes 2 laps, 1 comparisons, and 1 linked files/);
      if (owners.length) assert.ok(prompt.includes(`Shared history will be kept for ${owners.length} other recording${owners.length === 1 ? '' : 's'}.`));
      else assert.doesNotMatch(prompt, /Shared history/);
      assert.equal(h.id('libraryStatus').textContent, 'Deletion cancelled.');
      assert.equal(h.calls.filter(call => call.options.headers?.['X-Pitwall-Delete-Token']).length, 0);
      h.w.confirm = () => true;
      remove(); await settle();
      const mutations = h.calls.filter(call => call.options.headers?.['X-Pitwall-Delete-Token']);
      assert.equal(mutations.length, 1);
      assert.equal(mutations[0].options.headers['X-Pitwall-Delete-Token'], preview.confirmation_token);
      assert.equal(h.id('libraryStatus').dataset.tone, 'success');
    } finally { h.close(); }
  });
}

// Since 5.4.0 the suggested reference is compared automatically. Playback,
// gauges, the map and single-lap analysis must not wait for that comparison
// (in 4.12.2 they stayed empty until one was created), and the suggestion is
// posted exactly once.
test('Review row opens playback at once; the suggested reference is compared once without holding playback', async () => {
  const pending = deferred();
  const h = await harness({ '/api/v1/comparisons': (url, options, route) => options.method === 'POST' ? pending.promise : route(url, options) });
  const posts = () => h.calls.filter(call => call.options.method === 'POST');
  try {
    h.id('tab-analysis').click(); await settle();
    [...h.id('libraryRows').querySelectorAll('button')].find(button => button.textContent === 'Review').click(); await settle();
    assert.equal(h.id('session-review').hidden, false);
    h.id('sessionLapRows').querySelector('button').click(); await settle();
    assert.equal(h.id('lap-lab').hidden, false);
    assert.equal(posts().length, 1);
    assert.deepEqual(JSON.parse(posts()[0].options.body).reference, { kind: 'lap', lap_id: 'b' });
    assert.match(h.id('lapLabStatus').textContent, /Aligning laps/);
    assert.equal(h.id('playbackToggle').disabled, false);
    assert.equal(h.id('analyzeLapAlone').disabled, false);
    assert.equal(h.id('gaugeSpeed').textContent, '180.0 km/h');
    assert.ok(h.contexts.get('comparisonMap').calls.some(call => call[0] === 'arc'));
    h.id('playbackNext').click();
    assert.equal(h.id('playbackDistance').textContent, '10 m');
    assert.equal(h.id('gaugeGear').textContent, '4');
    h.id('analyzeLapAlone').click(); await settle();
    assert.equal(h.id('soloAnalysisPane').hidden, false);
    h.id('playbackToggle').click(); assert.equal(h.timers.size, 1);
    [...h.timers.values()][0](); assert.equal(h.id('playbackRange').value, '3');
    [...h.timers.values()][0](); assert.equal(h.timers.size, 0);
    h.id('playbackToggle').click(); assert.equal(h.id('playbackRange').value, '0', 'Play at the end restarts');
    pending.resolve(response(comparison)); await settle();
    assert.match(h.id('lapLabStatus').textContent, /Comparison ready/);
    assert.equal(posts().length, 1);
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
    assert.equal(h.id('lapLabStatus').dataset.tone, 'success');
    const next = h.api.selectSession('s2');
    assert.equal(h.id('playbackToggle').disabled, true);
    assert.equal(h.timers.size, 0);
    assert.equal(h.id('gaugeSpeed').textContent, 'Unavailable');
    assert.equal(h.id('candidateLapSelect').value, '');
    assert.equal(h.id('soloAnalysisPane').hidden, true);
    assert.equal(h.contexts.get('comparisonMap').calls.some(call => call[0] === 'arc'), false);
    assert.equal(h.id('lapLabStatus').textContent, 'Choose a recorded lap to see its playback.');
    assert.equal(h.id('lapLabStatus').dataset.tone, undefined);
    slow.resolve(response({ session: session('s2') })); await next;
    assert.equal(h.id('lapLabStatus').textContent, 'Choose a recorded lap to see its playback.');
    assert.equal(h.id('lapLabStatus').dataset.tone, undefined);
  } finally { h.close(); }
});

test('Clearing the session removes both ready and failed lap notices and disables playback', async () => {
  for (const failed of [false, true]) {
    const h = await harness(failed ? { '/api/v1/laps/a/trace': () => response({ detail: 'Trace missing' }, 409) } : {});
    try {
      await h.api.selectSession('s1'); await h.api.openLap('a');
      assert.equal(h.id('lapLabStatus').dataset.tone, failed ? 'error' : 'success');
      if (!failed) h.id('playbackToggle').click();
      await h.api.selectSession('');
      assert.equal(h.id('lapLabStatus').textContent, 'Choose a recorded lap to see its playback.');
      assert.equal(h.id('lapLabStatus').dataset.tone, undefined);
      assert.equal(h.id('candidateLapSelect').value, '');
      assert.equal(h.id('playbackDistance').textContent, '0 m');
      assert.equal(h.id('gaugeSpeed').textContent, 'Unavailable');
      for (const id of ['playbackToggle', 'playbackPrevious', 'playbackNext', 'playbackRange', 'createComparison', 'analyzeLapAlone']) {
        assert.equal(h.id(id).disabled, true, `${id} disabled after clearing session`);
      }
      assert.equal(h.timers.size, 0);
    } finally { h.close(); }
  }
});

test('Late trace cannot restore a ready notice after switching or clearing the session', async () => {
  for (const nextSession of ['s2', '']) {
    const slowTrace = deferred();
    const h = await harness({ '/api/v1/laps/a/trace': () => slowTrace.promise });
    try {
      await h.api.selectSession('s1');
      const old = h.api.openLap('a');
      assert.match(h.id('lapLabStatus').textContent, /Loading recorded lap/);
      await h.api.selectSession(nextSession);
      assert.equal(h.id('lapLabStatus').textContent, 'Choose a recorded lap to see its playback.');
      slowTrace.resolve(response(trace('a', 99))); await old;
      assert.equal(h.id('lapLabStatus').textContent, 'Choose a recorded lap to see its playback.');
      assert.equal(h.id('lapLabStatus').dataset.tone, undefined);
      assert.equal(h.id('candidateLapSelect').value, '');
      assert.equal(h.id('playbackToggle').disabled, true);
      assert.equal(h.id('gaugeSpeed').textContent, 'Unavailable');
      assert.equal(h.contexts.get('comparisonMap').calls.some(call => call[0] === 'arc'), false);
    } finally { h.close(); }
  }
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
    // Opening lap a starts its automatic comparison, which stays pending.
    await h.api.selectSession('s1'); const old = h.api.openLap('a'); await settle();
    h.id('analyzeLapAlone').click();
    await h.api.selectSession('s2'); await h.api.openLap('c');
    slowCompare.resolve(response(comparison)); slowSolo.resolve(response({ lap_number: 1, segments: [] })); await old; await settle();
    assert.equal(h.id('gaugeSpeed').textContent, '72.0 km/h');
    assert.equal(h.id('comparisonDelta').textContent, 'Unavailable');
    assert.equal(h.id('soloAnalysisPane').hidden, true);
    assert.equal(h.calls.some(call => call.url.includes('/comparisons/cmp1/trace')), false);
  } finally { h.close(); }
});

test('Failed lap trace clears old data; solo summary failure does not break raw playback', async () => {
  // No reference: this case is about the lap's own playback, not the automatic comparison.
  const h = await harness({ '/api/v1/laps/a/references': () => response({ items: [] }), '/api/v1/laps/b/trace': () => response({ detail: 'Trace missing' }, 409), '/api/v1/laps/a/analysis': () => response({ detail: 'Summary missing' }, 409) });
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
  const h = await harness({ '/api/v1/laps/a/references': () => response({ items: [] }), '/api/v1/laps/a/analysis': () => response({
    lap_number: 1, coverage_ratio: .5, top_speed_kph: null, minimum_speed_kph: null,
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
    assert.equal(h.id('lapLabStatus').dataset.tone, 'warning');
    assert.equal(h.id('gaugeSpeed').textContent, '180.0 km/h');
    assert.equal(h.id('playbackToggle').disabled, false);
  } finally { h.close(); }
});

test('Missing canonical solo coverage remains unavailable even with finite trace samples', async () => {
  const h = await harness({ '/api/v1/laps/a/references': () => response({ items: [] }), '/api/v1/laps/a/analysis': () => response({
    lap_number: 1, top_speed_kph: 180, minimum_speed_kph: 50,
    metric_coverage: { speed: 1, throttle: 1, brake: 1 }, segments: [],
  }) });
  try {
    await h.api.selectSession('s1'); await h.api.openLap('a');
    h.id('analyzeLapAlone').click(); await settle();
    assert.match(h.id('lapLabStatus').textContent, /Whole-lap recording coverage is unavailable/);
    assert.equal(h.id('lapLabStatus').dataset.tone, 'warning');
    assert.equal(h.id('playbackToggle').disabled, false);
    assert.equal(h.id('gaugeSpeed').textContent, '180.0 km/h');
  } finally { h.close(); }
});

// ---------------------------------------------------------------- 5.4.0 Lap Lab
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
const posts = h => h.calls.filter(call => call.options.method === 'POST');
const withLaps = items => ({ '/api/v1/sessions/s1/laps': () => response({ items }) });

test('A late lap trace cannot replace a failed comparison or its reason', async () => {
  const slowTrace = deferred();
  const h = await harness({
    '/api/v1/laps/a/trace': () => slowTrace.promise,
    '/api/v1/comparisons': (url, options, route) => options.method === 'POST'
      ? response({ detail: { message: 'Laps cannot be aligned' } }, 422) : route(url, options),
  });
  try {
    await h.api.selectSession('s1');
    const opening = h.api.openLap('a'); await settle();
    // Compare again before the lap itself has loaded; the server refuses it.
    await h.api.createComparison();
    assert.match(h.id('lapLabStatus').textContent, /Laps cannot be aligned/);
    slowTrace.resolve(response(trace('a'))); await opening; await settle();
    assert.match(h.id('lapLabStatus').textContent, /Laps cannot be aligned/);
    assert.equal(h.id('lapLabStatus').dataset.tone, 'error');
    assert.equal(h.id('playbackToggle').disabled, false, 'the lap still plays on its own');
    assert.equal(posts(h).length, 1, 'no automatic retry after a refusal');
  } finally { h.close(); }
});

test('A lap known only from lap timing is never compared and says why', async () => {
  const timing = { ...lap('t'), lap_number: 3, coverage_ratio: 0 };
  const h = await harness({
    ...withLaps([lap('a'), lap('b'), timing]),
    '/api/v1/laps/t/trace': () => response({ detail: 'No telemetry was recorded for this lap.' }, 409),
  });
  try {
    await h.api.selectSession('s1'); await h.api.openLap('t'); await settle();
    assert.equal(h.calls.some(call => call.url.includes('/laps/t/references')), false);
    assert.equal(posts(h).length, 0);
    assert.match(h.id('referenceLapMeta').textContent, /lap timing only/);
    assert.equal(h.id('referenceDriverSelect').disabled, true);
    assert.match(h.id('lapLabStatus').textContent, /No telemetry/);
    assert.match(h.id('candidateLapSelect').selectedOptions[0].textContent, /timing only/);
  } finally { h.close(); }
});

test('Pickers leave out abandoned and superseded timelines and never default to a timing-only lap', async () => {
  const items = [
    { ...lap('a'), lap_time_ms: 91000 },
    { ...lap('a2'), lap_number: 1, timeline_epoch: 1, lap_time_ms: 90500 },   // supersedes a
    { ...lap('x'), lap_number: 2, timeline_epoch: 0, invalid_reason_mask: 3, valid: false, lap_time_ms: 80000 },
    { ...lap('t'), lap_number: 3, coverage_ratio: 0, lap_time_ms: 85000 },   // fastest, but timing only
    { ...lap('b'), lap_number: 4, lap_time_ms: 90000 },
  ];
  const h = await harness(withLaps(items));
  try {
    await h.api.selectSession('s1'); await settle();
    const select = h.id('candidateDriverSelect');
    select.value = select.options[1].value;
    select.dispatchEvent(new h.w.Event('change')); await settle();
    const values = [...h.id('candidateLapSelect').options].map(option => option.value).filter(Boolean);
    assert.deepEqual(values.sort(), ['a2', 'b', 't']);
    assert.equal(h.id('candidateLapSelect').value, 'b', 'the fastest lap with telemetry opens');
    const timingOption = [...h.id('referenceLapSelect').options].find(option => option.value === 't');
    if (timingOption) {
      assert.equal(timingOption.disabled, true);
      assert.match(timingOption.textContent, /timing only/);
    }
  } finally { h.close(); }
});

test('Stepping through reference laps compares once the choice settles', async () => {
  const h = await harness();
  try {
    await h.api.selectSession('s1'); await h.api.openLap('a'); await settle();
    assert.equal(posts(h).length, 1, 'the suggestion is compared once');
    const select = h.id('referenceLapSelect');
    for (const value of ['invalid', 'b', 'invalid', 'b', 'invalid']) {
      select.value = value;
      select.dispatchEvent(new h.w.Event('change'));
    }
    assert.equal(posts(h).length, 1, 'nothing is posted while the choice is still moving');
    assert.match(h.id('lapLabStatus').textContent, /Comparing against it/);
    await wait(450); await settle();
    assert.equal(posts(h).length, 2);
    assert.equal(JSON.parse(posts(h)[1].options.body).reference.lap_id, 'invalid');
  } finally { h.close(); }
});

test('Use suggested compares at once and keeps keyboard focus in the picker', async () => {
  const h = await harness();
  try {
    h.id('tab-analysis').click(); await settle();  // focus needs the page on screen
    await h.api.selectSession('s1'); await h.api.openLap('a'); await settle();
    const select = h.id('referenceLapSelect');
    select.value = 'invalid'; select.dispatchEvent(new h.w.Event('change'));
    assert.equal(h.id('referenceUseSuggested').hidden, false);
    h.id('referenceUseSuggested').focus();
    h.id('referenceUseSuggested').click(); await settle();
    assert.equal(posts(h).length, 2, 'the suggestion is compared without waiting');
    assert.equal(JSON.parse(posts(h)[1].options.body).reference.lap_id, 'b');
    assert.equal(h.w.document.activeElement, select);
    await wait(450); await settle();
    assert.equal(posts(h).length, 2, 'the cancelled manual choice is not posted later');
  } finally { h.close(); }
});

test('A caveated manual reference is compared without a dialog and a refusal shows inline', async () => {
  const h = await harness({
    '/api/v1/comparisons': (url, options, route) => options.method === 'POST' && JSON.parse(options.body).reference.lap_id === 'invalid'
      ? response({ detail: { message: 'This reference is incompatible: invalid lap' } }, 422) : route(url, options),
  });
  try {
    h.w.confirm = () => false;
    await h.api.selectSession('s1'); await h.api.openLap('a'); await settle();
    const select = h.id('referenceLapSelect');
    select.value = 'invalid'; select.dispatchEvent(new h.w.Event('change'));
    await wait(450); await settle();
    const body = JSON.parse(posts(h).at(-1).options.body);
    assert.equal(body.allow_caveated_reference, true);
    assert.match(h.id('lapLabStatus').textContent, /incompatible: invalid lap.*played on its own/);
    assert.equal(h.id('lapLabStatus').dataset.tone, 'error');
    assert.equal(h.id('comparisonDelta').textContent, 'Unavailable');
    assert.equal(h.id('playbackToggle').disabled, false);
    await wait(450); await settle();
    assert.equal(posts(h).filter(call => JSON.parse(call.options.body).reference.lap_id === 'invalid').length, 1, 'no retry');
  } finally { h.close(); }
});

test('Opening laps from Session Analysis: the last pick wins while a session loads', async () => {
  const slowS2 = deferred();
  const h = await harness({ '/api/v1/sessions/s2': () => slowS2.promise });
  try {
    h.w.dispatchEvent(new h.w.CustomEvent('pitwall:open-lap', { detail: { sessionId: 's2', lapId: 'c' } }));
    await settle();
    h.w.dispatchEvent(new h.w.CustomEvent('pitwall:open-lap', { detail: { sessionId: 's1', lapId: 'a' } }));
    await settle(); await settle();
    slowS2.resolve(response({ session: session('s2') })); await settle(); await settle();
    assert.equal(h.id('lapLabSessionSelect').value, 's1');
    assert.equal(h.id('candidateLapSelect').value, 'a');
    assert.equal(h.calls.some(call => call.url.includes('/laps/c/trace')), false);
  } finally { h.close(); }
});

test('A session outside the Library list keeps its name in every session selector', async () => {
  const s3 = { ...session('s3'), display_name: 'Older race' };
  const h = await harness({
    '/api/v1/sessions/s3': () => response({ session: s3 }),
    '/api/v1/sessions/s3/quality': () => response({ quality_score: 1, laps: { total: 1, valid: 1 } }),
    '/api/v1/sessions/s3/laps': () => response({ items: [{ ...lap('z'), lap_number: 5 }] }),
    '/api/v1/sessions/s3/field': () => response({ classification: [], cars_observed: 1 }),
  });
  try {
    h.w.dispatchEvent(new h.w.CustomEvent('pitwall:open-lap', { detail: { sessionId: 's3', lapId: 'z' } }));
    await settle(); await settle();
    for (const id of ['lapLabSessionSelect', 'reviewSessionSelect', 'fieldSessionSelect']) {
      assert.equal(h.id(id).value, 's3', id);
      assert.match(h.id(id).selectedOptions[0].textContent, /Older race/);
    }
    assert.equal(h.id('candidateLapSelect').value, 'z');
  } finally { h.close(); }
});

test('A reference from another session is named by its type and date', async () => {
  const h = await harness({
    '/api/v1/laps/a/references': () => response({ items: [{ lap_id: 'r9', session_id: 's9', driver: 'Rival', lap_number: 7, lap_time_ms: 89500, suggested: true, compatibility: { class: 'comparable_with_caveats', caveats: ['different session'] }, reasons: ['same track/layout'] }] }),
    '/api/v1/sessions/s9': () => response({ session: { id: 's9', session_type: 'Qualifying 1', started_at: '2026-10-01T12:00:00Z' } }),
  });
  try {
    await h.api.selectSession('s1'); await h.api.openLap('a'); await settle(); await settle();
    const group = [...h.id('referenceDriverSelect').querySelectorAll('optgroup')].find(item => item.label === 'Other sessions at this track');
    assert.ok(group, 'other-session group');
    assert.match(group.textContent, /Rival · Qualifying 1/);
    assert.match(h.id('lapLabStatus').textContent, /another session \(Qualifying 1/);
    assert.equal(h.id('lapLabStatus').dataset.tone, 'warning');
  } finally { h.close(); }
});

test('Deleting a session announces it and compares again when it supplied the reference', async () => {
  let references = 0;
  const h = await harness({
    '/api/v1/sessions/s2': (url, options, route) => options.method === 'DELETE'
      ? response(options.headers?.['X-Pitwall-Delete-Token'] ? { deleted: true } : { confirmation_token: 'token', impact: { records: {}, artifacts: [] } })
      : route(url, options),
    '/api/v1/laps/a/references': () => {
      references += 1;
      return response({ items: references === 1 ? [{ lap_id: 'c', session_id: 's2', driver: 'Driver', lap_number: 2, suggested: true, compatibility: { class: 'strict' } }] : [] });
    },
  });
  try {
    const deleted = [];
    h.w.addEventListener('pitwall:session-deleted', event => deleted.push(event.detail.sessionId));
    await h.api.selectSession('s1'); await h.api.openLap('a'); await settle();
    h.id('tab-analysis').click(); await settle();
    [...h.id('libraryRows').querySelectorAll('tr')].find(row => row.textContent.includes('Session s2'))
      .querySelectorAll('button').forEach(item => { if (item.textContent === 'Delete') item.click(); });
    await settle(); await settle();
    assert.deepEqual(deleted, ['s2']);
    assert.equal(references, 2, 'references are fetched again without the deleted session');
    assert.equal(h.id('comparisonDelta').textContent, 'Unavailable');
  } finally { h.close(); }
});
