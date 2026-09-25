import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

// Execute the shipped DRIVE functions, including their call from render38.
const html = fs.readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const start = html.indexOf('function tyreDegradationInsight(');
const end = html.indexOf('/* Race control on screen', start);
assert.ok(start > 0 && end > start);
const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)]
  .map(([, id]) => [id, { textContent: '', className: '', style: {} }]));
const context = vm.createContext({
  $: id => { assert.ok(nodes.has(id), `Missing shipped element ${id}`); return nodes.get(id); },
  dashVisible: () => false, drawPace: () => {}, damageText: () => '',
  loadRivals: () => {}, lastAuxLap: 10,
});
vm.runInContext(html.slice(start, end), context);
const insight = state => context.tyreDegradationInsight(state);
const state = (slope = .2) => ({
  current_lap: 10, mode_profile: 'race', weather: 'Clear',
  tyre: { compound: 'MEDIUM', age_laps: 7, wear: [20, 22, 23, 24] },
  analysis: { deg_model: { current_compound: 'MEDIUM', current_slope_s_per_lap: slope,
    compounds: { MEDIUM: { sample_size: 6 } } } },
});

test('Drive shows measured degradation beside tyres even with pace card hidden', () => {
  context.render38(state());
  assert.equal(nodes.get('deg').textContent, '+0.20 s/lap');
  assert.equal(nodes.get('tyreDegTag').textContent, '0.20 s slower per lap as tyres age');
  assert.match(nodes.get('tyreDegBasis').textContent, /MEDIUM · fuel-corrected · 6 clean laps/);
  assert.match(nodes.get('tyreDegTag').className, /warn/);
  const tyreCard = html.slice(html.indexOf('id="carCard"'), html.indexOf('id="fuelCard"'));
  assert.match(tyreCard, /id="tyreDegTag"/);
});

test('Negative and zero slopes retain their meaning without a plus-minus label', () => {
  assert.equal(insight(state(-.08)).value, '−0.08 s/lap');
  assert.match(insight(state(-.08)).tag, /0.08 s quicker per lap/);
  assert.equal(insight(state(0)).tag, 'Pace steady as tyres age');
  assert.equal(insight(state(-.0001)).value, '+0.00 s/lap');
});

test('Missing and malformed slopes explain learning rather than inventing zero', () => {
  for (const value of [null, undefined, NaN, Infinity, '', '0.2', false]) {
    const s = state();
    s.analysis.deg_model.current_slope_s_per_lap = value;
    const result = insight(s);
    assert.equal(result.value, 'Learning');
    assert.match(result.detail, /3\+ comparable clean laps.*fuel data/);
  }
  assert.equal(insight({}).value, 'Learning');
  const missing = state(); delete missing.analysis.deg_model.current_slope_s_per_lap;
  assert.equal(insight(missing).value, 'Learning');
});

test('An estimate uses the fitted tyres, never the final stint or summary model', () => {
  const s = state(null);
  s.strategy = { available: true, recommended: { compounds: ['MEDIUM', 'HARD'],
    stint_models: [{ deg_s_per_lap: .14, deg_source: 'track_prior' },
      { deg_s_per_lap: .55, deg_source: 'personal_history' }] },
    model_summary: { selected_stint_deg_s_per_lap: .55 } };
  assert.equal(insight(s).value, 'Est. +0.14 s/lap');
  assert.match(insight(s).tag, /^Estimated 0.14 s slower/);
  assert.match(insight(s).detail, /track model.*still learning/);
  delete s.strategy.recommended.stint_models;
  assert.equal(insight(s).value, 'Learning');
});

test('A compound change never carries the old measured or estimated degradation', () => {
  const s = state();
  s.tyre.compound = 'HARD';
  s.strategy = { available: true, recommended: { compounds: ['MEDIUM', 'HARD'],
    stint_models: [{ deg_s_per_lap: .4 }, { deg_s_per_lap: .1 }] } };
  assert.equal(insight(s).value, 'Learning');
});

test('Wet conditions explain uncertainty and identify retained dry-lap evidence', () => {
  const s = state(null);
  s.weather = 'Light rain';
  s.strategy = { available: true, recommended: { compounds: ['MEDIUM'],
    stint_models: [{ deg_s_per_lap: .14 }] } };
  assert.equal(insight(s).value, 'Wet conditions');
  assert.match(insight(s).detail, /Changing grip cannot yet be separated/);
  s.analysis.deg_model.current_slope_s_per_lap = .2;
  assert.match(insight(s).tag, /^Dry-lap trend:/);
  s.weather = 'Clear'; s.analysis.deg_model.current_slope_s_per_lap = null;
  s.strategy.weather_crossover = { wetness: .4 };
  assert.equal(insight(s).value, 'Wet conditions');
});

test('Qualifying and time trial do not present a stale race slope as measured', () => {
  for (const mode_profile of ['qualifying', 'time_trial']) {
    const result = insight({ ...state(), mode_profile });
    assert.equal(result.value, 'Not measured');
    assert.match(result.detail, /practice or race laps/);
  }
});

test('Rendering replaces an old figure with its unavailable explanation', () => {
  context.renderTyreDegradation(state());
  context.renderTyreDegradation(state(null));
  assert.equal(nodes.get('deg').textContent, 'Learning');
  assert.equal(nodes.get('tyreDegTag').textContent, 'Tyre degradation: learning');
  assert.match(nodes.get('tyreDegBasis').textContent, /3\+ comparable clean laps/);
});

test('Disconnected or stale telemetry cannot retain a current tyre trend', () => {
  for (const status of [{ connected: false }, { connected: true, telemetry_stale: true }]) {
    context.renderTyreDegradation({ ...state(), ...status });
    assert.equal(nodes.get('deg').textContent, 'Telemetry unavailable');
    assert.doesNotMatch(nodes.get('tyreDegTag').textContent, /0.20/);
    assert.match(nodes.get('tyreDegBasis').textContent, /Waiting for current telemetry/);
  }
});
