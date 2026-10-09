/* Session Analysis in the shipped markup with deterministic API fixtures.
   No user database, network, browser or tablet.
     NODE_PATH=<dir with jsdom>/node_modules node tools/session-analysis-ui-smoke.cjs
   Loading: static/js/session-analysis.js is an ES module and jsdom runs
   classic scripts only, so the smoke bundles it in memory. The model's
   `export` keywords are dropped and its exported names returned from an
   IIFE that replaces the view's single `import * as M` line; the view's own
   `export` keywords are dropped and `__test` is exposed on window. Nothing
   else in the source is rewritten, so the shipped code is what runs. The
   inline router (ANALYSIS_VIEWS, selectPage, selectAnalysisView) is taken
   from index.html, and runs before the module, as it does in the app.
   Timers and animation frames are fakes the tests drive by hand. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { test } = require('node:test');
const { JSDOM, VirtualConsole } = require('jsdom');

const root = path.resolve(__dirname, '..');
const read = (name) => fs.readFileSync(path.join(root, name), 'utf8');
const html = read('static/index.html');
const race = JSON.parse(read('tests/fixtures/session_analysis_race.json'));
const clone = (value) => JSON.parse(JSON.stringify(value));
const settle = async () => { for (let i = 0; i < 12; i += 1) await new Promise((resolve) => setImmediate(resolve)); };
const deferred = () => { let resolve; const promise = new Promise((done) => { resolve = done; }); return { promise, resolve }; };
const response = (payload, status = 200) => ({ ok: status < 400, status, statusText: status < 400 ? 'OK' : 'Error', text: async () => JSON.stringify(payload) });
const CHARTS = ['analysisPaceChart', 'analysisTraceChart', 'analysisPositionsChart', 'analysisStrategyChart', 'analysisLapTimesChart', 'analysisHeatmapChart', 'analysisLapseChart', 'analysisFastestChart'];
const TABLES = ['analysisPaceTable', 'analysisTraceTable', 'analysisPositionsTable', 'analysisStrategyTable', 'analysisLapTimesTable', 'analysisHeatmapTable', 'analysisLapseTable', 'analysisFastestTable'];
const STORAGE = 'pitwall.analysis.session';

function bundle() {
  const model = read('static/js/session-analysis-model.mjs');
  const names = [...model.matchAll(/^export (?:async )?(?:function|const|let|class) (\w+)/gm)].map((m) => m[1]);
  const inlined = `const M = (() => {\n${model.replace(/^export (?=(?:async )?(?:function|const|let|class) )/gm, '')}\nreturn { ${names.join(', ')} };\n})();`;
  const view = read('static/js/session-analysis.js');
  const importLine = /^import \* as M from "\.\/session-analysis-model\.mjs";\r?$/m;
  assert.match(view, importLine, 'the view imports the model once');
  const body = view.replace(importLine, () => inlined).replace(/^export (?=(?:async )?(?:function|const) )/gm, '');
  assert.doesNotMatch(body, /^(?:import|export)\b/m, 'no module syntax is left');
  return `(() => {\n"use strict";\n${body}\nwindow.__sessionAnalysisTest = __test;\n})();`;
}
const MODULE = bundle();
const ROUTER = [
  html.match(/^const ANALYSIS_VIEWS=\[[^\]]*\];/m)[0],
  'function loadTracks(){} function loadHistory(){} function loadAppSettings(){}',
  ...['selectAnalysisView', 'selectPage'].map((name) => html.match(new RegExp(`^function ${name}\\([^\\n]+`, 'm'))[0]),
].join('\n');

const practice = (() => {
  const p = clone(race);
  p.session_id = 'ses_practice';
  Object.assign(p.session, { is_race: false, session_type: 'Practice 1', leader_laps: null, winner_laps: null, provisional: false });
  p.basis.pace_laps = 'excludes pit laps, flag laps, invalid laps, laps excluded in lap notes, laps recorded without telemetry context and laps without a time';
  for (const lap of p.laps) Object.assign(lap, { position: null, gap_to_leader_ms: null, interval_ms: null, neutralised: null, suspended: false, pace_excluded: lap.lap_time_ms ? null : 'missing_time' });
  for (const d of p.drivers) Object.assign(d, { status: 'running', finish_position: null, laps_down: null });
  Object.assign(p, { segments: [], suspended_laps: [], neutralised_laps: [], winner_car_index: null, warnings: [] });
  p.race_control = { available: false, covered_from_lap: null, neutralisations: [], safety_car_laps: [], vsc_laps: [], red_flag_laps: [] };
  return p;
})();
const raceB = (() => {
  const b = clone(race);
  b.session_id = 'ses_b';
  b.session.track_name = 'Monza';
  b.session.provisional = true;
  b.warnings = ['marker-b'];
  return b;
})();

const LIST = [
  { id: 'ses_fixture_race', track_name: 'Singapore', session_type: 'Race', started_at: '2026-10-08T13:00:00+00:00' },
  { id: 'ses_b', track_name: 'Monza', session_type: 'Race', started_at: '2026-10-07T13:00:00+00:00' },
  { id: 'ses_practice', track_name: 'Singapore', session_type: 'Practice 1', started_at: '2026-10-06T13:00:00+00:00' },
];

async function harness({ hash = '', storage = {}, overrides = {}, reducedMotion = false, list = LIST } = {}) {
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('error', (...args) => errors.push(args.map(String).join(' ')));
  virtualConsole.on('jsdomError', (error) => errors.push(String(error?.stack || error)));
  const dom = new JSDOM(html, { url: `http://127.0.0.1:8769/${hash}`, runScripts: 'outside-only', pretendToBeVisual: true, virtualConsole });
  const w = dom.window;
  const id = (name) => w.document.getElementById(name);
  for (const [key, value] of Object.entries(storage)) w.localStorage.setItem(key, value);
  const timers = new Map(); let timerId = 0;
  w.setTimeout = (fn, ms) => { timers.set(++timerId, { fn, ms }); return timerId; };
  w.clearTimeout = (handle) => { timers.delete(handle); };
  const frames = new Map(); let frameId = 0;
  w.requestAnimationFrame = (fn) => { frames.set(++frameId, fn); return frameId; };
  w.cancelAnimationFrame = (handle) => { frames.delete(handle); };
  w.matchMedia = (query) => ({ matches: reducedMotion && /reduce/.test(query), media: query, addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {} });
  w.confirm = w.alert = w.prompt = () => { throw new Error('native dialogs are not allowed'); };
  const calls = [];
  const state = { list };
  const payloads = { ses_fixture_race: race, ses_b: raceB, ses_practice: practice };
  const route = async (url) => {
    const u = new URL(url, 'http://127.0.0.1:8769');
    if (u.pathname === '/api/v1/sessions') return response({ items: state.list });
    const match = u.pathname.match(/^\/api\/v1\/sessions\/([^/]+)\/analysis$/);
    if (match) {
      const sessionId = decodeURIComponent(match[1]);
      if (payloads[sessionId]) return response({ ...clone(payloads[sessionId]), hide_outliers: u.searchParams.get('hide_outliers') === 'true' });
      return response({ detail: 'Session not found' }, 404);
    }
    throw new Error(`Unexpected API request: ${url}`);
  };
  w.fetch = async (url, options = {}) => {
    calls.push(String(url));
    const u = new URL(url, 'http://127.0.0.1:8769');
    const key = Object.keys(overrides).find((k) => `${u.pathname}${u.search}`.startsWith(k));
    return key ? overrides[key](url, options, route) : route(url, options);
  };
  const opened = [];
  w.addEventListener('pitwall:open-lap', (event) => opened.push(event.detail));
  w.eval(ROUTER);
  w.document.querySelectorAll('.tab').forEach((button) => { button.onclick = () => w.selectPage(button.dataset.page); });
  w.document.querySelectorAll('.analysis-subnav .field-tab').forEach((button) => { button.onclick = () => w.selectAnalysisView(button.dataset.page); });
  // The inline router runs during parsing, before the deferred module.
  if (hash) w.selectPage(hash.slice(1));
  w.eval(MODULE);
  await settle();
  const runTimers = () => { const pending = [...timers.entries()]; timers.clear(); for (const [, t] of pending) t.fn(); };
  const runFrames = (now = 16) => { const pending = [...frames.entries()]; frames.clear(); for (const [, fn] of pending) fn(now); };
  const analyse = async (sessionId) => { w.dispatchEvent(new w.CustomEvent('pitwall:analyze-session', { detail: { sessionId } })); await settle(); };
  const show = async () => { w.selectPage('session-analysis'); await settle(); };
  const t = w.__sessionAnalysisTest;
  return { w, id, calls, timers, frames, runTimers, runFrames, analyse, show, opened, errors, state, t, close: () => w.close() };
}

const text = (h, name) => h.id(name).textContent;
const chartIsEmpty = (h, name) => {
  const node = h.id(name);
  return !node.querySelector('svg') && node.querySelectorAll('.empty').length === 1;
};
function assertCleared(h, label) {
  for (const name of CHARTS) assert.ok(chartIsEmpty(h, name), `${label}: ${name} shows only its placeholder`);
  for (const name of TABLES) assert.equal(h.id(name).children.length, 0, `${label}: ${name} is empty`);
  assert.equal(h.id('analysisKpis').children.length, 0, `${label}: KPIs cleared`);
  assert.equal(text(h, 'analysisPaceThin'), '', `${label}: thin-pace note cleared`);
  assert.equal(text(h, 'analysisTraceNote'), '', `${label}: trace note cleared`);
  assert.doesNotMatch(text(h, 'analysisTitle'), /wins|leads/, `${label}: title cleared`);
  assert.equal(h.id('analysisFocus').children.length, 0, `${label}: focus chips cleared`);
  assert.equal(h.id('analysisStopsRows').querySelectorAll('tr').length, 1, `${label}: stops cleared`);
  assert.equal(h.id('analysisLapsePlay').getAttribute('aria-pressed'), 'false');
}
function assertRace(h, label) {
  assert.match(text(h, 'analysisTitle'), /wins/, `${label}: race headline`);
  assert.ok(h.id('analysisKpis').children.length >= 4, `${label}: KPIs`);
  for (const name of CHARTS) assert.ok(h.id(name).querySelector('svg'), `${label}: ${name} drawn`);
  for (const name of TABLES) assert.ok(h.id(name).querySelector('details table'), `${label}: ${name} has a data table`);
}
const key = (h, target, name) => target.dispatchEvent(new h.w.KeyboardEvent('keydown', { key: name, bubbles: true }));
const pointer = (h, target, type, pointerType = 'touch') => target.dispatchEvent(new h.w.PointerEvent(type, { bubbles: true, pointerType, clientX: 1, clientY: 1 }));
const click = (h, target) => target.dispatchEvent(new h.w.MouseEvent('click', { bubbles: true }));
const tap = (h, target) => { pointer(h, target, 'pointerdown'); pointer(h, target, 'pointerup'); click(h, target); };

test('A reload at #session-analysis opens the remembered session once the deferred module loads', async () => {
  const h = await harness({ hash: '#session-analysis', storage: { [STORAGE]: 'ses_fixture_race' } });
  try {
    assert.equal(h.id('session-analysis').hidden, false);
    assert.ok(h.calls.some((url) => url.startsWith('/api/v1/sessions?limit=50')), 'the session list loads');
    assert.ok(h.calls.some((url) => url.startsWith('/api/v1/sessions/ses_fixture_race/analysis')), 'the remembered session reopens');
    assertRace(h, 'deep link');
    assert.equal(h.id('analysisSessionSelect').value, 'ses_fixture_race');
    assert.deepEqual(h.errors, []);
  } finally { h.close(); }
  // A stored analysis view reached through #analysis activates the same way.
  const stored = await harness({ hash: '#analysis', storage: { 'pitwall.analysis.view': 'session-analysis', [STORAGE]: 'ses_fixture_race' } });
  try {
    assert.equal(stored.id('session-analysis').hidden, false);
    assertRace(stored, 'stored view');
  } finally { stored.close(); }
  // A hidden view does no work until it is shown.
  const hidden = await harness({ storage: { [STORAGE]: 'ses_fixture_race' } });
  try {
    assert.equal(hidden.calls.length, 0);
    await hidden.show();
    assertRace(hidden, 'shown later');
  } finally { hidden.close(); }
});

test('A remembered session that no longer exists is forgotten and the chooser is shown', async () => {
  const h = await harness({ hash: '#session-analysis', storage: { [STORAGE]: 'ses_deleted_elsewhere' } });
  try {
    assert.equal(h.w.localStorage.getItem(STORAGE), null);
    assert.match(text(h, 'analysisStatus'), /no longer saved/);
    assert.equal(h.id('analysisSessionSelect').value, '');
    assert.ok([...h.id('analysisSessionSelect').options].every((o) => o.value !== 'ses_deleted_elsewhere'));
    assertCleared(h, 'after 404');
    assert.deepEqual(h.errors, []);
  } finally { h.close(); }
});

test('Switching sessions clears everything at once and only the newest request renders (A, B, A out of order)', async () => {
  const slowB = deferred(), slowA = deferred();
  let aCalls = 0;
  const h = await harness({ overrides: {
    '/api/v1/sessions/ses_b/analysis': () => slowB.promise,
    '/api/v1/sessions/ses_fixture_race/analysis': (url, options, route) => (++aCalls === 1 ? route(url) : slowA.promise),
  } });
  try {
    await h.show();
    await h.analyse('ses_fixture_race');
    assertRace(h, 'A');
    assert.match(text(h, 'analysisPaceThin'), /HAD/);
    h.w.dispatchEvent(new h.w.CustomEvent('pitwall:analyze-session', { detail: { sessionId: 'ses_b' } }));
    assertCleared(h, 'while B loads');
    assert.match(text(h, 'analysisTitle'), /Loading/);
    assert.ok(h.id('analysisPaceChart').textContent.includes('Loading'));
    h.w.dispatchEvent(new h.w.CustomEvent('pitwall:analyze-session', { detail: { sessionId: 'ses_fixture_race' } }));
    slowB.resolve(response(raceB)); await settle();
    assert.match(text(h, 'analysisTitle'), /Loading/, 'the stale B response is ignored');
    assert.doesNotMatch(text(h, 'analysisStatus'), /marker-b/);
    slowA.resolve(response(race)); await settle();
    assertRace(h, 'A again');
    assert.equal(h.id('analysisSessionSelect').value, 'ses_fixture_race');
  } finally { h.close(); }
});

test('A late failure cannot clear a newer success, and hide_outliers toggled mid-request applies the latest', async () => {
  const slowFail = deferred();
  const toggles = [];
  const h = await harness({ overrides: {
    '/api/v1/sessions/ses_b/analysis': () => slowFail.promise,
    '/api/v1/sessions/ses_fixture_race/analysis?hide_outliers=false': (url) => { const d = deferred(); toggles.push({ d, url }); return d.promise; },
  } });
  try {
    await h.show();
    h.w.dispatchEvent(new h.w.CustomEvent('pitwall:analyze-session', { detail: { sessionId: 'ses_b' } }));
    await h.analyse('ses_fixture_race');
    slowFail.resolve(response({ detail: 'old request failed' }, 500)); await settle();
    assertRace(h, 'after the late failure');
    assert.doesNotMatch(text(h, 'analysisStatus'), /old request failed/);
    // Off (slow), then on again (fast): the slow "off" response must not win.
    const box = h.id('analysisHideOutliers');
    box.checked = false; box.dispatchEvent(new h.w.Event('change', { bubbles: true }));
    box.checked = true; box.dispatchEvent(new h.w.Event('change', { bubbles: true }));
    await settle();
    assert.equal(h.t.state.analysis.hide_outliers, true);
    toggles[0].d.resolve(response({ ...clone(race), hide_outliers: false, warnings: ['marker-stale-toggle'] })); await settle();
    assert.equal(h.t.state.analysis.hide_outliers, true);
    assert.doesNotMatch(text(h, 'analysisStatus'), /marker-stale-toggle/);
  } finally { h.close(); }
});

test('A failure, the placeholder choice and the schema check all clear the previous session', async () => {
  let broken = true;
  const h = await harness({ overrides: {
    '/api/v1/sessions/ses_b/analysis': (url, options, route) => (broken ? response({ detail: 'Analysis store is busy' }, 409) : route(url)),
    '/api/v1/sessions/ses_old/analysis': () => response({ ...clone(race), schema_version: 1 }),
  } });
  try {
    await h.show();
    await h.analyse('ses_fixture_race');
    await h.analyse('ses_b');
    assertCleared(h, 'after a failure');
    assert.match(text(h, 'analysisStatus'), /Analysis store is busy.*Refresh/);
    assert.equal(h.id('analysisStatus').dataset.tone, 'error');
    broken = false;
    h.id('analysisRefresh').click(); await settle();
    assert.match(text(h, 'analysisStatus'), /marker-b/, 'Refresh retries the chosen session');
    await h.analyse('ses_old');
    assertCleared(h, 'old schema');
    assert.match(text(h, 'analysisStatus'), /Reload the app to view this analysis/);
    await h.analyse('ses_fixture_race');
    const select = h.id('analysisSessionSelect');
    select.value = ''; select.dispatchEvent(new h.w.Event('change', { bubbles: true })); await settle();
    assertCleared(h, 'placeholder');
    assert.equal(h.w.localStorage.getItem(STORAGE), null, 'choosing nothing forgets the session');
    assert.match(text(h, 'analysisStatus'), /Choose a saved session/);
    // Refresh with nothing chosen still works: it reloads the list.
    const before = h.calls.length;
    h.id('analysisRefresh').click(); await settle();
    assert.ok(h.calls.slice(before).some((url) => url.startsWith('/api/v1/sessions?limit=50')));
  } finally { h.close(); }
});

test('An empty but successful payload leaves no chart, table or note from the previous session', async () => {
  const empty = { ...clone(race), session_id: 'ses_empty', drivers: [], laps: [], race_pace: [], stints: [], pit_stops: [], fastest_laps: [], suspended_laps: [], neutralised_laps: [], segments: [], winner_car_index: null, warnings: ['No laps are stored for this session.'] };
  const h = await harness({ overrides: { '/api/v1/sessions/ses_empty/analysis': () => response(empty) } });
  try {
    await h.show();
    await h.analyse('ses_fixture_race');
    await h.analyse('ses_empty');
    for (const name of CHARTS) assert.ok(chartIsEmpty(h, name), `${name} is empty`);
    for (const name of TABLES) assert.equal(h.id(name).children.length, 0, `${name} is empty`);
    assert.equal(text(h, 'analysisPaceThin'), '');
    assert.equal(h.id('analysisStopsRows').textContent, 'No pit stops or tyre changes were recorded.');
    assert.match(text(h, 'analysisStatus'), /No laps are stored/);
    assert.deepEqual(h.errors, []);
  } finally { h.close(); }
});

test('Deleting the analysed session clears and forgets it and refreshes the session list', async () => {
  const h = await harness();
  try {
    await h.show();
    await h.analyse('ses_fixture_race');
    assert.equal(h.w.localStorage.getItem(STORAGE), 'ses_fixture_race');
    h.state.list = LIST.filter((s) => s.id !== 'ses_fixture_race');
    const before = h.calls.length;
    h.w.dispatchEvent(new h.w.CustomEvent('pitwall:session-deleted', { detail: { sessionId: 'ses_fixture_race' } }));
    assertCleared(h, 'after deletion');
    assert.equal(h.w.localStorage.getItem(STORAGE), null);
    await settle();
    assert.ok(h.calls.slice(before).some((url) => url.startsWith('/api/v1/sessions?limit=50')), 'the list is refreshed');
    assert.ok(![...h.id('analysisSessionSelect').options].some((o) => o.value === 'ses_fixture_race'));
    assert.match(text(h, 'analysisStatus'), /deleted/);
    // Deleting another session keeps the current one.
    await h.analyse('ses_b');
    h.w.dispatchEvent(new h.w.CustomEvent('pitwall:session-deleted', { detail: { sessionId: 'ses_practice' } })); await settle();
    assert.match(text(h, 'analysisStatus'), /marker-b/);
    assert.equal(h.w.localStorage.getItem(STORAGE), 'ses_b');
  } finally { h.close(); }
});

test('Practice: focus changes redraw lap times, race-only captions hide and the pace caption follows the basis', async () => {
  const h = await harness();
  try {
    await h.show();
    await h.analyse('ses_practice');
    for (const node of h.w.document.querySelectorAll('#session-analysis [data-analysis-race-only]')) assert.equal(node.hidden, true);
    assert.equal(h.id('analysisRaceOnlyNote').hidden, false);
    assert.equal(text(h, 'analysisPaceBasis'), `Pace ${practice.basis.pace_laps}.`);
    const series = () => h.id('analysisLapTimesChart').querySelectorAll('svg .sa-label-strong').length;
    const start = series();
    assert.ok(start >= 2, 'the default focus is drawn');
    h.id('analysisFocus').querySelector('.sa-chip').click(); await settle();
    assert.equal(series(), start - 1, 'removing a chip redraws');
    assert.equal(h.w.document.activeElement?.classList.contains('sa-chip') || h.w.document.activeElement === h.id('analysisAddDriver'), true, 'focus stays in the chip row');
    const add = h.id('analysisAddDriver');
    add.value = add.options[1].value; add.dispatchEvent(new h.w.Event('change', { bubbles: true })); await settle();
    assert.equal(series(), start, 'adding a driver redraws');
    add.value = add.options[1].value; add.dispatchEvent(new h.w.Event('change', { bubbles: true })); await settle();
    assert.equal(series(), start + 1);
    h.id('analysisResetFocus').click(); await settle();
    assert.equal(series(), start, 'Reset redraws');
    assert.match(h.id('analysisLapTimesTable').textContent, /Lap/);
    assert.doesNotMatch(text(h, 'analysisTitle'), /wins/);
    assert.deepEqual(h.errors, []);
  } finally { h.close(); }
  const raceView = await harness();
  try {
    await raceView.show();
    await raceView.analyse('ses_fixture_race');
    assert.match(text(raceView, 'analysisPaceBasis'), /^Pace excludes lap 1, restart laps/);
    for (const node of raceView.w.document.querySelectorAll('#session-analysis [data-analysis-race-only]')) assert.equal(node.hidden, false);
  } finally { raceView.close(); }
});

test('Reduced motion keeps one cancellable timeout and every exit path stops the timelapse', async () => {
  const h = await harness({ reducedMotion: true });
  const play = () => h.id('analysisLapsePlay');
  const stopped = (label) => {
    assert.equal(h.t.state.lapse.timer, 0, `${label}: no timeout`);
    assert.equal(h.t.state.lapse.raf, 0, `${label}: no frame`);
    assert.equal(h.timers.size, 0, `${label}: nothing pending`);
    assert.equal(play().textContent, 'Play', `${label}: button reset`);
    assert.equal(play().getAttribute('aria-pressed'), 'false');
  };
  try {
    await h.show();
    await h.analyse('ses_fixture_race');
    h.id('analysisLapseRange').value = '1'; h.id('analysisLapseRange').dispatchEvent(new h.w.Event('input', { bubbles: true }));
    play().click();
    assert.equal(h.timers.size, 1);
    play().click(); play().click(); // Pause and Play again inside one interval
    assert.equal(h.timers.size, 1, 'Play, Pause, Play leaves exactly one timeout');
    assert.equal(play().getAttribute('aria-pressed'), 'true');
    h.runTimers();
    assert.equal(h.t.state.lapse.t, 2, 'one lap per interval');
    assert.equal(h.timers.size, 1);
    assert.match(text(h, 'analysisLapseLabel'), /^Lap 2 of 12/);
    const exits = [
      ['leaving the view', () => h.w.dispatchEvent(new h.w.CustomEvent('pitwall:pagechange', { detail: { page: 'library' } }))],
      ['hiding the document', () => { Object.defineProperty(h.w.document, 'hidden', { value: true, configurable: true }); h.w.document.dispatchEvent(new h.w.Event('visibilitychange')); Object.defineProperty(h.w.document, 'hidden', { value: false, configurable: true }); }],
      ['pagehide', () => h.w.dispatchEvent(new h.w.Event('pagehide'))],
      ['deleting a session', () => h.w.dispatchEvent(new h.w.CustomEvent('pitwall:session-deleted', { detail: { sessionId: 'ses_practice' } }))],
      ['opening Lap Lab', () => { const cell = h.id('analysisHeatmapChart').querySelector('[data-lap-id]'); cell.focus(); key(h, cell, 'Enter'); }],
      ['switching session', () => h.w.dispatchEvent(new h.w.CustomEvent('pitwall:analyze-session', { detail: { sessionId: 'ses_b' } }))],
    ];
    for (const [label, exit] of exits) {
      if (label !== exits[0][0]) await h.show();
      if (!h.t.state.analysis) await h.analyse('ses_fixture_race');
      h.id('analysisLapseRange').value = '1'; h.id('analysisLapseRange').dispatchEvent(new h.w.Event('input', { bubbles: true }));
      play().click();
      assert.equal(h.timers.size, 1, `${label}: playing`);
      exit();
      stopped(label);
      await settle();
    }
    assert.equal(h.opened.length, 1, 'the Lap Lab hand-off was dispatched');
  } finally { h.close(); }
  const animated = await harness();
  try {
    await animated.show();
    await animated.analyse('ses_fixture_race');
    animated.id('analysisLapseRange').value = '1'; animated.id('analysisLapseRange').dispatchEvent(new animated.w.Event('input', { bubbles: true }));
    animated.id('analysisLapsePlay').click();
    const handle = animated.t.state.lapse.raf;
    assert.ok(handle && animated.frames.has(handle), 'an animation frame is pending');
    const svg = animated.id('analysisLapseChart').querySelector('svg');
    animated.runFrames(1000); animated.runFrames(1500);
    assert.equal(animated.id('analysisLapseChart').querySelector('svg'), svg, 'frames move marks instead of rebuilding the SVG');
    assert.ok(animated.t.state.lapse.t > 1);
    const pending = animated.t.state.lapse.raf;
    assert.ok(animated.frames.has(pending));
    animated.w.dispatchEvent(new animated.w.CustomEvent('pitwall:pagechange', { detail: { page: 'lap-lab' } }));
    assert.equal(animated.frames.has(pending), false, 'leaving cancels the pending frame');
    assert.equal(animated.t.state.lapse.raf, 0);
    assert.equal(animated.id('analysisLapsePlay').textContent, 'Play');
  } finally { animated.close(); }
});

test('Keyboard: one Tab stop per chart, arrow keys move between marks, and only keyboard focus is announced', async () => {
  const h = await harness();
  try {
    await h.show();
    await h.analyse('ses_fixture_race');
    const doc = h.w.document;
    for (const svg of doc.querySelectorAll('#session-analysis svg[role="img"]')) {
      assert.equal(svg.querySelectorAll('[tabindex]').length, 0, 'nothing focusable sits under role="img"');
    }
    for (const name of CHARTS.filter((n) => n !== 'analysisLapseChart')) {
      const chart = h.id(name);
      const stops = [...chart.querySelectorAll('[tabindex="0"]')];
      assert.equal(stops.length, 1, `${name} has one Tab stop`);
      const group = chart.querySelector('svg[role="group"]');
      assert.ok(group?.getAttribute('aria-label'), `${name} is a labelled group`);
    }
    assert.equal(h.id('analysisLapseChart').querySelectorAll('[tabindex]').length, 0);
    for (const mark of doc.querySelectorAll('#session-analysis [data-key]:not([data-key="chart"])')) {
      assert.match(mark.getAttribute('aria-label') || '', /\b[A-Z]{3}\b/, 'every mark names its driver');
    }
    const heat = h.id('analysisHeatmapChart');
    const first = heat.querySelector('[tabindex="0"]');
    first.focus();
    assert.equal(heat.querySelector('.sa-tip').getAttribute('aria-live'), 'off', 'focus from no key press is not announced');
    key(h, first, 'ArrowRight');
    const second = doc.activeElement;
    assert.notEqual(second, first);
    assert.equal(second.getAttribute('tabindex'), '0');
    assert.equal(first.getAttribute('tabindex'), '-1');
    assert.equal(heat.querySelectorAll('[tabindex="0"]').length, 1);
    assert.equal(heat.querySelector('.sa-tip').getAttribute('aria-live'), 'polite', 'a keyboard focus change is announced');
    key(h, second, 'ArrowDown');
    assert.notEqual(doc.activeElement.getAttribute('data-row'), second.getAttribute('data-row'));
    // A mouse hover shows the reading without announcing it.
    const cell = heat.querySelector('[data-row="1"]');
    pointer(h, cell, 'pointerdown', 'mouse');
    pointer(h, cell, 'pointermove', 'mouse');
    assert.equal(heat.querySelector('.sa-tip').getAttribute('aria-live'), 'off');
    // Enter opens a traced lap; an untraced lap has no Lap Lab action.
    const botRow = M_row(h, 'BOT');
    const untraced = heat.querySelector(`[data-key="heat-${botRow}-9"]`);
    assert.ok(untraced && !untraced.hasAttribute('data-lap-id'));
    assert.match(untraced.getAttribute('aria-label'), /timing only — no telemetry/);
    untraced.focus(); key(h, untraced, 'Enter');
    assert.equal(h.opened.length, 0);
    const traced = heat.querySelector('[data-lap-id="lap_0_9"]');
    traced.focus(); key(h, traced, 'Enter');
    assert.deepEqual(clone(h.opened), [{ sessionId: 'ses_fixture_race', lapId: 'lap_0_9' }]);
    // The race trace is one focusable group stepped by lap.
    const trace = h.id('analysisTraceChart').querySelector('svg');
    trace.focus(); key(h, trace, 'End');
    assert.match(h.id('analysisTraceChart').querySelector('.sa-tip').textContent, /^Lap 12/);
    assert.match(h.id('analysisTraceChart').querySelector('.sa-tip').textContent, /behind/);
    // Re-rendering keeps keyboard focus on the same mark and open tables open.
    const box = h.id('analysisPaceChart').querySelector('[data-key]:not([data-key="chart"])');
    box.focus();
    const boxKey = box.getAttribute('data-key');
    h.id('analysisPaceTable').querySelector('details').open = true;
    const toggle = h.id('analysisHideOutliers');
    toggle.checked = false; toggle.dispatchEvent(new h.w.Event('change', { bubbles: true })); await settle();
    assert.equal(doc.activeElement.getAttribute('data-key'), boxKey, 'focus survives the redraw');
    assert.notEqual(doc.activeElement, box);
    assert.equal(h.id('analysisPaceTable').querySelector('details').open, true, 'an open table stays open');
    assert.deepEqual(h.errors, []);
  } finally { h.close(); }
});

function M_row(h, code) {
  return h.t.state.analysis.drivers.find((d) => d.code === code).car_index;
}

test('Touch: a first tap shows the value, a second tap or the tip button opens Lap Lab, and pointerdown does nothing', async () => {
  const h = await harness();
  try {
    await h.show();
    await h.analyse('ses_fixture_race');
    const heat = h.id('analysisHeatmapChart');
    const cell = heat.querySelector('[data-lap-id="lap_0_9"]');
    pointer(h, cell, 'pointerdown');
    assert.equal(h.opened.length, 0, 'pointerdown never acts');
    pointer(h, cell, 'pointerup'); cell.focus(); click(h, cell);
    assert.equal(h.opened.length, 0, 'the first tap only reads the lap');
    const tip = heat.querySelector('.sa-tip');
    assert.equal(tip.hidden, false);
    assert.match(tip.textContent, /LEC lap 9/);
    const open = tip.querySelector('.sa-tip-open');
    assert.ok(open, 'the pinned reading offers Open in Lap Lab');
    // A tap elsewhere keeps nothing pinned; the reading stays until then.
    click(h, h.id('analysisTitle'));
    assert.equal(tip.hidden, true);
    tap(h, cell);
    tap(h, cell);
    assert.deepEqual(h.opened.map((o) => o.lapId), ['lap_0_9'], 'the second tap opens');
    tap(h, cell);
    heat.querySelector('.sa-tip-open').click();
    assert.equal(h.opened.length, 2, 'the tip button opens');
    // A lap-time dot works the same way; a mouse hover then click opens at once.
    const dot = h.id('analysisLapTimesChart').querySelector('[data-lap-id]');
    pointer(h, dot, 'pointermove', 'mouse');
    pointer(h, dot, 'pointerdown', 'mouse'); click(h, dot);
    assert.equal(h.opened.length, 3);
    // The heatmap table is the 44 px route: buttons only for traced laps.
    const table = h.id('analysisHeatmapTable');
    assert.ok(table.querySelector('select#analysisHeatmapDriver'));
    assert.ok(table.querySelectorAll('button.sa-open').length >= 1);
    const select = table.querySelector('select');
    select.value = String(M_row(h, 'BOT')); select.dispatchEvent(new h.w.Event('change', { bubbles: true }));
    assert.match(h.id('analysisHeatmapTable').textContent, /Timing only/);
    assert.equal(h.id('analysisHeatmapTable').querySelectorAll('button.sa-open').length, 2, 'only BOT\'s two traced laps can open');
    h.id('analysisHeatmapTable').querySelector('button.sa-open').click();
    assert.equal(h.opened[3].lapId, 'lap_4_1');
  } finally { h.close(); }
});

test('Driver-facing text: lapped finishers, retirements, unknown stops and literal names', async () => {
  const hostile = clone(race);
  hostile.session_id = 'ses_hostile';
  hostile.drivers[1].display_name = '<img src=x onerror=alert(1)>';
  hostile.drivers[2].display_name = 'Nonetheless None NaN';
  const h = await harness({ overrides: { '/api/v1/sessions/ses_hostile/analysis': () => response(hostile) } });
  try {
    await h.show();
    await h.analyse('ses_fixture_race');
    const view = h.id('session-analysis');
    const positions = [...h.id('analysisPositionsChart').querySelectorAll('text')].map((t) => t.textContent);
    assert.ok(positions.includes('P5 BOT +1 lap'));
    assert.ok(positions.includes('HAD · out L2'));
    assert.ok(!positions.some((t) => /BOT.*out/.test(t)));
    assert.match(h.id('analysisStrategyTable').textContent, /Stops unknown/);
    assert.match(h.id('analysisStrategyTable').textContent, /Retired/);
    const stops = h.id('analysisStopsRows').textContent;
    assert.match(stops, /Stop not recorded/);
    assert.match(stops, /Unknown/);
    assert.match(stops, /Tyre change while suspended/);
    assert.match(h.id('analysisLapseTable').textContent, /P5 BOT \+1 lap/);
    assert.doesNotMatch(view.textContent, /undefined|NaN|\bnull\b|0:00\.000|\bP0\b|\b0 stops\b|unrecorded_stop|pace_excluded|no_context|incomplete_record/);
    assert.match(text(h, 'analysisTraceChart').concat(h.id('analysisTraceChart').innerHTML), /SC/);
    await h.analyse('ses_hostile');
    assert.equal(view.querySelectorAll('img').length, 0, 'names never become markup');
    assert.ok([...h.id('analysisAddDriver').options].some((o) => o.textContent.includes('<img src=x onerror=alert(1)>')) || h.t.state.focus.includes(hostile.drivers[1].car_index));
    assert.match(view.textContent, /Nonetheless None NaN|PIA|ALB/);
    assert.deepEqual(h.errors, []);
  } finally { h.close(); }
});
