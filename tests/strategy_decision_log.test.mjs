import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../static/js/strategy.js', import.meta.url), 'utf8');
class Element {
  constructor() { this.children = []; this._text = ''; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(' '); }
  appendChild(child) { this.children.push(child); return child; }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; this._text = ''; }
}
let sequence = 0;
async function fixture(snapshots) {
  const strategy = await import('data:text/javascript;base64,' + Buffer.from(source + `\n// ${sequence++}`).toString('base64'));
  const host = new Element();
  globalThis.document = {getElementById: () => host, createElement: () => new Element()};
  globalThis.fetch = async () => ({ok: true, json: async () => ({strategies: snapshots})});
  return {host, load: strategy.loadDecisionLog};
}
function snapshot(id, position) {
  return {id, lap_num: 1, race_control_phase: 'green', model: {confidence: 'low'},
    recommended: {instruction: 'A second compound is mandatory — box lap 18 for HARD.',
      box_laps: [18], compounds: ['MEDIUM', 'HARD'], projected_finish_position: position}};
}

test('identical pit calls expose their different estimated finishes without deleting saved evidence', async () => {
  const positions = [3, 16, 19, 9, 8];
  const {host, load} = await fixture(positions.map((position, index) => snapshot(index + 1, position)).reverse());
  await load();
  assert.equal(host.children.length, 5);
  host.children.forEach((entry, index) => {
    assert.match(entry.textContent, /Lap 1.*box lap 18 for HARD/);
    assert.ok(entry.textContent.includes(`Estimated finish P${positions[index]}`));
    assert.match(entry.textContent, /green · low/);
  });
});

test('projection, later-stop, compound, phase and confidence changes refresh the rendered log', async () => {
  const snapshots = [snapshot(1, 3)];
  snapshots[0].recommended.box_laps = [18, 27];
  snapshots[0].recommended.compounds = ['MEDIUM', 'HARD', 'SOFT'];
  const {host, load} = await fixture(snapshots);
  await load();
  assert.match(host.textContent, /Full plan: lap 18 HARD, then lap 27 SOFT/);
  snapshots[0].recommended.projected_finish_position = 7;
  await load();
  assert.match(host.textContent, /Estimated finish P7/);
  snapshots[0].recommended.box_laps[1] = 29;
  snapshots[0].recommended.compounds[2] = 'MEDIUM';
  await load();
  assert.match(host.textContent, /Full plan: lap 18 HARD, then lap 29 MEDIUM/);
  snapshots[0].model.confidence = 'high';
  snapshots[0].race_control_phase = 'safety_car';
  await load();
  assert.match(host.textContent, /safety_car · high/);
  const existingRow = host.children[0];
  await load();
  assert.equal(host.children[0], existingRow, 'unchanged content does not rebuild DOM');
});

test('missing or zero projection never becomes an estimated finishing position', async () => {
  const {host, load} = await fixture([snapshot(1, null), snapshot(2, 0), snapshot(3, undefined)]);
  await load();
  assert.equal(host.children.length, 3);
  assert.doesNotMatch(host.textContent, /Estimated finish|P0/);
});
