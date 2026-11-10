import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
// The strategy text goes through the screen-language runtime, a page global.
import '../static/js/i18n.js';

const source = fs.readFileSync(new URL('../static/js/strategy.js', import.meta.url), 'utf8');
globalThis.document = {getElementById: () => null, createElement: () => ({})};
const {readinessLine} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

const call = (extra = {}) => ({box_lap: 20, fit_compound: 'HARD', gain_vs_green_plan_s: 11.2, ...extra});
const state = (ready, lap = 20) => ({current_lap: lap, strategy: {if_neutralised: ready}});

test('the prepared safety-car call is said on the lap it was prepared for', () => {
  assert.equal(readinessLine(state({lap: 20, safety_car: call()})),
    'If a safety car comes out this lap: box for hards, about 11 s better.');
  assert.equal(readinessLine(state({lap: 20, safety_car: call({gain_vs_green_plan_s: null})})),
    'If a safety car comes out this lap: box for hards.');
});

test('a safety car that would not bring the car in says so', () => {
  assert.equal(readinessLine(state({lap: 20, safety_car: call({box_lap: 31})})),
    'If a safety car comes out this lap: stay out.');
});

test('nothing is said for another lap, or without a prepared call', () => {
  assert.equal(readinessLine(state({lap: 19, safety_car: call()})), '');
  assert.equal(readinessLine(state({lap: 20})), '');
  assert.equal(readinessLine({current_lap: 20, strategy: {}}), '');
});

const {chanceBars, sensitivityLine} = await import('data:text/javascript;base64,' + Buffer.from(source + '\n// 2').toString('base64'));

test('a plan shows its win, podium and points chances as three bars', () => {
  class Node { constructor() { this.children = []; this.attributes = {}; this.style = {}; this.className = ''; }
    appendChild(child) { this.children.push(child); return child; } setAttribute(k, v) { this.attributes[k] = String(v); } }
  globalThis.document = {getElementById: () => null, createElement: () => new Node()};
  const bars = chanceBars({p_win: 0.04, p_podium: 0.21, p_points: 0.876});
  assert.equal(bars.attributes['aria-label'], 'Win 4% · podium 21% · points 88%');
  assert.deepEqual(bars.children.map((bar) => bar.children[0].style.width), ['4%', '21%', '88%']);
  assert.equal(chanceBars({p_win: 0.1}), null, 'a plan without the drawn races has no bars');
});

test('the recommended plan says what a safety car and rain do to it', () => {
  assert.equal(sensitivityLine({safety_car_swing: 1.2, rain_swing: 2.1}),
    'A safety car gains this plan 1 place on average. Rain costs it 2 places.');
  assert.equal(sensitivityLine({safety_car_swing: -0.6, rain_swing: null}), 'A safety car costs this plan 1 place on average.');
  assert.equal(sensitivityLine({safety_car_swing: 0.3, rain_swing: -0.2}), '', 'under half a place says nothing');
});
