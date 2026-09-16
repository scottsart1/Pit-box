// Exercise the shipped DRIVE trace renderer, not a replacement implementation.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const source = fs.readFileSync(path.resolve(__dirname, '../static/index.html'), 'utf8');
const dom = new JSDOM(source, { runScripts: 'outside-only' });
const { window } = dom;
const byId = id => window.document.getElementById(id);
const lines = [];
const context = { clearRect() { lines.length = 0; }, beginPath() {}, stroke() {},
  moveTo(x, y) { lines.push(['move', x, y]); }, lineTo(x, y) { lines.push(['line', x, y]); } };
window.HTMLCanvasElement.prototype.getContext = () => context;
try {
  window.eval('function $(id){return document.getElementById(id)}\n' +
    ['drawTrace', 'traceLiveLabel', 'renderDriveTrace'].map(name => {
      const match = source.match(new RegExp(`^function ${name}\\([^\\n]+`, 'm'));
      assert(match, `Missing shipped renderer ${name}`);
      return match[0];
    }).join('\n'));
  const now = Date.now() / 1000;
  const state = { connected: true, packet_group_freshness: { 6: now },
    speed_kph: 0, throttle: 0, brake: 0,
    traces: [{ d: 0, speed: 100, throttle: 1, brake: 0 },
             { d: 100, speed: 0, throttle: 0, brake: 0 }] };
  window.renderDriveTrace(state);
  assert.match(byId('traceCard').textContent, /lap history by distance/);
  assert.match(byId('traceLiveStatus').textContent, /Now: 0 km\/h · throttle 0% · brake 0% · stationary/);
  const shape = structuredClone(lines);
  window.renderDriveTrace({ ...state, traces: state.traces.map(p => ({ ...p, t: 200 })) });
  assert.deepEqual(lines, shape, 'timestamp-only updates cannot redraw a different curve');
  assert.match(window.traceLiveLabel({ ...state, throttle: 1, brake: 0.5 }, now), /throttle 100% · brake 50%/);
  for (const stale of [{ ...state, connected: false }, { ...state, telemetry_stale: true },
    { ...state, packet_group_freshness: {} },
    { ...state, packet_group_freshness: { 6: now - 10 } }, { ...state, speed_kph: null }]) {
    assert.match(window.traceLiveLabel(stale, now), /^Current telemetry unavailable/);
  }
  console.log('PASS: DRIVE labels historical traces separately from current pedals, preserves curve geometry, and rejects stale readings.');
} finally { window.close(); }
