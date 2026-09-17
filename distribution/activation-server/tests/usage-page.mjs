import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';

const source = await readFile(new URL('../../website/usage-stats.js', import.meta.url), 'utf8');
const ids = ['usageReportLogin', 'usageReportKey', 'usageReportStatus', 'usageReportData', 'usageReportRefresh', 'usageReportLock',
  'usageReportUpdated', 'usageReportPeriod', 'usageJourneyRows', 'usageRetentionRows', 'usageFeatureRows', 'usageVersionRows'];
function element() {
  return {value: '', hidden: false, textContent: '', children: [], listeners: {}, disabled: false,
    addEventListener(name, fn) { this.listeners[name] = fn; }, replaceChildren() { this.children = []; },
    appendChild(child) { this.children.push(child); }};
}
function harness(fetcher) {
  const elements = Object.fromEntries(ids.map(id => [id, element()])), events = {};
  const context = vm.createContext({document: {getElementById: id => elements[id], createElement: element},
    window: {addEventListener: (name, fn) => { events[name] = fn; }}, AbortController, Date, Intl, setTimeout, clearTimeout, fetch: fetcher});
  vm.runInContext(source, context);
  return {elements, events, async login() {
    elements.usageReportKey.value = 'private-local-test-key';
    elements.usageReportLogin.listeners.submit({preventDefault() {}});
    await new Promise(resolve => setImmediate(resolve));
  }};
}
const usage = {
  metric: 'opted_in_installations', generated_at: '2026-09-17T16:00:00Z', activity_since: '2026-08-19',
  installations: [{platform: 'windows', installations: 3, new_30d: 3}, {platform: 'android', installations: 2, new_30d: 2}],
  features: [{platform: 'windows', event: 'racing', installations: 2}, {platform: 'android', event: 'engineer', installations: 1}],
  recent: [{platform: 'windows', active_7d: 2, active_today: 1}], versions: [{platform: 'windows', version: '4.10.2', installations: 3}],
  retention: [{day: 1, platforms: [{platform: 'windows', eligible: 2, returned: 1}]}, {day: 7, platforms: []}, {day: 30, platforms: []}],
};
const downloads = {metric: 'download_starts', daily: [{day: '2026-09-16', platform: 'windows', starts: 8}, {day: '2026-09-17', platform: 'android', starts: 4}]};
const response = body => ({ok: true, status: 200, json: async () => structuredClone(body)});

test('usage dashboard distinguishes starts, installations and not-yet-eligible retention', async () => {
  const app = harness(async (url, options) => {
    assert.ok(!url.includes('key'));
    assert.equal(options.headers.Authorization, 'Bearer private-local-test-key');
    assert.equal(options.cache, 'no-store');
    assert.equal(options.credentials, 'omit');
    return response(url.endsWith('/usage-stats') ? usage : downloads);
  });
  await app.login();
  const e = app.elements;
  assert.equal(e.usageReportKey.value, '');
  assert.equal(e.usageReportData.hidden, false);
  assert.equal(e.usageJourneyRows.children[0].children[3].textContent, '12');
  assert.equal(e.usageJourneyRows.children[1].children[3].textContent, '5');
  assert.equal(e.usageRetentionRows.children[0].children[1].textContent, '1 / 2 (50.0%)');
  assert.equal(e.usageRetentionRows.children[1].children[3].textContent, 'Not yet eligible');
});

test('usage outage never displays invented zero counts', async () => {
  const app = harness(async url => url.endsWith('/usage-stats') ? {ok: false, status: 503} : response(downloads));
  await app.login();
  assert.equal(app.elements.usageReportData.hidden, true);
  assert.equal(app.elements.usageJourneyRows.children.length, 0);
  assert.match(app.elements.usageReportStatus.textContent, /unavailable/);
});

test('independent download failure still displays valid usage without fake starts', async () => {
  const app = harness(async url => url.endsWith('/usage-stats') ? response(usage) : {ok: false, status: 503});
  await app.login();
  assert.equal(app.elements.usageReportData.hidden, false);
  assert.equal(app.elements.usageJourneyRows.children[0].children[3].textContent, '—');
});

test('locking and page exit clear data and late requests cannot reveal it', async () => {
  const resolvers = [];
  const app = harness(() => new Promise(r => { resolvers.push(r); }));
  await app.login();
  app.elements.usageReportLock.listeners.click();
  resolvers[0](response(usage));
  resolvers[1](response(downloads));
  await new Promise(r => setImmediate(r));
  assert.equal(app.elements.usageReportData.hidden, true);
  assert.equal(app.elements.usageReportKey.value, '');
  const loaded = harness(async url => response(url.endsWith('/usage-stats') ? usage : downloads));
  await loaded.login();
  loaded.events.pagehide();
  assert.equal(loaded.elements.usageReportData.hidden, true);
  assert.equal(loaded.elements.usageJourneyRows.children.length, 0);
});

test('malformed metrics and impossible retention are rejected', async () => {
  const invalid = structuredClone(usage);
  invalid.retention[0].platforms[0].returned = 99;
  const app = harness(async url => response(url.endsWith('/usage-stats') ? invalid : downloads));
  await app.login();
  assert.equal(app.elements.usageReportData.hidden, true);
});

test('server text renders as text, never executable HTML', async () => {
  const data = structuredClone(usage);
  data.versions[0].version = '<img src=x onerror=alert(1)>';
  const app = harness(async url => response(url.endsWith('/usage-stats') ? data : downloads));
  await app.login();
  const cell = app.elements.usageVersionRows.children[0].children[1];
  assert.equal(cell.textContent, data.versions[0].version);
  assert.equal(cell.innerHTML, undefined);
});
