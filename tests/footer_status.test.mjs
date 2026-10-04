import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const html = fs.readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const start = html.indexOf('function compactFooterStatus(');
const end = html.indexOf('/* End compact status strip. */', start);
assert.ok(start > 0 && end > start);
const nodes = new Map(['footerTelemetry', 'footerRadio'].map(id => [id, {
  textContent: '', dataset: {}, title: '', attributes: {},
  setAttribute(name, value) { this.attributes[name] = value; },
}]));
const context = vm.createContext({ $: id => nodes.get(id) });
vm.runInContext(html.slice(start, end), context);
const status = (state = {}, transport = true) => context.compactFooterStatus(state, transport);

test('No packets and absent radio configuration remain unknown, not ready', () => {
  assert.equal(status().telemetry.text, 'No telemetry');
  assert.equal(status().radio.text, 'Radio idle');
  assert.equal(status({ connected: false }).telemetry.tone, 'muted');
});

test('Live, paused, recently stale and lost telemetry are distinct', () => {
  assert.equal(status({ connected: true }).telemetry.text, 'Telemetry live');
  assert.equal(status({ connected: true, game_paused: true }).telemetry.text, 'Game paused');
  assert.equal(status({ connected: false, game_presence: 'standing_by', last_packet_at: 10 }).telemetry.text, 'Telemetry stale');
  const lost = status({ connected: false, telemetry_stale: true, last_packet_at: 10, game_presence: 'none' });
  assert.equal(lost.telemetry.text, 'Telemetry lost');
  assert.equal(lost.telemetry.tone, 'error');
  assert.equal(status({ connected: true, telemetry_stale: true }).telemetry.text, 'Telemetry stale');
});

test('An old paused flag cannot conceal a lost game connection', () => {
  const state = { game_paused: true, connected: false, telemetry_stale: true, last_packet_at: 10 };
  assert.equal(status({ ...state, game_presence: 'standing_by' }).telemetry.text, 'Game paused');
  assert.equal(status({ ...state, game_presence: 'none' }).telemetry.text, 'Telemetry lost');
});

test('An app disconnect overrides retained live telemetry and active speech', () => {
  const state = { connected: true, radio_indicator: 'speaking' };
  const offline = status(state, false);
  assert.equal(offline.telemetry.text, 'App offline');
  assert.equal(offline.radio.text, 'Radio unknown');
  assert.equal(status(state).radio.text, 'Engineer speaking');
});

test('Radio error and missing microphone win over stale ready or active flags', () => {
  const state = { wake_enabled: true, wake_status: 'ready — say Mark', ptt_mask: 4, ptt_status: 'ready', ptt_pressed: true };
  assert.equal(status({ ...state, radio_indicator: 'error' }).radio.text, 'Radio error');
  assert.equal(status({ ...state, engineer_status: 'audio error', wake_status: 'microphone unavailable' }).radio.text, 'Mic unavailable');
  assert.equal(status({ ...state, engineer_status: 'PTT active; native voice disabled' }).radio.text, 'Radio unavailable');
  assert.equal(status({ ...state, voice_ready: false }).radio.text, 'Radio unavailable');
});

test('An unrelated receiver error does not invent a radio failure', () => {
  assert.equal(status({ wake_enabled: true, wake_status: 'ready', last_error: 'UDP forwarding target failed' }).radio.text, 'Wake on');
});

test('Listening, thinking and speaking show actual active radio phases', () => {
  assert.equal(status({ radio_indicator: 'listening' }).radio.text, 'Listening');
  assert.equal(status({ ptt_pressed: true }).radio.text, 'Listening');
  assert.equal(status({ wake_armed: true }).radio.text, 'Listening');
  assert.equal(status({ radio_indicator: 'processing' }).radio.text, 'Engineer thinking');
  assert.equal(status({ engineer_status: 'transcribing' }).radio.text, 'Engineer thinking');
  assert.equal(status({ radio_indicator: 'speaking' }).radio.text, 'Engineer speaking');
});

test('Disabled wake and uncalibrated PTT are never presented as radio ready', () => {
  assert.equal(status({ wake_enabled: false, ptt_status: 'calibration required' }).radio.text, 'Radio not set');
  assert.equal(status({ wake_enabled: false, ptt_status: 'unconfigured' }).radio.text, 'Radio not set');
  assert.equal(status({ wake_enabled: false, ptt_status: 'ready', ptt_mask: 4 }).radio.text, 'PTT set');
  assert.equal(status({ wake_enabled: true, wake_status: 'starting' }).radio.text, 'Wake starting');
  assert.equal(status({ wake_enabled: true, wake_status: 'ready — say Mark' }).radio.text, 'Wake on');
  assert.notEqual(status({ wake_enabled: true, wake_status: 'ready' }).radio.tone, 'good');
});

test('Real renderer replaces error/offline DOM with recovered current state', () => {
  context.renderCompactFooter({}, false);
  assert.equal(nodes.get('footerTelemetry').dataset.tone, 'error');
  context.renderCompactFooter({ connected: true, wake_enabled: true, wake_status: 'ready' });
  assert.equal(nodes.get('footerTelemetry').textContent, 'Telemetry live');
  assert.equal(nodes.get('footerTelemetry').dataset.tone, 'good');
  assert.equal(nodes.get('footerRadio').textContent, 'Wake on');
  assert.match(nodes.get('footerRadio').attributes['aria-label'], /Wake on/);
  context.renderCompactFooter({ engineer_status: 'error' });
  assert.equal(nodes.get('footerRadio').textContent, 'Radio error');
  assert.equal(nodes.get('footerRadio').dataset.tone, 'error');
});

test('Diagnostic IDs remain on Connection exactly once, with no static port claim in footer', () => {
  const connection = html.slice(html.indexOf('<main id="connection"'), html.indexOf('</main>', html.indexOf('<main id="connection"')));
  const footer = html.slice(html.indexOf('<footer class="bottom"'), html.indexOf('</footer>'));
  for (const id of ['wakeFooter', 'ptt', 'rate', 'queue', 'llmFooter']) {
    assert.ok(connection.includes(`id="${id}"`));
    assert.equal([...html.matchAll(new RegExp(`id="${id}"`, 'g'))].length, 1);
    assert.ok(!footer.includes(`id="${id}"`));
  }
  assert.match(footer, /href="#connection"/);
  assert.doesNotMatch(footer, /UDP 20777|Database persistent/);
  assert.match(html, /function render\(s\)\{dismissBoot\(\);lastState=s;renderCompactFooter\(s\)/);
  assert.match(html, /ws\.onclose=\(\)=>\{renderCompactFooter\(lastState\|\|\{\},false\)/);
});
