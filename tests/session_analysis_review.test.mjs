import test from 'node:test';
import assert from 'node:assert/strict';
import '../static/js/i18n.js';
import { callsLaneModel, callWord, reviewStopsTable, reviewSummary } from '../static/js/session-analysis-model.mjs';

const analysis = { session: { is_race: true, total_laps: 20 }, drivers: [], laps: [{ car_index: 0, lap_number: 20 }] };

test('each call sits on its lap of the stint chart, coloured by whether it was said', () => {
  const review = { available: true, calls: [
    { lap: 15, type: 'strategy_change', text: 'Box this lap for hards.', delivered: true, outcome: 'delivered' },
    { lap: 16, type: 'rival_pitting', text: 'Albon is in.', delivered: false, outcome: 'superseded' },
    { lap: 17, type: 'weather_crossover', text: 'Rain.', delivered: false, outcome: 'voice delivery failed' },
  ] };
  const model = callsLaneModel(analysis, review, { width: 1052 });
  // The stint chart's axis: lap 0 at 52 px, lap 20 at 1040 px.
  assert.equal(model.marks[0].x, 52 + (15 - 0.5) * (988 / 20));
  assert.deepEqual(model.marks.map((mark) => mark.tone), ['said', 'unsaid', 'failed']);
  assert.equal(model.marks[0].box, true, 'a box call carries its word on the lane');
  assert.equal(model.marks[0].word, 'Plan');
  assert.match(model.marks[1].text, /^Lap 16 · Rival · not said \(superseded\): Albon is in\.$/);
  assert.equal(model.said, 1);
  assert.equal(model.unsaid, 2);
  assert.equal(callWord('something_new'), 'Call');
});

test('calls on one lap sit side by side, and only the first box call is labelled', () => {
  const review = { available: true, calls: [
    { lap: 7, type: 'strategy_change', text: 'Box this lap for hards.', delivered: true },
    { lap: 7, type: 'weather_box_reminder', text: 'Box, box.', delivered: true },
  ] };
  const [first, second] = callsLaneModel(analysis, review, { width: 1052 }).marks;
  const centre = 52 + 6.5 * (988 / 20);
  assert.ok(first.x < centre && second.x > centre && Math.abs(first.x + second.x - 2 * centre) < 1e-9);
  assert.deepEqual([first.box, second.box], [true, false]);
});

test('the stops table names the call, whether it was heard and what hindsight says', () => {
  const table = reviewStopsTable({ on_the_money_s: 1.5, stops: [
    { lap: 17, from: 'MEDIUM', to: 'HARD', phase: 'green', recommended_lap_at_the_time: 15, called_on_lap: 15,
      laps_after_call: 2, heard: true, hindsight_best_lap: 12, cost_vs_hindsight_s: 28.04, places: 1 },
    { lap: 30, from: 'HARD', to: 'SOFT', phase: 'safety_car', recommended_lap_at_the_time: null, called_on_lap: null,
      laps_after_call: null, heard: null, hindsight_best_lap: null, cost_vs_hindsight_s: null, places: null },
    { lap: 40, from: 'SOFT', to: 'MEDIUM', phase: 'green', called_on_lap: 40, laps_after_call: 0, heard: false,
      hindsight_best_lap: 40, cost_vs_hindsight_s: 0.6, places: 0 },
  ] });
  assert.deepEqual(table.rows[0], ['17', 'Medium to Hard', 'lap 15', 'lap 15, stopped 2 laps later', 'yes', 'lap 12', '28.0 s', '1']);
  assert.deepEqual(table.rows[1].slice(1, 7), ['Hard to Soft (safety car)', '—', 'not called', '—', 'not comparable', '—']);
  assert.deepEqual(table.rows[2].slice(3, 7), ['lap 40', 'no', 'lap 40', 'on the money']);
});

test('the summary counts the calls never said and a late wet stop', () => {
  const text = reviewSummary({ available: true, calls: [{}, {}, {}], undelivered: 1, overrides: [],
    weather: [{ spell: 1, laps_late: 2, cost_s: 9.1 }] });
  assert.equal(text, '3 calls · 1 never said · rain spell 1: stopped 2 laps after the track crossed to inters (9.1 s)');
  assert.equal(reviewSummary({ available: false }), '');
});
