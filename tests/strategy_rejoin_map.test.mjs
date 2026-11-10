import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
// The strategy text goes through the screen-language runtime, a page global.
import '../static/js/i18n.js';

const source = fs.readFileSync(new URL('../static/js/strategy.js', import.meta.url), 'utf8');
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this._text = ''; this.className = ''; this.attributes = {}; this.listeners = {}; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return [this._text, ...this.children.map(child => child.textContent)].filter(Boolean).join(' '); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; this._text = ''; }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(name, handler) { this.listeners[name] = handler; }
}
let sequence = 0;
async function fixture() {
  const strategy = await import('data:text/javascript;base64,' + Buffer.from(source + `\n// ${sequence++}`).toString('base64'));
  const body = new Element('tbody');
  globalThis.document = {getElementById: (id) => (id === 'stratRejoinRows' ? body : null), createElement: (tag) => new Element(tag)};
  return {body, render: strategy.renderRejoinMap};
}
const row = (box_lap, rejoin_position, extra = {}) => ({
  box_lap, rejoin_position, car_ahead: 'TSUNODA', gap_ahead_s: 2.8, car_behind: 'ALBON', gap_behind_s: 2.1,
  clear_air: true, ...extra,
});
const state = (rows, box_lap = 15) => ({strategy: {available: true, recommended: {box_lap}, rejoin_map: rows}});

test('one column per stop lap, the recommended one marked and clear air named', async () => {
  const {body, render} = await fixture();
  render(state([row(14, 11), row(15, 9), row(16, 9, {gap_ahead_s: 0.6, clear_air: false})]));
  const [laps, rejoin, ahead, behind, air] = body.children;
  assert.equal(body.children.length, 5);
  assert.equal(laps.children.length, 4, 'a row label and three laps');
  assert.deepEqual(rejoin.children.slice(1).map((cell) => cell.textContent), ['P11', 'P9', 'P9']);
  assert.equal(ahead.children[1].textContent, 'TSU 2.8');
  assert.equal(behind.children[3].textContent, 'ALB 2.1');
  assert.deepEqual(air.children.slice(1).map((cell) => cell.textContent), ['clear air', 'clear air', 'traffic']);
  assert.match(laps.children[2].className, /is-recommended/);
  assert.doesNotMatch(laps.children[1].className, /is-recommended/);
  assert.match(laps.children[1].className, /is-clear/);
  assert.doesNotMatch(laps.children[3].className, /is-clear/);
  const button = laps.children[1].children[0];
  assert.equal(button.textContent, '14');
  assert.equal(typeof button.listeners.click, 'function');
});

test('an unknown neighbour and unknown air are shown as unknown', async () => {
  const {body, render} = await fixture();
  render(state([row(14, 1, {car_ahead: null, gap_ahead_s: null, clear_air: null})]));
  const [, , ahead, , air] = body.children;
  assert.equal(ahead.children[1].textContent, '—');
  assert.equal(air.children[1].textContent, '—');
});

test('no plan shows the empty state, and unchanged content is not rebuilt', async () => {
  const {body, render} = await fixture();
  render({strategy: {available: false}});
  assert.match(body.textContent, /Rejoin positions appear once a race plan exists/);
  const rows = [row(14, 11)];
  render(state(rows));
  const first = body.children[0];
  render(state(rows));
  assert.equal(body.children[0], first);
});
