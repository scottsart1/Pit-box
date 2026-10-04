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
  esc: value => String(value),
});
vm.runInContext(html.slice(start, end), context);
vm.runInContext(html.slice(html.indexOf('function renderStrategy38('), html.indexOf('/* ---- Pre-race plan panel')), context);
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

const stintState = () => ({ ...state(null), connected: true, current_lap: 3,
  tyre: { compound: 'MEDIUM', age_laps: 2, wear: [13, 9, 12, 10] },
  strategy: { available: true, recommended: {
    compounds: ['MEDIUM', 'SOFT'], box_lap: 11, stops_remaining: 1,
    instruction: 'Box lap 11 for SOFT.',
    tyre_reason: 'Current model projects 100% by the finish; soft stint projects 76%.',
    stint_models: [{ usable_life_laps: 13, operational_wear_limit_pct: 85, feasible: true },
      { usable_life_laps: 4 }],
  } },
});

test('Driver tyre life uses the fitted stint and age, while the stop has a clear countdown', () => {
  const result = context.tyreStintInsight(stintState());
  assert.equal(result.headline, 'Est. 11 laps of tyre life');
  assert.equal(result.stop, '8 laps to planned stop');
  assert.equal(result.warning, '');
  assert.doesNotMatch(JSON.stringify(result), /100%|76%|finish|soft/i);
});

test('Tyre-life summary never borrows a final stint or a different fitted compound', () => {
  const s = stintState(); s.tyre.compound = 'HARD';
  assert.equal(context.tyreStintInsight(s).life, null);
  assert.equal(context.tyreStintInsight(s).headline, 'Tyre life still learning');
  s.tyre.compound = 'MEDIUM'; s.strategy.recommended.stint_models.shift();
  s.strategy.recommended.compounds.shift();
  assert.equal(context.tyreStintInsight(s).life, null);
});

test('Missing, partial and stale tyre readings do not look like healthy tyres', () => {
  for (const change of [{ connected: false }, { telemetry_stale: true },
    { tyre: { compound: 'MEDIUM', wear: [10, 10], age_laps: 2 } },
    { tyre: { compound: 'UNKNOWN', wear: [0, 0, 0, 0] } }]) {
    const result = context.tyreStintInsight({ ...stintState(), ...change });
    assert.equal(result.headline, 'Tyre data unavailable');
    assert.equal(result.life, null);
  }
});

test('Current wear limits and wet crossover remain explicit even with an agreed stay-out call', () => {
  const s = stintState(); s.tyre.wear[0] = 86; s.weather = 'Light rain';
  s.strategy_intent = { active: true, direction: 'stay_out', intent: 'overcut' };
  s.strategy.weather_crossover = { worth_stopping: true, compound: 'INTER' };
  assert.equal(context.tyreStintInsight(s).headline, 'Tyres at wear limit');
  const infeasible = stintState(); infeasible.strategy.recommended.stint_models[0].feasible = false;
  assert.equal(context.tyreStintInsight(infeasible).headline, 'Tyre life below the planned stint');
  context.renderStrategy38(s);
  assert.match(nodes.get('strategyMain').textContent, /overcut.*staying out/);
  assert.match(nodes.get('strategyWarning').textContent, /wear limit reached.*Weather favours inter/);
  assert.match(nodes.get('strategyWarning').className, /error/);
});

test('Driver call keeps observed compound requirement, never the future plan waiver', () => {
  const s = stintState();
  s.strategy.compound_rule = { applies: false, wet_waiver: true };
  s.strategy.observed_compound_rule = { applies: true, dry_count: 1, change_outstanding: true };
  context.renderStrategy38(s);
  assert.equal(nodes.get('compoundRule').textContent, '1/2 dry compounds used · change required');
  assert.equal(nodes.get('strategyReason').textContent, '8 laps to planned stop');
  assert.match(nodes.get('strategyTyreReason').textContent, /100%/); // retained in expandable evidence
});

test('Stop due and no-stop plans do not mislabel race distance as tyre life', () => {
  const s = stintState(); s.current_lap = 11;
  assert.equal(context.tyreStintInsight(s).stop, 'Planned stop this lap');
  s.current_lap = 12;
  assert.equal(context.tyreStintInsight(s).stop, 'Stop due · lap 11');
  s.strategy.recommended = { stops_remaining: 0, feasible: true, compounds: ['MEDIUM'] };
  assert.equal(context.tyreStintInsight(s).stop, 'No further stop planned');
  assert.equal(context.tyreStintInsight(s).life, null);
});
