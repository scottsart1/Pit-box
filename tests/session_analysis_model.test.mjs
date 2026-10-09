import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import {
  COMPOUNDS, compoundInfo, continuousRuns, defaultFocus, driverStyles, fastestModel, formatGap, formatLapTime,
  frameAt, gapSeries, headline, heatColour, heatmapModel, lapAxisTicks, lapBands, lapTimesModel, niceTicks,
  pitStopRows, positionsModel, raceTraceModel, racePaceModel, scale, strategyModel, timelapseFrames, HEAT_EXCLUDED,
} from '../static/js/session-analysis-model.mjs';

// Produced by tests/session_analysis_fixture.py through the real backend.
const race = JSON.parse(readFileSync(new URL('./fixtures/session_analysis_race.json', import.meta.url), 'utf8'));
const code = (analysis, carIndex) => analysis.drivers.find((d) => d.car_index === carIndex).code;
const idx = (analysis, c) => analysis.drivers.find((d) => d.code === c).car_index;

test('formatting: lap times, gaps and compounds including inters, wets and C-labels', () => {
  assert.equal(formatLapTime(91649), '1:31.649');
  assert.equal(formatLapTime(null), '—');
  assert.equal(formatLapTime(0), '—');
  assert.equal(formatGap(0), 'Leader');
  assert.equal(formatGap(253), '+0.253 s');
  assert.equal(formatGap(-1200), '−1.200 s');
  assert.equal(compoundInfo('INTER').letter, 'I');
  assert.equal(compoundInfo('wet').letter, 'W');
  assert.equal(compoundInfo(null), COMPOUNDS.UNKNOWN);
  assert.equal(compoundInfo('UNKNOWN'), COMPOUNDS.UNKNOWN);
  assert.equal(compoundInfo('mystery'), COMPOUNDS.UNKNOWN);
  // A C-label is a recorded compound, not missing tyre data.
  assert.deepEqual([compoundInfo('C3').letter, compoundInfo('c3').label], ['C3', 'C3 compound']);
  assert.notEqual(compoundInfo('C3').fill, COMPOUNDS.UNKNOWN.fill);
});

test('scales and ticks are round and cover the domain', () => {
  const x = scale(0, 10, 100, 200);
  assert.equal(x(5), 150);
  assert.deepEqual(niceTicks(91.2, 96.4, 5), [92, 93, 94, 95, 96]);
  assert.deepEqual(niceTicks(0, 47, 5), [0, 10, 20, 30, 40]);
  assert.deepEqual(niceTicks(3, 3), [3]);
  assert.deepEqual(lapAxisTicks(31), [1, 5, 10, 15, 20, 25, 31]);
  assert.deepEqual(lapAxisTicks(12), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]);
  assert.deepEqual(lapAxisTicks(1), [1]);
  // A narrow chart thins the labels but keeps lap 1 and the last lap.
  const thin = lapAxisTicks(12, 5);
  assert.ok(thin.length <= 5 && thin[0] === 1 && thin[thin.length - 1] === 12, JSON.stringify(thin));
  assert.ok(lapAxisTicks(60, 6).length <= 8);
});

test('team colours: teammates share a colour, the second is dashed, unknown teams get stable fallbacks', () => {
  const styles = driverStyles(race);
  const nor = styles.get(idx(race, 'NOR'));
  const pia = styles.get(idx(race, 'PIA'));
  assert.equal(nor.colour, pia.colour);
  assert.notEqual(nor.dashed, pia.dashed);
  assert.equal(styles.get(idx(race, 'LEC')).dashed, false);
  const custom = { drivers: [{ car_index: 0, team_id: 9001, code: 'AAA' }, { car_index: 1, team_id: 9002, code: 'BBB' }, { car_index: 2, team_id: 9001, code: 'CCC' }] };
  const fallback = driverStyles(custom);
  assert.equal(fallback.get(0).colour, fallback.get(2).colour);
  assert.notEqual(fallback.get(0).colour, fallback.get(1).colour);
  assert.equal(fallback.get(2).dashed, true);
});

test('default focus is the player and the cars that finished around them, never more than four', () => {
  const focus = defaultFocus(race);
  assert.equal(focus[0], idx(race, 'LEC'));
  assert.equal(focus.length, 4);
  assert.deepEqual(new Set(focus), new Set(['LEC', 'PIA', 'ALB', 'NOR'].map((c) => idx(race, c))));
  const noPlayer = { drivers: race.drivers.map((d) => ({ ...d, is_player: false })) };
  assert.deepEqual(defaultFocus(noPlayer, 2), race.drivers.filter((d) => d.finish_position).sort((a, b) => a.finish_position - b.finish_position).slice(0, 2).map((d) => d.car_index));
});

test('race pace boxes follow the API order and keep thin samples out of the boxes', () => {
  const model = racePaceModel(race, { width: 1200, height: 340 });
  const apiOrder = race.race_pace.filter((r) => r.n >= 5).map((r) => code(race, r.car_index));
  assert.deepEqual(model.boxes.map((b) => b.code), apiOrder);
  assert.ok(model.thin.some((t) => t.code === 'HAD' && t.n === 1));
  for (const box of model.boxes) {
    assert.ok(box.whiskerHigh <= box.q3 && box.q3 <= box.median && box.median <= box.q1 && box.q1 <= box.whiskerLow, `${box.code} box is ordered top to bottom`);
    assert.ok(box.crossEnd - box.crossStart <= 24, 'boxes are at most 24 px wide');
    assert.equal(box.nText, `${box.n} laps`);
  }
  const horizontal = racePaceModel(race, { width: 358, horizontal: true });
  assert.equal(horizontal.horizontal, true);
  for (const box of horizontal.boxes) assert.ok(box.whiskerLow <= box.q1 && box.q1 <= box.median && box.median <= box.q3 && box.q3 <= box.whiskerHigh);
  assert.deepEqual(racePaceModel({ race_pace: [] }).boxes, []);
  // A driver with laps but no pace lap at all is listed, not silently dropped
  // (the real Singapore race: BOT and VER had every lap excluded).
  const unpaced = { ...race, race_pace: race.race_pace.filter((r) => code(race, r.car_index) !== 'ALB') };
  assert.ok(racePaceModel(unpaced, { width: 1200 }).thin.some((t) => t.code === 'ALB' && t.n === 0));
  assert.ok(!racePaceModel(race, { width: 1200 }).thin.some((t) => t.n === 0 && code(race, t.car_index) === 'LEC'));
});

test('race trace never draws a line across the red flag and resets at the restart', () => {
  const series = gapSeries(race);
  const lec = series.get(idx(race, 'LEC'));
  assert.ok(!lec.some((p) => race.suspended_laps.includes(p.lap)), 'no gap is claimed while suspended');
  const runs = continuousRuns(race, lec);
  assert.ok(runs.length >= 2, 'the trace splits at the suspension');
  assert.ok(runs.every((run) => run.every((p, i) => i === 0 || p.lap === run[i - 1].lap + 1)));
  const model = raceTraceModel(race, { focus: [idx(race, 'LEC'), idx(race, 'NOR')] });
  const red = model.bands.find((b) => b.kind === 'suspended');
  assert.ok(red && red.first === 6 && red.last === 7 && red.label === 'RED');
  assert.ok(model.bands.some((b) => b.kind === 'sc' && b.first === 3 && b.label === 'SC'));
  const focusLines = model.lines.filter((l) => l.focus);
  assert.equal(focusLines.length, 2);
  assert.equal(model.lines[model.lines.length - 1].focus, true, 'focus lines are drawn last, on top');
  for (const line of model.lines) for (const run of line.runs) assert.match(run, /^[\d.]+,[\d.]+( [\d.]+,[\d.]+)+$/);
  assert.deepEqual(model.endLabels.map((l) => l.text).sort(), ['LEC', 'NOR'], 'focus lines carry their driver code');
});

test('race trace against a chosen driver compares only within one racing segment', () => {
  const nor = idx(race, 'NOR');
  const series = gapSeries(race, nor);
  assert.ok(series.get(nor).every((p) => p.gap === 0));
  const lec = series.get(idx(race, 'LEC'));
  const lap8 = race.laps.find((l) => l.car_index === idx(race, 'LEC') && l.lap_number === 8);
  const nor8 = race.laps.find((l) => l.car_index === nor && l.lap_number === 8);
  assert.equal(lec.find((p) => p.lap === 8).gap, lap8.segment_time_ms - nor8.segment_time_ms);
});

test('positions: every completed lap has a line point, a retirement ends "out", a lapped finisher is classified', () => {
  const model = positionsModel(race, { focus: [idx(race, 'LEC')] });
  const had = model.labels.find((l) => l.car_index === idx(race, 'HAD'));
  assert.match(had.text, /out L2/);
  const bot = model.labels.find((l) => l.car_index === idx(race, 'BOT'));
  assert.equal(bot.text, 'P5 BOT +1 lap');
  const lecLabel = model.labels.find((l) => l.car_index === idx(race, 'LEC'));
  assert.match(lecLabel.text, /^P1 LEC$/);
  assert.equal(model.lines.filter((l) => l.focus).length, 1);
  assert.equal(model.yTicks[0].label, 'P1');
  assert.ok(model.labels.filter((l) => / out /.test(l.text)).every((l) => race.drivers.find((d) => d.car_index === l.car_index).status === 'retired'));
});

test('tyre strategy: stints tile the race, letters only where they fit, unknown tyres are grey', () => {
  const model = strategyModel(race, { width: 1200 });
  const nor = model.rows.find((r) => r.code === 'NOR');
  assert.deepEqual(nor.stints.map((s) => s.letter), ['M', 'M', 'H']);
  for (const row of model.rows) {
    for (let i = 1; i < row.stints.length; i += 1) assert.ok(row.stints[i].x >= row.stints[i - 1].x + row.stints[i - 1].width, `${row.code} stints do not overlap`);
  }
  const bot = model.rows.find((r) => r.code === 'BOT');
  assert.equal(bot.stints[bot.stints.length - 1].fill, COMPOUNDS.UNKNOWN.fill);
  const narrow = strategyModel(race, { width: 140 });
  const letterless = narrow.rows.flatMap((r) => r.stints).filter((s) => s.letter === '');
  assert.ok(letterless.length, 'tiny bars drop the letter instead of clipping it');
  assert.ok(letterless.every((s) => s.text.includes(s.label)), 'a letterless bar still names its compound');
  assert.equal(model.rows.length, race.drivers.length);
});

test('lap times plot racing laps only and break lines at excluded laps', () => {
  const lecIndex = idx(race, 'LEC');
  const model = lapTimesModel(race, { focus: [lecIndex] });
  const lec = model.series[0];
  const excluded = race.laps.filter((l) => l.car_index === lecIndex && l.pace_excluded).map((l) => l.lap_number);
  assert.ok(lec.points.every((p) => !excluded.includes(p.lap)));
  assert.ok(lec.runs.length >= 2);
  assert.ok(lec.points.every((p) => p.openable && p.lap_id), 'traced racing laps can open in Lap Lab');
  assert.equal(lapTimesModel(race, { focus: [] }).empty, true);
});

test('heatmap colours are diverging around the median and explain every excluded lap', () => {
  assert.equal(heatColour(null), HEAT_EXCLUDED);
  assert.equal(heatColour(-1.4), '#2f6fd6');
  assert.equal(heatColour(-0.5), '#5b8fe0');
  assert.equal(heatColour(0), '#2a3541');
  assert.equal(heatColour(0.6), '#d9893d');
  assert.equal(heatColour(2), '#f0a24a');
  const model = heatmapModel(race);
  const lec = model.rows.find((r) => r.code === 'LEC');
  assert.equal(lec.cells.length, model.total);
  const lap1 = lec.cells[0];
  assert.equal(lap1.fill, HEAT_EXCLUDED);
  assert.match(lap1.text, /Lap 1/);
  const suspended = lec.cells[5];
  assert.match(suspended.text, /race suspended/i);
  assert.ok(lec.cells.some((c) => c.delta !== null && c.lap_id && c.openable), 'racing cells link to a lap');
  const had = model.rows.find((r) => r.code === 'HAD');
  assert.match(had.cells[11].text, /not driven/);
  assert.equal(had.cells[11].state, 'none');
});

test('timelapse frames follow the order, freeze while suspended and interpolate between laps', () => {
  const frames = timelapseFrames(race);
  assert.equal(frames.length, 12);
  assert.ok(frames[5].suspended && frames[6].suspended);
  assert.deepEqual(frames[6].entries.map((e) => e.car_index), frames[5].entries.map((e) => e.car_index), 'order holds while stopped');
  assert.ok(frames[6].entries.every((e) => e.gap_ms === null || e.gap_ms === undefined));
  const last = frames[11];
  assert.equal(last.entries[0].car_index, idx(race, 'LEC'));
  const mid = frameAt(frames, 9.5);
  const a = frames[8].entries.find((e) => e.car_index === idx(race, 'PIA'));
  const b = frames[9].entries.find((e) => e.car_index === idx(race, 'PIA'));
  const blended = mid.entries.find((e) => e.car_index === idx(race, 'PIA'));
  assert.ok(Math.abs(blended.gap_ms - (a.gap_ms + b.gap_ms) / 2) < 1e-6);
  assert.equal(frameAt(frames, 99).lap, 12);
  assert.equal(frameAt([], 3).entries.length, 0);
});

test('fastest laps, pit stops and the headline read straight from the payload', () => {
  const fastest = fastestModel(race);
  assert.equal(fastest.rows[0].delta_to_fastest_ms, 0);
  assert.match(fastest.rows[0].label, /^1:\d\d\.\d{3}$/);
  assert.ok(fastest.rows.slice(1).every((r) => r.label.startsWith('+')));
  const stops = pitStopRows(race);
  const nor = stops.filter((s) => s.code === 'NOR');
  assert.deepEqual(nor.map((s) => s.kindText), ['Tyre change while suspended', 'Pit stop']);
  assert.equal(nor[0].lossText, '—');
  assert.equal(nor[1].lossText, '22.1 s');
  const alb = stops.filter((s) => s.code === 'ALB');
  assert.deepEqual(alb.map((s) => s.kindText), ['Tyre change while suspended', 'Stop not recorded']);
  assert.equal(alb[1].lossText, 'Unknown');
  const head = headline(race);
  assert.match(head.title, /wins/);
  assert.ok(head.kpis.some((k) => k.label === 'Your result (derived)' && k.value === 'P1'));
  assert.ok(head.kpis.some((k) => k.label === 'Neutralised' && /red flag 6–7/.test(k.detail) && /safety car 3/.test(k.detail)));
  const practice = headline({ session: { is_race: false, session_type: 'Practice 1', track_name: 'Singapore' }, drivers: [{ car_index: 0, code: 'LEC', is_player: true, best_lap_ms: 91000, laps_recorded: 8 }], fastest_laps: [] });
  assert.equal(practice.title, 'Singapore · Practice 1');
  assert.equal(practice.kpis[0].label, 'Your best');
});

test('lap bands merge consecutive laps', () => {
  const bands = lapBands({ neutralised_laps: [3, 4, 5, 9], suspended_laps: [] }, (l) => l * 10);
  assert.deepEqual(bands.map((b) => [b.first, b.last]), [[3, 5], [9, 9]]);
});
