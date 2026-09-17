import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';

const source = await readFile(new URL('../../website/download-stats.js', import.meta.url), 'utf8');
const ids = ['reportLogin', 'reportKey', 'reportStatus', 'reportData', 'reportRefresh', 'reportRows',
  'totalAll', 'totalWindows', 'totalAndroid', 'reportUpdated', 'reportSince', 'reportLock',
  'historyData', 'historyRows', 'historyAll', 'historyWindows', 'historyAndroid', 'historyStatus', 'historyPeriod',
  'combinedAll', 'combinedWindows', 'combinedAndroid', 'combinedSources'];
function element() {
  return {value: '', hidden: false, textContent: '', disabled: false, children: [], listeners: {},
    addEventListener(name, fn) { this.listeners[name] = fn; }, focus() {},
    replaceChildren() { this.children = []; }, appendChild(child) { this.children.push(child); }};
}
function harness(fetcher) {
  const elements = Object.fromEntries(ids.map(id => [id, element()]));
  elements.reportData.hidden = true;
  const events = {};
  const context = vm.createContext({
    document: {getElementById: id => elements[id], createElement: element},
    window: {addEventListener: (event, fn) => { events[event] = fn; }},
    AbortController, setTimeout, clearTimeout, Intl, Date, fetch: fetcher,
  });
  vm.runInContext(source, context);
  return {elements, events, async login() {
    elements.reportKey.value = 'private-test-key';
    elements.reportLogin.listeners.submit({preventDefault() {}});
    await new Promise(resolve => setImmediate(resolve));
  }};
}
const payload = {
  metric: 'download_starts', generated_at: '2026-09-17T14:00:00Z',
  first_recorded_at: '2026-09-16T10:00:00Z', totals: {all: 9, windows: 7, android: 2},
  daily: [{day: '2026-09-17', platform: 'windows', starts: 7}, {day: '2026-09-17', platform: 'android', starts: 2}],
};
test('report displays separate totals, dates and no secret in the URL', async () => {
  const app = harness(async (url, options) => {
    assert.equal(url, 'https://pitwall-activation.sarthakvij123450.workers.dev/download-stats');
    assert.equal(options.headers.Authorization, 'Bearer private-test-key');
    assert.equal(options.cache, 'no-store');
    assert.equal(options.credentials, 'omit');
    return {ok: true, status: 200, json: async () => payload};
  });
  await app.login();
  const e = app.elements;
  assert.equal(e.reportKey.value, '');
  assert.equal(e.reportData.hidden, false);
  assert.equal(e.totalWindows.textContent, '7');
  assert.equal(e.totalAndroid.textContent, '2');
  assert.equal(e.totalAll.textContent, '9');
  assert.equal(e.reportRows.children.length, 30);
  assert.equal(e.reportRows.children[0].children[3].textContent, '9');
  assert.equal(e.reportRows.children[2].children[1].textContent, '—');
  e.reportLock.listeners.click();
  assert.equal(e.reportData.hidden, true);
  assert.equal(e.reportRows.children.length, 0);
  assert.equal(e.totalAll.textContent, '—');
});
test('refresh outage hides stale numbers rather than displaying zeros', async () => {
  let healthy = true;
  const app = harness(async () => healthy ? {ok: true, status: 200, json: async () => payload} : {ok: false, status: 503});
  await app.login();
  healthy = false;
  await app.elements.reportRefresh.listeners.click();
  assert.equal(app.elements.reportData.hidden, true);
  assert.equal(app.elements.totalAll.textContent, '—');
  assert.match(app.elements.reportStatus.textContent, /temporarily unavailable/);
});
test('wrong key and malformed report never expose a count', async () => {
  for (const response of [{ok: false, status: 401}, {ok: true, status: 200, json: async () => ({...payload, totals: {all: 99, windows: 7, android: 2}})}]) {
    const app = harness(async () => response);
    await app.login();
    assert.equal(app.elements.reportData.hidden, true);
    assert.equal(app.elements.reportLogin.hidden, false);
    assert.equal(app.elements.reportKey.value, '');
  }
});
test('locking during a pending request cannot reveal data after it resolves', async () => {
  let resolve;
  const app = harness(() => new Promise(complete => { resolve = complete; }));
  await app.login();
  app.elements.reportLock.listeners.click();
  resolve({ok: true, status: 200, json: async () => payload});
  await new Promise(complete => setImmediate(complete));
  assert.equal(app.elements.reportData.hidden, true);
  assert.equal(app.elements.reportRefresh.disabled, false);
  assert.equal(app.elements.reportRows.children.length, 0);
});
test('page exit clears loaded counts and credentials', async () => {
  const app = harness(async () => ({ok: true, status: 200, json: async () => payload}));
  await app.login();
  app.events.pagehide();
  assert.equal(app.elements.reportData.hidden, true);
  assert.equal(app.elements.totalAll.textContent, '—');
  assert.equal(app.elements.reportKey.value, '');
});

const history = {metric: 'historical_file_requests', source: 'Cloudflare R2 analytics',
  from: '2026-08-06T07:57:51Z', until: '2026-09-17T08:10:28Z', recovered_at: '2026-09-17T12:00:00Z',
  totals: {windows: 260, android: 10, all: 270},
  daily: [{day: '2026-09-16', platform: 'windows', requests: 260}, {day: '2026-09-17', platform: 'android', requests: 10}]};

test('historical totals, coverage and daily rows are separate and erased when locked', async () => {
  const app = harness(async () => ({ok: true, status: 200, json: async () => ({...payload, history})}));
  await app.login();
  const e = app.elements;
  assert.equal(e.totalAll.textContent, '9');
  assert.equal(e.historyAll.textContent, '270');
  assert.equal(e.historyWindows.textContent, '260');
  assert.equal(e.historyAndroid.textContent, '10');
  assert.equal(e.combinedAll.textContent, '279');
  assert.equal(e.combinedWindows.textContent, '267');
  assert.equal(e.combinedAndroid.textContent, '12');
  assert.match(e.combinedSources.textContent, /270 historical file requests \+ 9 live download starts/);
  assert.equal(e.historyData.hidden, false);
  assert.equal(e.historyRows.children.length, 43);
  assert.equal(e.historyRows.children[0].children[2].textContent, '10');
  assert.equal(e.historyRows.children[2].children[1].textContent, '0');
  assert.match(e.historyPeriod.textContent, /UTC.*end exclusive.*Recovered/);
  e.reportLock.listeners.click();
  assert.equal(e.historyData.hidden, true);
  assert.equal(e.historyAll.textContent, '—');
  assert.equal(e.historyRows.children.length, 0);
  assert.equal(e.historyPeriod.textContent, '');
  assert.equal(e.combinedAll.textContent, '—');
  assert.equal(e.combinedSources.textContent, '');
});

test('missing history is unavailable, not a zero or invented backfill', async () => {
  const app = harness(async () => ({ok: true, status: 200, json: async () => payload}));
  await app.login();
  assert.equal(app.elements.historyData.hidden, true);
  assert.match(app.elements.historyStatus.textContent, /not a zero/);
  assert.equal(app.elements.totalAll.textContent, '9');
  assert.equal(app.elements.combinedAll.textContent, '—');
  assert.match(app.elements.combinedSources.textContent, /unavailable/);
});

test('combined overview includes history without changing any source counter', async () => {
  const data = {...payload, history, totals: {windows: 1, android: 2, all: 3},
    daily: [{day: '2026-09-17', platform: 'windows', starts: 1}, {day: '2026-09-17', platform: 'android', starts: 2}]};
  const app = harness(async () => ({ok: true, status: 200, json: async () => data}));
  await app.login();
  assert.equal(app.elements.combinedAll.textContent, '273');
  assert.equal(app.elements.combinedWindows.textContent, '261');
  assert.equal(app.elements.combinedAndroid.textContent, '12');
  assert.equal(app.elements.totalAll.textContent, '3');
  assert.equal(app.elements.historyAll.textContent, '270');
  assert.equal(data.totals.all, 3);
});

test('inconsistent, duplicated or oversized historical data fails closed', async () => {
  for (const broken of [
    {...history, totals: {...history.totals, all: 271}},
    {...history, daily: [...history.daily, history.daily[0]]},
    {...history, from: '2020-01-01T00:00:00Z'},
    {...history, daily: [{day: '2026-09-18', platform: 'windows', requests: 270}]},
  ]) {
    const app = harness(async () => ({ok: true, status: 200, json: async () => ({...payload, history: broken})}));
    await app.login();
    assert.equal(app.elements.reportData.hidden, true);
    assert.equal(app.elements.historyAll.textContent, '—');
  }
});
