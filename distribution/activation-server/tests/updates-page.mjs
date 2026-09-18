import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';
const source = await readFile(new URL('../../../static/js/updates.js', import.meta.url), 'utf8');
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
function element() { return {listeners: {}, hidden: false, disabled: false, textContent: '', checked: false, before() {}, appendChild(child) { this.child = child; }, removeAttribute(key) { delete this[key]; }, addEventListener(name, fn) { this.listeners[name] = fn; }}; }
function harness(data) {
  const elements = new Map(), header = element(), calls = [], timers = [];
  const get = id => { if (!elements.has(id)) elements.set(id, element()); return elements.get(id); };
  const document = {getElementById: get, createElement: element, querySelector: () => header, visibilityState: 'visible'};
  vm.runInNewContext(source, {document, setInterval: fn => timers.push(fn), fetch: async (url, options) => { calls.push({url, options}); return {ok: true, json: async () => data}; }});
  return {get, header, calls, timers, document};
}
const state = () => ({enabled: true, current_version: '4.11.0', available: true, dismissed: false, outcome: 'ok', release: {version: '4.12.0', download_url: 'https://yourpitbox.com/#windows-release', notes: '<script>not executable</script>'}});
test('update UI offers only a download link, uses text, and polls cached local status', async () => {
  const data = state(), h = harness(data); await settle();
  assert.equal(h.header.child.hidden, false); assert.equal(h.header.child.href, data.release.download_url);
  assert.equal(h.get('updateNotes').textContent, data.release.notes);
  assert.equal(h.calls[0].url, '/api/v1/updates'); assert.equal(h.calls[0].options.method, undefined);
  h.get('updateCheck').listeners.click(); await settle();
  assert.equal(h.calls[1].url, '/api/v1/updates/check'); assert.equal(h.calls[1].options.method, 'POST');
  data.dismissed = true; h.get('updateDismiss').listeners.click(); await settle();
  assert.equal(h.header.child.hidden, true); assert.equal(h.get('updateDownload').hidden, false);
  assert.equal(JSON.parse(h.calls[2].options.body).dismiss, true);
  assert.ok(!source.includes('innerHTML'));
});
test('unsafe or unavailable releases cannot show an update download', async () => {
  for (const release of [null, {...state().release, download_url: 'https://attacker.test/file.exe'}]) {
    const h = harness({...state(), release}); await settle();
    assert.equal(h.header.child.hidden, true); assert.equal(h.get('updateDownload').hidden, true);
    assert.equal(h.header.child.href, undefined);
  }
});
