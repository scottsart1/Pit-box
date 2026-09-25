import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';

const source = await readFile(new URL('../../../static/js/onboarding.js', import.meta.url), 'utf8');
const html = await readFile(new URL('../../../static/index.html', import.meta.url), 'utf8');
const version = (await readFile(new URL('../../../src/pitwall/__init__.py', import.meta.url), 'utf8')).match(/__version__ = "([\d.]+)"/)[1];
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
const initialState = () => ({connected: false, game_paused: false, wake_enabled: false, wake_phrase: 'mark', wake_input_rms: 0, ptt_mask: 0});

function harness(options = {}) {
  const elements = new Map(), calls = [], listeners = {}, timers = new Set(), observers = [], events = [], copied = [];
  const storage = new Map(options.seen ? [['pitwall.onboarding.v1', options.seen]] : []);
  let now = 1000;
  const document = {activeElement: null, getElementById: get, querySelector: () => get('connectionTab')};
  function get(id) {
    if (!elements.has(id)) elements.set(id, {
      id, hidden: false, disabled: false, value: '', textContent: '', open: false,
      listeners: {}, classList: {contains: name => name === 'done' && !options.booting},
      before() {}, focus() { document.activeElement = this; },
      showModal() { this.open = true; }, close() { this.open = false; },
      addEventListener(name, fn) { this.listeners[name] = fn; },
      click() { if (!this.disabled) return this.listeners.click?.(); },
    });
    return elements.get(id);
  }
  get('usagePrompt').hidden = !options.usageUndecided;
  get('usageToggle').disabled = !!options.usageLoading;
  const window = {addEventListener: (name, fn) => { listeners[name] = fn; }, dispatchEvent: event => { events.push(event.type); listeners[event.type]?.(event); }};
  const responses = {
    '/api/state': options.state || initialState(),
    '/api/v1/credentials/openai': {configured: !!options.configured},
    '/api/ptt/calibrate': {ok: true},
    '/api/wake/config': {enabled: true},
    '/api/v1/network/status': {listener: {bind_host: '0.0.0.0', port: 20777, state: 'listening'}, recommendation: {console_destination_ipv4: '192.168.1.50'}},
    '/api/v1/network/interfaces': {},
  };
  const context = {
    document, window, AbortController, setTimeout, clearTimeout,
    CustomEvent: class { constructor(type) { this.type = type; } },
    navigator: {clipboard: {writeText: async value => { if (options.clipboardBlocked) throw new Error('denied'); copied.push(value); }}},
    setInterval: fn => { timers.add(fn); return fn; }, clearInterval: fn => timers.delete(fn),
    Date: class extends Date { static now() { return now; } },
    MutationObserver: class { constructor(fn) { observers.push(fn); } observe() {} },
    localStorage: {
      getItem(key) { if (options.storageBlocked) throw new Error('denied'); return storage.get(key) ?? null; },
      setItem(key, value) { if (options.storageBlocked) throw new Error('denied'); storage.set(key, value); },
    },
    fetch: async (url, request) => {
      calls.push({url, ...request});
      if (options.fetch) return options.fetch(url, request, responses);
      return {ok: true, json: async () => responses[url]};
    },
  };
  vm.runInNewContext(source, context);
  return {
    get, calls, storage, responses, timers, document, events, copied,
    state: data => listeners['pitwall:state']({detail: data}),
    mutate: () => observers.forEach(fn => fn()),
    tick: ms => { now += ms; timers.forEach(fn => fn()); },
    submit: () => get('onboardingKeyForm').listeners.submit({preventDefault() {}}),
    escape: () => get('onboardingDialog').listeners.cancel({preventDefault() {}}),
  };
}

test('first-run walkthrough is read-only and each of its three steps is skippable', async () => {
  const h = harness(); await settle();
  assert.equal(h.get('onboardingDialog').open, true);
  assert.equal(h.get('onboardingTitle').textContent, 'Give your engineer a voice');
  assert.equal(h.get('onboardingBack').hidden, true);
  for (let i = 1; i <= 3; i++) {
    assert.equal(h.get('onboardingProgress').textContent, `Step ${i} of 3`);
    assert.equal(h.get(`onboardingStep${i - 1}`).hidden, false);
    h.get('onboardingSkipStep').click();
  }
  assert.equal(h.get('onboardingStep3').hidden, false);
  assert.equal(h.get('onboardingSkipStep').textContent, 'Skip tour');
  h.get('onboardingSkipStep').click();
  assert.equal(h.get('onboardingDialog').open, false);
  assert.equal(h.storage.get('pitwall.onboarding.v1'), 'finished');
  assert.equal(h.timers.size, 0);
  assert.ok(h.calls.every(call => !call.method));
});

test('existing choice suppresses automatic tour; Settings can reopen without overwriting a key', async () => {
  const h = harness({seen: 'finished', configured: true}); await settle();
  assert.equal(h.get('onboardingDialog').open, false);
  h.get('onboardingRestart').click(); await settle();
  assert.equal(h.get('onboardingDialog').open, true);
  assert.match(h.get('onboardingKeyStatus').textContent, /already configured/);
  h.get('onboardingNext').click(); h.get('onboardingBack').click();
  assert.equal(h.get('onboardingProgress').textContent, 'Step 1 of 3');
  h.get('onboardingNext').click(); h.get('onboardingNext').click(); h.get('onboardingNext').click();
  h.get('onboardingBeginTour').click();
  assert.equal(h.storage.get('pitwall.onboarding.v1'), 'finished');
  assert.ok(h.events.includes('pitwall:tour-start'));
  assert.ok(h.calls.every(call => !call.method));
});

test('saving a key uses existing verified local API, clears password and never stores it in browser preferences', async () => {
  const h = harness(); await settle();
  h.responses['/api/v1/credentials/openai'] = {configured: true};
  h.get('onboardingKey').value = 'test-only-not-a-real-key';
  await h.submit();
  const writes = h.calls.filter(call => call.method);
  assert.equal(writes.length, 1);
  assert.equal(writes[0].url, '/api/v1/credentials/openai');
  assert.equal(writes[0].method, 'PUT');
  assert.deepEqual(JSON.parse(writes[0].body), {api_key: 'test-only-not-a-real-key', verify: true});
  assert.equal(h.get('onboardingKey').value, '');
  assert.match(h.get('onboardingKeyStatus').textContent, /checked and saved/);
  h.get('onboardingClose').click();
  assert.deepEqual([...h.storage], [['pitwall.onboarding.v1', 'skipped']]);
  assert.ok(!source.includes('innerHTML'));
});

test('saving errors are visible and never reflect server-returned secret text', async () => {
  const h = harness({fetch: async (url, request, responses) => ({
    ok: request.method !== 'PUT', status: request.method === 'PUT' ? 403 : 200,
    json: async () => request.method === 'PUT' ? {detail: 'secret text must not be reflected'} : responses[url],
  })}); await settle();
  h.get('onboardingKey').value = 'test-only-not-a-real-key'; await h.submit();
  assert.match(h.get('onboardingKeyStatus').textContent, /device running Your Pit Box/);
  assert.doesNotMatch(h.get('onboardingKeyStatus').textContent, /secret text|checked and saved/);
  assert.equal(h.get('onboardingSaveKey').disabled, false);
  assert.equal(h.get('onboardingEnableMark').disabled, true);
  h.get('onboardingSkipStep').click(); assert.equal(h.get('onboardingStep1').hidden, false);
});

test('calibration needs live telemetry and starting it never claims the controller has been calibrated', async () => {
  const h = harness({configured: true}); await settle();
  h.get('onboardingNext').click(); h.get('onboardingCalibrate').click();
  assert.equal(h.calls.filter(call => call.method).length, 0);
  h.state({...initialState(), connected: true});
  await h.get('onboardingCalibrate').click();
  assert.equal(h.calls.at(-1).url, '/api/ptt/calibrate');
  assert.match(h.get('onboardingCalibrationResult').textContent, /not completed yet/);
  h.state({...initialState(), connected: true, ptt_mask: 4, ptt_status: 'bound to bit 0x4'});
  assert.match(h.get('onboardingPttStatus').textContent, /bound to bit 0x4/);
});

test('enabling voice is explicit; microphone input is not described as a completed radio check', async () => {
  const h = harness({configured: true}); await settle();
  h.get('onboardingNext').click(); h.get('onboardingNext').click();
  assert.equal(h.calls.filter(call => call.method).length, 0);
  await h.get('onboardingEnableMark').click();
  assert.equal(h.calls.at(-1).url, '/api/wake/config');
  assert.deepEqual(JSON.parse(h.calls.at(-1).body), {enabled: true});
  h.state({...initialState(), wake_enabled: true, wake_input_rms: 50, wake_status: 'ready', wake_phrase: 'hey mark'});
  assert.match(h.get('onboardingWakeStatus').textContent, /radio on/);
  assert.ok(h.get('onboardingMicMeter').value > 0);
  assert.match(h.get('onboardingMicStatus').textContent, /does not yet confirm/);
  assert.equal(h.get('onboardingExample').textContent, '“Hey mark, radio check.”');
  h.tick(6000);
  assert.equal(h.get('onboardingMicMeter').value, 0);
  assert.equal(h.get('onboardingEnableMark').disabled, true);
  assert.match(h.get('onboardingWakeStatus').textContent, /unavailable/);
});

test('a denied microphone does not report voice enabled', async () => {
  const h = harness({configured: true}); await settle();
  h.responses['/api/wake/config'] = {enabled: false};
  await h.get('onboardingEnableMark').click();
  assert.match(h.get('onboardingWakeResult').textContent, /not enabled/);
  h.state({...initialState(), wake_enabled: true, last_error: 'Microphone error: unavailable'});
  assert.match(h.get('onboardingWakeStatus').textContent, /needs attention/);
});

test('unsuccessful calibration never reports a started calibration', async () => {
  const h = harness(); await settle();
  h.responses['/api/ptt/calibrate'] = {ok: false};
  h.state({...initialState(), connected: true});
  await h.get('onboardingCalibrate').click();
  assert.match(h.get('onboardingCalibrationResult').textContent, /was not started/);
});

test('Skip remains available during a key save and a late response cannot reopen the tour', async () => {
  let resolveSave;
  const h = harness({fetch: async (url, request, responses) => {
    if (request.method === 'PUT') return await new Promise(resolve => { resolveSave = resolve; });
    return {ok: true, json: async () => responses[url]};
  }}); await settle();
  h.get('onboardingKey').value = 'test-only-not-a-real-key';
  const pending = h.submit();
  assert.equal(h.get('onboardingSaveKey').disabled, true);
  h.get('onboardingClose').click();
  assert.equal(h.get('onboardingDialog').open, false);
  resolveSave({ok: true, json: async () => ({configured: true})});
  await pending;
  assert.equal(h.get('onboardingDialog').open, false);
  assert.equal(h.get('onboardingKey').value, '');
  assert.equal(h.storage.get('pitwall.onboarding.v1'), 'skipped');
});

test('tour waits for existing usage choice and boot; neither decision is changed', async () => {
  const options = {usageUndecided: true, booting: true};
  const h = harness(options); await settle();
  assert.equal(h.get('onboardingDialog').open, false);
  options.booting = false; h.mutate();
  assert.equal(h.get('onboardingDialog').open, false);
  h.get('usagePrompt').hidden = true; h.mutate(); await settle();
  assert.equal(h.get('onboardingDialog').open, true);
  assert.ok(!h.calls.some(call => call.url.includes('/usage')));
});

test('an already live session suppresses automatic popups, even after pausing', async () => {
  const h = harness({state: {...initialState(), connected: true}}); await settle();
  assert.equal(h.get('onboardingDialog').open, false);
  h.state({...initialState(), connected: true, game_paused: true});
  assert.equal(h.get('onboardingDialog').open, false);
  h.get('onboardingRestart').click();
  assert.equal(h.get('onboardingDialog').open, true);
});

test('Escape and Skip setup clear unsaved key; storage failure never blocks exit', async () => {
  const h = harness({storageBlocked: true}); await settle();
  h.get('onboardingKey').value = 'unsaved-secret'; h.escape();
  assert.equal(h.get('onboardingKey').value, '');
  assert.equal(h.get('onboardingDialog').open, false);
  assert.match(h.get('onboardingSavedStatus').textContent, /could not remember/);
  h.mutate(); assert.equal(h.get('onboardingDialog').open, false);
  h.get('onboardingRestart').click(); h.get('onboardingClose').click();
  assert.equal(h.get('onboardingDialog').open, false);
});

test('onboarding markup has unique IDs, official key link, accessible password field and versioned assets', () => {
  const ids = [...html.matchAll(/id="(onboarding[^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  for (const match of source.matchAll(/get\("(onboarding[^"]+)"\)/g)) assert.ok(ids.includes(match[1]), match[1]);
  assert.match(html, /id="onboardingKey" type="password" autocomplete="off"/);
  assert.match(html, /https:\/\/platform.openai.com\/api-keys" target="_blank" rel="noopener noreferrer"/);
  assert.match(html, /<dialog id="onboardingDialog"[^>]*aria-labelledby="onboardingTitle"/);
  assert.ok(html.includes(`/static/js/onboarding.js?v=${version}`));
  assert.ok(html.includes(`/static/css/onboarding.css?v=${version}`));
});

test('step 2 shows and copies actual UDP destination and port without leaving the guide', async () => {
  const h = harness(); await settle(); h.get('onboardingNext').click(); await settle();
  assert.equal(h.get('onboardingUdpIp').textContent, '192.168.1.50');
  assert.equal(h.get('onboardingUdpPort').textContent, '20777');
  assert.match(h.get('onboardingNetworkStatus').textContent, /waiting for game telemetry/);
  await h.get('onboardingCopyIp').click(); await h.get('onboardingCopyPort').click();
  assert.deepEqual(h.copied, ['192.168.1.50', '20777']);
  assert.equal(h.get('onboardingStep1').hidden, false);
  assert.equal(h.get('onboardingDialog').open, true);
  assert.ok(h.calls.every(call => !call.method));
});

test('network refresh updates changed Wi-Fi details and honours a specific listener binding', async () => {
  const h = harness(); await settle(); h.get('onboardingNext').click(); await settle();
  h.responses['/api/v1/network/status'] = {listener: {bind_host: '10.0.0.9', port: 20784, state: 'receiving'}, recommendation: {console_destination_ipv4: '192.168.1.50'}};
  await h.get('onboardingRefreshNetwork').click();
  assert.ok(h.calls.some(call => call.url === '/api/v1/network/interfaces'));
  assert.equal(h.get('onboardingUdpIp').textContent, '10.0.0.9');
  assert.equal(h.get('onboardingUdpPort').textContent, '20784');
  assert.match(h.get('onboardingNetworkStatus').textContent, /^Receiving/);
});

test('wildcard, loopback, malformed and missing addresses cannot be copied as PS5 destinations', async () => {
  for (const bad of ['0.0.0.0', '127.0.0.1', '999.2.3.4', '224.0.0.1', '<img src=x>', null]) {
    const h = harness(); await settle();
    h.responses['/api/v1/network/status'].recommendation.console_destination_ipv4 = bad;
    h.get('onboardingNext').click(); await settle();
    assert.equal(h.get('onboardingUdpIp').textContent, 'Unavailable');
    assert.equal(h.get('onboardingCopyIp').disabled, true);
  }
  const h = harness(); await settle();
  h.responses['/api/v1/network/status'].listener = {bind_host: '127.0.0.1', port: -1, state: 'listening'};
  h.get('onboardingNext').click(); await settle();
  assert.equal(h.get('onboardingUdpIp').textContent, 'Unavailable');
  assert.equal(h.get('onboardingUdpPort').textContent, 'Unavailable');
});

test('network outage removes stale values and clipboard denial gives a manual fallback', async () => {
  let outage = false;
  const h = harness({clipboardBlocked: true, fetch: async (url, request, responses) => ({
    ok: !(outage && url.includes('/network/')), status: 503, json: async () => responses[url],
  })}); await settle(); h.get('onboardingNext').click(); await settle();
  await h.get('onboardingCopyIp').click();
  assert.match(h.get('onboardingCopyStatus').textContent, /Clipboard unavailable/);
  outage = true; await h.get('onboardingRefreshNetwork').click();
  assert.equal(h.get('onboardingUdpIp').textContent, 'Unavailable');
  assert.equal(h.get('onboardingCopyIp').disabled, true);
  assert.match(h.get('onboardingNetworkStatus').textContent, /could not be read/);
});
