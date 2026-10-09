// Session Analysis against the schema-version-2 contract: every enum value
// has driver-facing text, unknowns never become zeros, and the headline only
// claims what the recording supports.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  COMPOUNDS, PACE_EXCLUSION_TEXT, STATUS_TEXT, STOP_KIND_TEXT, compoundInfo, countText, deltaWords, exclusionText, fastestTable,
  formatGap, formatLapTime, frameAt, gapWords, hasRaceOrder, headline, heatmapModel, heatmapTable, lapBands, lapTimesModel, lapTimesTable,
  lapsDownText, lapseTable, paceTable, pitStopRows, positionText, positionsModel, positionsTable, racePaceModel,
  raceTraceModel, referenceNote, referenceOptions, resultText, schemaSupported, statusText, stopKindText, stopLossText,
  stopsText, strategyModel, strategyTable, timelapseFrames, traceReading, traceTable, fastestModel,
} from '../static/js/session-analysis-model.mjs';

const race = JSON.parse(readFileSync(new URL('./fixtures/session_analysis_race.json', import.meta.url), 'utf8'));
const clone = (value) => JSON.parse(JSON.stringify(value));
const idx = (analysis, c) => analysis.drivers.find((d) => d.code === c).car_index;

// The committed race as the backend reports it while the recording is
// unfinished: every placed car is "running" and keeps its place and laps down.
function provisionalRace() {
  const p = clone(race);
  p.session.provisional = true;
  p.session.status = 'incomplete';
  for (const d of p.drivers) {
    if (['finished', 'lapped', 'retired'].includes(d.status)) d.status = 'running';
    d.gap_to_winner_ms = null;
  }
  return p;
}

// One recorded car: unplaced, but its laps still carry position 1 and a 0 ms
// gap, as the backend's per-lap order produces for a field of one.
function singleCar({ provisional = false } = {}) {
  const p = clone(race);
  const player = p.drivers.find((d) => d.is_player);
  p.session.provisional = provisional;
  p.drivers = [{ ...player, status: provisional ? 'running' : 'unranked', finish_position: null, laps_down: null, gap_to_winner_ms: null }];
  p.laps = p.laps.filter((l) => l.car_index === player.car_index).map((l) => ({ ...l, position: l.position == null ? null : 1, gap_to_leader_ms: l.gap_to_leader_ms == null ? null : 0 }));
  p.field = { cars: 6, cars_with_laps: 1, complete: false };
  p.winner_car_index = null;
  return p;
}

// Two recorded cars, LEC and the given runner-up placed second.
function duelWith(code) {
  const p = clone(race);
  const keep = new Set([idx(race, 'LEC'), idx(race, code)]);
  p.drivers = p.drivers.filter((d) => keep.has(d.car_index)).map((d) => (d.code === code ? { ...d, finish_position: 2 } : d));
  p.laps = p.laps.filter((l) => keep.has(l.car_index));
  p.field = { cars: 2, cars_with_laps: 2, complete: true };
  return p;
}

// The enum tuples come from the backend source, so a new value fails here
// until it has text.
const backend = readFileSync(new URL('../src/pitwall/session_analysis.py', import.meta.url), 'utf8');
const tuple = (name) => {
  const match = backend.match(new RegExp(`^${name} = \\(([^)]*)\\)`, 'm'));
  assert.ok(match, `${name} is defined in session_analysis.py`);
  return [...match[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
};
const PACE_REASONS = tuple('PACE_EXCLUSION_REASONS');
const STOP_KINDS = tuple('STOP_KINDS');
const STATUSES = tuple('DRIVER_STATUSES');

// Every string inside a model, recursively.
function strings(value, out = []) {
  if (typeof value === 'string') out.push(value);
  else if (Array.isArray(value)) value.forEach((v) => strings(v, out));
  else if (value && typeof value === 'object') Object.values(value).forEach((v) => strings(v, out));
  return out;
}
const RAW_KEY = new RegExp(`\\b(${[...PACE_REASONS, ...STOP_KINDS, ...STATUSES].filter((k) => k.includes('_')).join('|')})\\b`);
const BANNED = /undefined|\bnull\b|NaN|0:00\.000|\bP0\b|\b0 stops\b|Infinity/;
function assertClean(label, value) {
  for (const text of strings(value)) {
    assert.doesNotMatch(text, BANNED, `${label}: "${text}"`);
    assert.doesNotMatch(text, RAW_KEY, `${label} shows a raw enum key: "${text}"`);
  }
}

// WCAG relative luminance and contrast of two #rrggbb colours.
const luminance = (hex) => {
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
};
const contrast = (a, b) => { const [x, y] = [luminance(a), luminance(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05); };

test('every tyre letter, the wet W included, reaches 4.5:1 on its fill', () => {
  for (const [name, info] of Object.entries(COMPOUNDS)) {
    assert.ok(contrast(info.fill, info.ink) >= 4.5, `${name}: ${contrast(info.fill, info.ink).toFixed(2)}:1`);
  }
  const c3 = compoundInfo('C3');
  assert.ok(contrast(c3.fill, c3.ink) >= 4.5);
  const css = readFileSync(new URL('../static/css/v42.css', import.meta.url), 'utf8');
  const wet = css.match(/\.sa-swatch\[data-compound="WET"\] \{ background: (#[0-9a-f]{6}); color: (#[0-9a-f]{6}); \}/i);
  assert.ok(wet && contrast(wet[1], wet[2]) >= 4.5, 'the legend swatch matches');
});

test('the page checks the payload schema', () => {
  assert.equal(schemaSupported(race), true);
  for (const old of [{}, { schema_version: 1 }, { schema_version: '2' }, null, undefined]) assert.equal(schemaSupported(old), false);
});

test('every pace exclusion, stop kind and driver status has short driver-facing text', () => {
  assert.ok(PACE_REASONS.length >= 13 && STOP_KINDS.length === 3 && STATUSES.length >= 7);
  for (const reason of PACE_REASONS) {
    const text = exclusionText(reason);
    assert.equal(text, PACE_EXCLUSION_TEXT[reason], `${reason} has its own text`);
    assert.ok(text && !text.includes('_') && text !== reason && text.length <= 40, `${reason} -> ${text}`);
  }
  assert.equal(exclusionText('some_future_reason'), 'Left out of pace');
  assert.equal(exclusionText(null), '');
  for (const kind of STOP_KINDS) {
    assert.ok(STOP_KIND_TEXT[kind], `${kind} has text`);
    for (const under of [false, true]) {
      const text = stopKindText({ kind, under_neutralisation: under });
      assert.ok(text && !text.includes('_'), `${kind} -> ${text}`);
    }
  }
  assert.equal(stopKindText({ kind: 'pit_stop', under_neutralisation: true }), 'Pit stop · neutralised');
  assert.equal(stopKindText({ kind: 'pit_stop', under_neutralisation: false }), 'Pit stop');
  assert.equal(stopKindText({ kind: 'tyre_change', under_neutralisation: true }), 'Tyre change while suspended');
  assert.equal(stopKindText({ kind: 'unrecorded_stop' }), 'Stop not recorded');
  assert.equal(stopKindText({ kind: 'something_new' }), 'Tyre change');
  assert.equal(stopLossText({ kind: 'unrecorded_stop', estimated_loss_ms: null }), 'Unknown');
  for (const status of STATUSES) {
    const driver = { status, laps_down: status === 'lapped' ? 2 : null, finish_position: status === 'no_data' ? null : 4, pit_stops: null };
    assert.ok(STATUS_TEXT[status], `${status} has text`);
    for (const text of [statusText(driver), resultText(driver), stopsText(driver)]) {
      assert.ok(text && !text.includes('_') && !/undefined|null|NaN/.test(text), `${status} -> ${text}`);
    }
  }
  assert.equal(statusText({ status: 'lapped', laps_down: 1 }), '+1 lap');
  assert.equal(statusText({ status: 'a_new_status' }), '');
});

test('heatmap cells, tables and stop rows never show a raw key for any exclusion reason', () => {
  const analysis = clone(race);
  const timedLaps = analysis.laps.filter((l) => l.lap_time_ms);
  PACE_REASONS.forEach((reason, i) => { timedLaps[i].pace_excluded = reason; });
  const heat = heatmapModel(analysis);
  const cells = heat.rows.flatMap((r) => r.cells);
  for (const reason of PACE_REASONS.filter((r) => r !== 'missing_time')) {
    assert.ok(cells.some((c) => c.text.includes(exclusionText(reason))), `${reason} is explained in a cell`);
  }
  assertClean('heatmap', heat);
  assertClean('heatmap table', heatmapTable(analysis, idx(analysis, 'LEC')));
  assertClean('pace table', paceTable(analysis));
  assertClean('lap times table', lapTimesTable(analysis, { focus: analysis.drivers.map((d) => d.car_index) }));
  assertClean('stops', pitStopRows(analysis).map((s) => [s.kindText, s.lossText, s.tyres, s.tyresText]));
  assertClean('strategy table', strategyTable(analysis));
  assertClean('positions table', positionsTable(analysis));
});

test('nulls render as a dash or as words, never as zeros', () => {
  for (const value of [null, undefined, NaN, '', 0, -5, true]) assert.equal(formatLapTime(value), '—');
  assert.equal(positionText(0), '—');
  assert.equal(positionText(null), '—');
  assert.equal(positionText(1), 'P1');
  assert.equal(formatGap(null), '—');
  assert.equal(formatGap(0), 'Leader', 'a 0 ms gap is a real value');
  assert.equal(gapWords(null), '—');
  assert.equal(gapWords(0), 'level');
  assert.equal(countText(null, 'lap'), '—');
  assert.equal(lapsDownText(null), '');
  assert.equal(lapsDownText(0), '');
  assert.equal(stopsText({ status: 'finished', pit_stops: null }), 'Stops unknown');
  assert.equal(stopsText({ status: 'finished', pit_stops: 0 }), '0 stops', 'a known zero is shown');
  assert.equal(stopsText({ status: 'finished', pit_stops: 1 }), '1 stop');
  assert.equal(stopsText({ status: 'retired', pit_stops: 1 }), 'Retired', 'a retirement is not a stop count');
  assert.equal(stopLossText({ kind: 'pit_stop', estimated_loss_ms: null }), '—');
  assert.equal(stopLossText({ kind: 'pit_stop', estimated_loss_ms: -150 }), '−0.1 s');
  assert.equal(deltaWords(null), '—');
  assert.equal(compoundInfo(undefined).letter, '?');

  // A payload where every optional number is missing.
  const empty = clone(race);
  for (const lap of empty.laps) Object.assign(lap, { lap_time_ms: null, s1_ms: null, s2_ms: null, s3_ms: null, tyre_age_laps: null, position: null, gap_to_leader_ms: null, interval_ms: null, segment_time_ms: null, compound: null });
  for (const d of empty.drivers) Object.assign(d, { finish_position: null, pit_stops: null, race_time_ms: null, gap_to_winner_ms: null, ideal_lap_ms: null, best_lap_ms: null, laps_down: null });
  for (const s of empty.pit_stops) s.estimated_loss_ms = null;
  for (const r of empty.fastest_laps) Object.assign(r, { ideal_lap_ms: null });
  empty.ideal_lap = { best_sectors_ms: null, best_driver_ideal_ms: null };
  empty.official_result = { player_position: null, derived_player_position: null, agrees: null };
  const focus = empty.drivers.map((d) => d.car_index);
  const frames = timelapseFrames(empty);
  assertClean('headline', headline(empty));
  assertClean('positions', positionsModel(empty, { focus }));
  assertClean('trace table', traceTable(empty, { focus }));
  assertClean('lap times', lapTimesModel(empty, { focus }));
  assertClean('lap times table', lapTimesTable(empty, { focus }));
  assertClean('heatmap', heatmapModel(empty));
  assertClean('strategy', strategyModel(empty, { width: 400 }));
  assertClean('timelapse table', lapseTable(empty, frames));
  assertClean('fastest', fastestTable(empty));
  assertClean('positions table', positionsTable(empty));
  assertClean('strategy table', strategyTable(empty));
  assertClean('stops', pitStopRows(empty).map((s) => [s.kindText, s.lossText]));
  const head = headline(empty);
  assert.ok(head.kpis.find((k) => /Your result/.test(k.label)).detail.includes('Stops unknown'));
});

test('gaps always say behind or ahead, with positive meaning behind', () => {
  assert.equal(gapWords(253), '0.253 s behind');
  assert.equal(gapWords(-1200), '1.200 s ahead');
  assert.equal(gapWords(2000, { reference: 'LEC' }), '2.000 s behind LEC');
  assert.equal(gapWords(-500, { reference: 'LEC' }), '0.500 s ahead of LEC');
  const focus = [idx(race, 'LEC'), idx(race, 'PIA')];
  const reading = traceReading(race, { focus, lap: 12 });
  assert.equal(reading[0].value, 'Leader');
  assert.match(reading[1].value, /^\d+\.\d{3} s behind$/);
  const toPia = traceReading(race, { focus, reference: idx(race, 'PIA'), lap: 12 });
  assert.match(toPia[0].value, /ahead of PIA$/);
  assert.equal(toPia[1].value, 'Reference');
  const suspended = traceReading(race, { focus, lap: 6 });
  assert.ok(suspended.every((r) => r.value === 'Race suspended'));
  const table = traceTable(race, { focus });
  assert.ok(table.rows.some((row) => row.some((cell) => / behind$/.test(cell))));
  // A two-car race where the runner-up is 253 ms behind the winner.
  const duel = headline({ ...clone(race), drivers: [
    { ...race.drivers[0], finish_position: 1, gap_to_winner_ms: 0, status: 'finished' },
    { ...race.drivers[1], finish_position: 2, gap_to_winner_ms: 253, status: 'finished' },
  ] });
  assert.match(duel.title, /wins by 0\.253 s$/);
  assert.equal(duel.kpis[0].detail, 'PIA 0.253 s behind');
});

test('headline: a provisional session or a partial field never claims a win', () => {
  const provisional = clone(race);
  provisional.session.provisional = true;
  const p = headline(provisional);
  assert.doesNotMatch(p.title, /wins/);
  assert.match(p.title, /Provisional order/);
  assert.equal(p.kpis[0].label, 'Provisional order');
  assert.match(p.subtitle, /provisional order: the recording did not finish/);
  assert.equal(p.kpis.find((k) => /^Your/.test(k.label)).label, 'Your position (provisional)');

  const partial = clone(race);
  partial.field = { cars: 6, cars_with_laps: 4, complete: false };
  const q = headline(partial);
  assert.doesNotMatch(q.title, /wins/);
  assert.equal(q.kpis[0].label, 'Leading recorded car');
  assert.match(q.subtitle, /4 of 6 cars recorded/);
  assert.equal(q.kpis.find((k) => /Fastest/.test(k.label)).label, 'Fastest recorded lap');
});

test('headline: an unfinished recording claims only the fastest recorded lap', () => {
  // record_complete only covers gaps and flashbacks; it stays true here.
  const p = provisionalRace();
  assert.equal(p.record_complete, true);
  const h = headline(p);
  assert.equal(h.kpis.find((k) => /Fastest/.test(k.label)).label, 'Fastest recorded lap');
  assert.ok(!h.kpis.some((k) => k.label === 'Fastest lap'));
  const practice = clone(race);
  practice.session = { ...race.session, is_race: false, session_type: 'Practice 1', provisional: true };
  assert.equal(headline(practice).kpis.find((k) => /Fastest/.test(k.label)).label, 'Fastest recorded lap');
});

test('headline: the runner-up reads by its status; a retirement is never laps down', () => {
  const retired = headline(duelWith('HAD'));
  assert.equal(retired.kpis[0].label, 'Winner');
  assert.equal(retired.kpis[0].detail, 'HAD retired');
  assert.doesNotMatch(JSON.stringify(retired), /\+10 laps/);
  const lapped = headline(duelWith('BOT'));
  assert.equal(lapped.kpis[0].detail, 'BOT +1 lap');
  const close = headline(duelWith('PIA'));
  assert.match(close.kpis[0].detail, /^PIA \d+\.\d{3} s behind$/);
  // Provisional: the runner-up is "running" and only its laps down are known.
  const provisional = provisionalRace();
  provisional.drivers.find((d) => d.code === 'PIA').finish_position = 7;
  provisional.drivers.find((d) => d.code === 'BOT').finish_position = 2;
  assert.equal(headline(provisional).kpis[0].detail, 'BOT +1 lap');
});

test('headline: a single recorded car makes no order claim', () => {
  const single = clone(race);
  single.drivers = [{ ...race.drivers[0], status: 'unranked' }];
  single.field = { cars: 1, cars_with_laps: 1, complete: true };
  single.laps = single.laps.filter((l) => l.car_index === single.drivers[0].car_index);
  const h = headline(single);
  assert.doesNotMatch(h.title, /wins|leads/);
  assert.ok(!h.kpis.some((k) => /Winner|leading|Provisional/i.test(k.label)));
  const you = h.kpis.find((k) => /^Your/.test(k.label));
  assert.equal(you.value, '—');
  assert.match(you.detail, /only recorded car/);
  assert.doesNotMatch(h.subtitle, /order derived/);
});

test('a single recorded car has no race order: no frames to replay, whatever its laps say', () => {
  assert.equal(hasRaceOrder(race), true);
  assert.equal(hasRaceOrder(provisionalRace()), true);
  for (const single of [singleCar(), singleCar({ provisional: true })]) {
    assert.equal(hasRaceOrder(single), false, single.drivers[0].status);
    assert.ok(single.laps.some((l) => l.position === 1), 'its laps still carry P1');
    assert.deepEqual(timelapseFrames(single), []);
    assert.deepEqual(lapseTable(single, timelapseFrames(single)).rows, []);
    const h = headline(single);
    assert.doesNotMatch(JSON.stringify(h), /\bP1\b|wins|leads|Leader/);
  }
  const practice = clone(race);
  practice.session = { ...race.session, is_race: false };
  assert.equal(hasRaceOrder(practice), false, 'practice has no race order');
  assert.equal(hasRaceOrder(null), false);
});

test('headline: the official result wins and a disagreement is explained', () => {
  const disagree = clone(race);
  disagree.official_result = { player_position: 3, derived_player_position: 1, agrees: false };
  const you = headline(disagree).kpis.find((k) => /^Your/.test(k.label));
  assert.equal(you.label, 'Your result');
  assert.equal(you.value, 'P3');
  assert.match(you.detail, /derived order differs \(penalties not applied\)/);
  const agree = clone(race);
  agree.official_result = { player_position: 1, derived_player_position: 1, agrees: true };
  const same = headline(agree).kpis.find((k) => /^Your/.test(k.label));
  assert.deepEqual([same.label, same.value], ['Your result', 'P1']);
  assert.match(same.detail, /official result/);
  const derived = headline(race).kpis.find((k) => /^Your/.test(k.label));
  assert.equal(derived.label, 'Your result (derived)');
});

test('headline: when the official result disputes the derived order, nobody "wins" on lap times', () => {
  // The player (LEC) is first on lap times, but the official result is P3.
  const disagree = clone(race);
  assert.equal(disagree.winner_car_index, idx(race, 'LEC'));
  disagree.official_result = { player_position: 3, derived_player_position: 1, agrees: false };
  const h = headline(disagree);
  assert.doesNotMatch(h.title, /wins/);
  assert.match(h.title, /first on lap times$/);
  assert.equal(h.kpis[0].label, 'First on lap times');
  assert.equal(h.kpis[0].value, 'LEC');
  assert.ok(!h.kpis.some((k) => k.label === 'Winner'));
  assert.deepEqual(h.kpis.slice(1, 2).map((k) => [k.label, k.value]), [['Your result', 'P3']]);
  // A disagreement elsewhere in the order unsettles the derived winner too.
  const lower = clone(race);
  lower.official_result = { player_position: 2, derived_player_position: 1, agrees: false };
  lower.winner_car_index = idx(race, 'PIA');
  assert.doesNotMatch(headline(lower).title, /wins/);
  // Agreement, or no official result at all, keeps the win.
  const agree = clone(race);
  agree.official_result = { player_position: 1, derived_player_position: 1, agrees: true };
  assert.match(headline(agree).title, /wins/);
  assert.equal(headline(agree).kpis[0].label, 'Winner');
  assert.match(headline(race).title, /wins/);
});

test('headline: incomplete records, retirements and race control', () => {
  const incomplete = clone(race);
  incomplete.record_complete = false;
  assert.equal(headline(incomplete).kpis.find((k) => /Fastest/.test(k.label)).label, 'Fastest recorded lap');
  assert.equal(headline(race).kpis.find((k) => /Fastest/.test(k.label)).label, 'Fastest lap');

  const retired = clone(race);
  const player = retired.drivers.find((d) => d.is_player);
  Object.assign(player, { status: 'retired', finish_position: 6, pit_stops: 1 });
  const you = headline(retired).kpis.find((k) => /^Your/.test(k.label));
  assert.equal(you.value, 'DNF');
  assert.match(you.detail, /Retired/);
  assert.doesNotMatch(you.detail, /stop/);

  const unknownStops = clone(race);
  unknownStops.drivers.find((d) => d.is_player).pit_stops = null;
  assert.match(headline(unknownStops).kpis.find((k) => /^Your/.test(k.label)).detail, /Stops unknown/);

  const blind = clone(race);
  blind.race_control = { available: false, covered_from_lap: null, neutralisations: [], safety_car_laps: [], vsc_laps: [], red_flag_laps: [] };
  const b = headline(blind);
  const neutral = b.kpis.find((k) => k.label === 'Neutralised');
  assert.match(neutral.detail, /safety cars not recorded/);
  assert.match(neutral.detail, /red flag 6–7/, 'the red flag still comes from the suspended laps');
  assert.match(b.subtitle, /safety cars not recorded/);

  const vsc = clone(race);
  vsc.race_control = { ...race.race_control, vsc_laps: [9, 10], neutralisations: [...race.race_control.neutralisations, { kind: 'VSC', first_lap: 9, last_lap: 10 }] };
  const v = headline(vsc);
  assert.match(v.kpis.find((k) => k.label === 'Neutralised').detail, /VSC 9–10/);
  assert.match(v.subtitle, /VSC on laps 9–10/);
  const partialCover = clone(race);
  partialCover.race_control.covered_from_lap = 5;
  assert.match(headline(partialCover).subtitle, /race control recorded from lap 5/);
});

test('lap bands: SC and VSC from race control, the red flag from suspended laps', () => {
  const analysis = clone(race);
  analysis.race_control.vsc_laps = [9, 10];
  const bands = lapBands(analysis, (l) => l * 10);
  assert.deepEqual(bands.map((b) => [b.kind, b.label, b.first, b.last]), [['sc', 'SC', 3, 3], ['vsc', 'VSC', 9, 10], ['suspended', 'RED', 6, 7]]);
  delete analysis.race_control;
  analysis.neutralised_laps = [3];
  assert.deepEqual(lapBands(analysis, (l) => l).map((b) => b.label), ['SC', 'RED']);
});

test('lapped finishers stay classified in the timelapse; only a retirement leaves it', () => {
  const frames = timelapseFrames(race);
  const final = frames[frames.length - 1];
  const order = race.drivers.filter((d) => d.status !== 'retired' && d.finish_position).sort((a, b) => a.finish_position - b.finish_position);
  assert.deepEqual(final.entries.map((e) => e.car_index), order.map((d) => d.car_index), 'the flag shows every classified car in finish order');
  const bot = final.entries.find((e) => e.car_index === idx(race, 'BOT'));
  assert.deepEqual([bot.label, bot.laps_down, bot.gap_ms], ['P5', 1, null]);
  assert.ok(!final.entries.some((e) => e.car_index === idx(race, 'HAD')));
  // Between laps 11 and 12 the lapped car stays; between 2 and 3 the retired car goes.
  assert.ok(frameAt(frames, 11.5).entries.some((e) => e.car_index === idx(race, 'BOT')));
  assert.ok(frames[1].entries.some((e) => e.car_index === idx(race, 'HAD')));
  assert.ok(!frameAt(frames, 2.5).entries.some((e) => e.car_index === idx(race, 'HAD')));
  const table = lapseTable(race, frames);
  assert.match(table.rows[table.rows.length - 1][1], /P5 BOT \+1 lap$/);
  assert.match(table.rows[table.rows.length - 1][2], /^Finish/);
  assert.match(table.rows[5][2], /Race suspended/);
  assert.match(table.rows[2][2], /Safety car/);
});

test('provisional timelapse: every placed car stays to the last recorded lap with its laps down, and that lap is not the finish', () => {
  const p = provisionalRace();
  const frames = timelapseFrames(p);
  assert.equal(frames.length, 12);
  const last = frames[frames.length - 1];
  const placed = p.drivers.filter((d) => d.finish_position).sort((a, b) => a.finish_position - b.finish_position);
  assert.deepEqual(last.entries.map((e) => e.car_index), placed.map((d) => d.car_index), 'the last frame holds every placed car in order');
  const at = (frame, c) => frame.entries.find((e) => e.car_index === idx(p, c));
  assert.deepEqual([at(last, 'BOT').label, at(last, 'BOT').laps_down, at(last, 'BOT').gap_ms], ['P5', 1, null]);
  assert.deepEqual([at(last, 'HAD').label, at(last, 'HAD').laps_down], ['P6', 10]);
  // Not known to have retired: it holds its place after its record ends.
  assert.ok(frames.every((f) => at(f, 'HAD')), 'HAD is in every frame');
  assert.equal(at(frames[2], 'HAD').laps_down, 1);
  assert.ok(frames.flatMap((f) => f.entries).every((e) => !e.out), 'nobody is shown leaving');
  assert.ok(frameAt(frames, 11.5).entries.some((e) => e.car_index === idx(p, 'BOT')));
  const table = lapseTable(p, frames);
  const [lap, order, note] = table.rows[table.rows.length - 1];
  assert.equal(lap, '12');
  assert.match(order, /P5 BOT \+1 lap, P6 HAD \+10 laps$/);
  assert.doesNotMatch(note, /Finish/);
  assert.match(note, /^Last recorded lap/);
  // A started but unfinished lap after the last ordered lap has no order:
  // the replay still ends at the last ordered lap, with the whole field.
  const unfinished = provisionalRace();
  const lec = unfinished.laps.find((l) => l.car_index === idx(unfinished, 'LEC') && l.lap_number === 12);
  unfinished.laps.push({ ...lec, lap_number: 13, lap_id: 'lap_0_13', lap_time_ms: null, s1_ms: null, s2_ms: null, s3_ms: null, position: null, gap_to_leader_ms: null, interval_ms: null, segment_time_ms: null, pace_excluded: 'missing_time' });
  const more = timelapseFrames(unfinished);
  assert.equal(more.length, 12);
  assert.equal(more[11].entries.length, placed.length);
});

test('positions label every status: lapped, retired, incomplete record and provisional', () => {
  const analysis = clone(race);
  const alb = analysis.drivers.find((d) => d.code === 'ALB');
  Object.assign(alb, { status: 'incomplete_record', finish_position: null, record_gap_from_lap: 10 });
  for (const lap of analysis.laps) if (lap.car_index === alb.car_index && lap.lap_number >= 10) lap.position = null;
  const labels = positionsModel(analysis).labels;
  const text = (c) => labels.find((l) => l.car_index === idx(analysis, c)).text;
  assert.equal(text('ALB'), 'ALB · record ends L9');
  assert.equal(text('BOT'), 'P5 BOT +1 lap');
  assert.equal(text('HAD'), 'HAD · out L2');
  assert.equal(resultText(alb), 'Record incomplete');
  assert.equal(resultText(analysis.drivers.find((d) => d.code === 'BOT')), 'P5 · +1 lap');
});

test('race pace: identical laps give a finite box with n, far outliers stay inside the chart', () => {
  const flat = { drivers: [{ car_index: 0, code: 'PLA' }], race_pace: [{ car_index: 0, n: 5, min_ms: 89400, q1_ms: 89400, median_ms: 89400, q3_ms: 89400, max_ms: 89400, whisker_low_ms: 89400, whisker_high_ms: 89400, outliers: [], excluded: {} }] };
  for (const horizontal of [false, true]) {
    const model = racePaceModel(flat, { width: horizontal ? 358 : 900, height: 300, horizontal });
    const box = model.boxes[0];
    for (const key of ['q1', 'q3', 'median', 'whiskerLow', 'whiskerHigh', 'cross', 'crossStart', 'crossEnd']) assert.ok(Number.isFinite(box[key]), `${key} is finite`);
    assert.equal(box.nText, '5 laps');
    assert.ok(model.ticks.length >= 1 && model.ticks.every((t) => Number.isFinite(t.pos)));
  }
  // The time axis at phone width (a 390 px phone leaves the chart 344 px)
  // keeps at least three labels, spaced so they never touch (7.25 s is a real
  // 22-car race). Very short spans or narrower charts may fall back to two
  // labels where three would touch, but never to a lone label.
  for (const span of [600, 1000, 1600, 2600, 4000, 7248, 8200, 9000, 12500, 21000]) {
    for (const offset of [0, 37, 250, 518, 1300]) {
      const low = 91000 + offset;
      const paced = { drivers: [{ car_index: 0, code: 'AAA' }, { car_index: 1, code: 'BBB' }], race_pace: [0, 1].map((car) => ({
        car_index: car, n: 8, q1_ms: low + span * 0.3, median_ms: low + span * 0.4, q3_ms: low + span * 0.5,
        whisker_low_ms: car ? low + span * 0.2 : low, whisker_high_ms: car ? low + span : low + span * 0.6, outliers: [], excluded: {},
      })) };
      for (const width of [300, 344, 390, 520]) {
        const { ticks } = racePaceModel(paced, { width, horizontal: true });
        assert.ok(ticks.length >= (width >= 344 && span >= 1600 ? 3 : 2), `${span} ms at ${width} px: ${ticks.map((t) => t.label).join(' ')}`);
        const spacing = Math.min(...ticks.slice(1).map((t, i) => t.pos - ticks[i].pos));
        const widest = Math.max(...ticks.map((t) => t.label.length));
        assert.ok(spacing >= widest * 5.5 + 4, `${span} ms at ${width} px: ${ticks.map((t) => t.label).join(' ')} only ${spacing.toFixed(0)} px apart`);
      }
    }
  }
  const spike = clone(race);
  spike.race_pace[0].outliers = [{ lap_number: 7, lap_time_ms: 140000 }, { lap_number: 8, lap_time_ms: 92700 }];
  for (const horizontal of [false, true]) {
    const model = racePaceModel(spike, { width: horizontal ? 358 : 900, height: 300, horizontal });
    const [far, near] = model.boxes[0].outliers;
    assert.equal(far.off, 'high');
    assert.equal(near.off, null);
    const edge = horizontal ? model.plot.right : model.plot.top;
    assert.ok(Math.abs(far.pos - edge) < 0.01, 'an off-scale outlier sits on the plot edge');
    for (const o of model.boxes.flatMap((b) => b.outliers)) {
      if (horizontal) assert.ok(o.pos >= model.plot.left && o.pos <= model.plot.right);
      else assert.ok(o.pos >= model.plot.top && o.pos <= model.plot.bottom);
    }
  }
});

test('heatmap: a missing lap is "not recorded", and only traced timed laps open Lap Lab', () => {
  const analysis = clone(race);
  const lec = idx(analysis, 'LEC');
  analysis.laps = analysis.laps.filter((l) => !(l.car_index === lec && l.lap_number === 10));
  const model = heatmapModel(analysis);
  const row = (c) => model.rows.find((r) => r.code === c);
  const gap = row('LEC').cells[9];
  assert.deepEqual([gap.state, gap.openable], ['missing', false]);
  assert.match(gap.text, /not recorded/);
  const untraced = row('BOT').cells[8];
  assert.equal(untraced.openable, false);
  assert.match(untraced.text, /timing only — no telemetry/);
  const noTime = row('HAD').cells[2];
  assert.equal(noTime.openable, false);
  assert.match(noTime.text, /no lap time/);
  assert.equal(row('LEC').cells[8].openable, true);
  const lecTable = heatmapTable(analysis, lec);
  assert.ok(lecTable.rows.some((r) => r[4]?.lapId === 'lap_0_9' && r[4].text === 'Open in Lap Lab'));
  assert.ok(lecTable.rows.some((r) => r[0] === '10' && r[3] === 'Not recorded'));
  const botTable = heatmapTable(analysis, idx(analysis, 'BOT'));
  assert.ok(botTable.rows.filter((r) => Number(r[0]) > 2).every((r) => typeof r[4] === 'string'), 'untraced laps offer no Lap Lab button');
  assert.ok(botTable.rows.some((r) => r[4] === 'Timing only'));
});

test('strategy names compounds even without letters, and C-labels are not "no tyre data"', () => {
  const analysis = clone(race);
  analysis.stints[0].stints[1].compound = 'C3';
  const model = strategyModel(analysis, { width: 1200 });
  const stint = model.rows[0].stints[1];
  assert.deepEqual([stint.letter, stint.label], ['C3', 'C3 compound']);
  const table = strategyTable(analysis);
  assert.match(table.rows[0][2], /C3 compound L8–12/);
  const narrow = strategyModel(analysis, { width: 120 });
  for (const s of narrow.rows.flatMap((r) => r.stints)) assert.ok(s.text.includes(s.label));
  const stopsColumn = Object.fromEntries(table.rows.map((r) => [r[0], r[1]]));
  assert.deepEqual([stopsColumn.LEC, stopsColumn.BOT, stopsColumn.HAD], ['1 stop', 'Stops unknown', 'Retired']);
});

test('the gap reference list leaves out retired cars and explains a short record', () => {
  const options = referenceOptions(race);
  const codes = options.map((o) => o.label);
  assert.ok(!codes.some((c) => c.startsWith('HAD')), 'a retired car is not offered');
  assert.ok(codes.includes('BOT · +1 lap'));
  assert.ok(codes.includes('LEC (you)'));
  assert.ok(options.every((o) => typeof o.value === 'string'));
  assert.equal(referenceNote(race, String(idx(race, 'BOT'))), 'Gaps to BOT end at lap 11, its last timed lap.');
  assert.equal(referenceNote(race, String(idx(race, 'LEC'))), '');
  assert.equal(referenceNote(race, 'leader'), '');
  const model = raceTraceModel(race, { focus: [idx(race, 'LEC')], reference: idx(race, 'BOT') });
  assert.ok(model.lines.find((l) => l.car_index === idx(race, 'LEC')).runs.length > 0, 'the trace still draws against a lapped reference');
});

test('practice sessions: lap times follow focus without race positions', () => {
  const practice = {
    schema_version: 2,
    session: { is_race: false, session_type: 'Practice 2', track_name: 'Spa', provisional: true },
    field: { cars: 2, cars_with_laps: 2, complete: true },
    drivers: [
      { car_index: 0, code: 'PLA', is_player: true, status: 'running', best_lap_ms: 89400, laps_recorded: 5, laps_completed: 5, pit_stops: 0 },
      { car_index: 1, code: 'NOR', is_player: false, status: 'running', best_lap_ms: 89400, laps_recorded: 5, laps_completed: 4, pit_stops: 0 },
    ],
    laps: [1, 2, 3, 4, 5].flatMap((n) => [0, 1].map((car) => ({
      car_index: car, lap_number: n, lap_id: `l${car}${n}`, lap_time_ms: car === 1 && n === 5 ? null : 89400, traced: true, position: null,
      gap_to_leader_ms: null, pace_excluded: car === 1 && n === 5 ? 'missing_time' : null, compound: 'MEDIUM',
    }))),
    race_pace: [{ car_index: 0, n: 5, median_ms: 89400, q1_ms: 89400, q3_ms: 89400, whisker_low_ms: 89400, whisker_high_ms: 89400, min_ms: 89400, max_ms: 89400, outliers: [], excluded: {} }],
    fastest_laps: [{ car_index: 0, lap_number: 1, lap_id: 'l01', lap_time_ms: 89400, delta_to_fastest_ms: 0, traced: true }],
    race_control: { available: false, safety_car_laps: [], vsc_laps: [], neutralisations: [], red_flag_laps: [] },
    suspended_laps: [], segments: [], stints: [], pit_stops: [],
  };
  const one = lapTimesModel(practice, { focus: [0] });
  const both = lapTimesModel(practice, { focus: [0, 1] });
  assert.equal(one.series.length, 1);
  assert.equal(both.series.length, 2);
  assert.equal(both.series[1].points.length, 4);
  const table = lapTimesTable(practice, { focus: [0, 1] });
  assert.deepEqual(table.rows[4], ['5', '1:29.400', 'No lap time']);
  const head = headline(practice);
  assert.equal(head.title, 'Spa · Practice 2');
  assert.ok(!head.kpis.some((k) => k.label === 'Neutralised'));
  assert.match(head.subtitle, /the recording did not finish/);
  assertClean('practice positions', positionsModel(practice));
  assertClean('practice fastest', fastestModel(practice));
});
