import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';
const html = await readFile(new URL('../../website/owner.html', import.meta.url), 'utf8');
const source = await readFile(new URL('../../website/owner.js', import.meta.url), 'utf8');
const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
const settle = async () => { for (let i = 0; i < 12; i++) await new Promise(resolve => setImmediate(resolve)); };
const now = '2026-09-18T00:00:00.000Z';
const overview = {generated_at: now, downloads: {metric: 'download_starts', first_recorded_at: '2026-09-16', totals: {windows: 5, android: 2, all: 7}, daily: [{day: '2026-09-17', platform: 'windows', starts: 5}, {day: '2026-09-18', platform: 'android', starts: 2}], history: null},
  subscribers: {metric: 'newsletter_subscribers', total: 3, new_30d: 3, sources: [{source: 'website-download', total: 3}]},
  usage: {metric: 'opted_in_installations', installations: [], features: [], recent: [], versions: [], retention: [1, 7, 30].map(day => ({day, platforms: []}))}};
const email = value => ({email: value, created_at: now, source: 'website-download'});
const emails = {generated_at: now, total: 3, snapshot: 3, next: null, subscribers: [email('one@example.test'), email('two@example.test'), email('three@example.test')]};
const response = body => ({ok: true, status: 200, json: async () => structuredClone(body)});
const history = {metric: 'historical_file_requests', source: 'Cloudflare R2 analytics', from: '2026-09-12T08:00:00Z', until: '2026-09-16T00:00:00Z', recovered_at: now,
  totals: {windows: 11, android: 3, all: 14}, daily: [{day: '2026-09-12', platform: 'windows', requests: 11}, {day: '2026-09-15', platform: 'android', requests: 3}]};
function element() { return {value: '', hidden: false, disabled: false, checked: true, textContent: '', children: [], listeners: {}, attributes: {}, addEventListener(name, fn) { this.listeners[name] = fn; }, replaceChildren() { this.children = []; }, appendChild(child) { this.children.push(child); }, setAttribute(k, v) { this.attributes[k] = v; }, focus() {}, select() { this.selected = true; }}; }
function harness(fetcher = async url => response(url.includes('/overview') ? overview : emails), clipboard = async () => {}) {
  const elements = Object.fromEntries(ids.map(id => [id, element()])), windowEvents = {}, docEvents = {}, timers = [], calls = [];
  elements.ownerData.hidden = true;
  let time = Date.parse(now);
  class Clock extends Date { static now() { return time; } }
  const document = {getElementById: id => elements[id], createElement: element, visibilityState: 'visible', addEventListener: (name, fn) => { docEvents[name] = fn; }};
  const context = vm.createContext({document, window: {addEventListener: (name, fn) => { windowEvents[name] = fn; }}, navigator: {clipboard: {writeText: clipboard}}, AbortController, Date: Clock, Intl, setTimeout, clearTimeout, setInterval: fn => timers.push(fn),
    fetch: async (url, options) => { calls.push({url, options}); return fetcher(url, options); }});
  vm.runInContext(source, context);
  return {elements, calls, document, windowEvents, docEvents, timers, advance: ms => { time += ms; }, async login() { elements.ownerKey.value = 'private-owner-fixture-key-not-a-secret'; elements.ownerLogin.listeners.submit({preventDefault() {}}); await settle(); }};
}
test('owner login loads true metrics and email rows; key never enters URLs or persisted storage', async () => {
  const h = harness(); await h.login();
  assert.equal(h.elements.ownerKey.value, ''); assert.equal(h.elements.ownerData.hidden, false);
  assert.equal(h.elements.metricDownloads.textContent, '7'); assert.equal(h.elements.metricEmails.textContent, '3');
  assert.equal(h.elements.metricRetention.textContent, 'Not yet eligible'); assert.equal(h.elements.emailRows.children.length, 3);
  for (const call of h.calls) { assert.ok(!call.url.includes('private-owner')); assert.equal(call.options.cache, 'no-store'); assert.equal(call.options.credentials, 'omit'); assert.equal(call.options.redirect, 'error'); }
  assert.ok(!/localStorage|sessionStorage|innerHTML/.test(source));
});
test('copy all fetches all pages, not only displayed rows, and writes each address once', async () => {
  let copied = '';
  const h = harness(async url => {
    if (url.includes('/overview')) return response(overview);
    if (!url.includes('limit=200')) return response({...emails, next: 3, subscribers: [emails.subscribers[0]]});
    return response(url.includes('before=') ? {...emails, subscribers: emails.subscribers.slice(1)} : {...emails, next: 3, subscribers: [emails.subscribers[0]]});
  }, async value => { copied = value; });
  await h.login(); assert.equal(h.elements.emailRows.children.length, 1);
  await h.elements.copyAllEmails.listeners.click();
  assert.equal(copied, 'one@example.test\ntwo@example.test\nthree@example.test');
  assert.match(h.elements.emailStatus.textContent, /Copied all 3/);
});
test('clipboard refusal offers selectable plain text; locking removes all loaded email data', async () => {
  const h = harness(undefined, async () => { throw new Error('Denied'); }); await h.login();
  await h.elements.copyAllEmails.listeners.click();
  assert.equal(h.elements.emailCopyFallback.hidden, false); assert.ok(h.elements.emailCopyText.value.includes('@'));
  h.elements.selectEmails.listeners.click(); assert.equal(h.elements.emailCopyText.selected, true);
  h.elements.ownerLock.listeners.click();
  assert.equal(h.elements.emailCopyText.value, ''); assert.equal(h.elements.emailRows.children.length, 0); assert.equal(h.elements.ownerData.hidden, true);
});
test('no partial copy on paging error or malformed email content', async () => {
  let copied = false, broken = false;
  const h = harness(async url => {
    if (url.includes('/overview')) return response(overview);
    if (broken && url.includes('limit=200')) return response({...emails, subscribers: [email('bad\naddress@example.test')]});
    return response(emails);
  }, async () => { copied = true; });
  await h.login(); broken = true; await h.elements.copyAllEmails.listeners.click();
  assert.equal(copied, false); assert.match(h.elements.emailStatus.textContent, /Invalid email/);
});
test('automatic refresh runs visible and unlocked, but inactivity and hidden-page limits lock', async () => {
  const h = harness(); await h.login(); const initial = h.calls.length;
  h.advance(60000); h.timers[0](); await settle(); assert.ok(h.calls.length > initial);
  h.advance(15 * 60000); h.timers[0](); assert.equal(h.elements.ownerData.hidden, true);
  await h.login(); h.document.visibilityState = 'hidden'; h.docEvents.visibilitychange(); const count = h.calls.length;
  h.advance(60000); h.timers[0](); await settle(); assert.equal(h.calls.length, count);
  h.advance(5 * 60000); h.document.visibilityState = 'visible'; h.docEvents.visibilitychange(); assert.equal(h.elements.ownerData.hidden, true);
});
test('old requests cannot repopulate the dashboard after locking', async () => {
  let resolve; const h = harness(() => new Promise(done => { resolve = done; }));
  await h.login(); h.elements.ownerLock.listeners.click(); resolve(response(overview)); await settle();
  assert.equal(h.elements.ownerData.hidden, true); assert.equal(h.elements.metricDownloads.textContent, '—'); assert.equal(h.calls.length, 1);
});
test('wrong keys and unavailable reports do not show fabricated zero totals', async () => {
  const denied = harness(async () => ({ok: false, status: 401})); await denied.login();
  assert.equal(denied.elements.ownerData.hidden, true); assert.match(denied.elements.ownerStatus.textContent, /not accepted/);
  const missing = harness(async url => response(url.includes('/overview') ? {generated_at: now, downloads: null, usage: null, subscribers: null} : emails)); await missing.login();
  assert.equal(missing.elements.metricDownloads.textContent, '—'); assert.equal(missing.elements.metricActive.textContent, '—');
  assert.match(missing.elements.ownerStatus.textContent, /unavailable/);
});

test('recovered history populates the overview and complete daily coverage without changing live counts', async () => {
  const data = structuredClone(overview); data.downloads.history = history;
  const original = JSON.stringify(data);
  const h = harness(async url => response(url.includes('/overview') ? data : emails)); await h.login();
  assert.equal(h.elements.metricRecorded.textContent, '21');
  assert.equal(h.elements.metricDownloads.textContent, '7');
  assert.equal(h.elements.metricRecentDownloads.textContent, '7');
  assert.match(h.elements.metricRecordedSources.textContent, /14 historical reads \+ 7 live starts/);
  assert.match(h.elements.metricRecordedSources.textContent, /different counting methods/);
  const cells = element => element.children.map(tr => tr.children.map(td => td.textContent));
  assert.deepEqual(cells(h.elements.downloadPlatformRows), [['Windows', '11', '5', '16', '5'], ['Android', '3', '2', '5', '2']]);
  assert.equal(h.elements.historyDetails.hidden, false);
  assert.deepEqual(cells(h.elements.historyDailyRows), [['2026-09-15', '0', '3', '3'], ['2026-09-14', '0', '0', '0'], ['2026-09-13', '0', '0', '0'], ['2026-09-12', '11', '0', '11']]);
  assert.match(h.elements.historyCoverage.textContent, /end exclusive/);
  assert.equal(JSON.stringify(data), original);
  await h.elements.ownerRefresh.listeners.click();
  assert.equal(h.elements.historyDailyRows.children.length, 4); // refresh must not append or double count
  assert.equal(h.elements.metricRecorded.textContent, '21');
  h.elements.ownerLock.listeners.click();
  assert.equal(h.elements.metricRecorded.textContent, '—');
  assert.equal(h.elements.metricRecordedSources.textContent, '');
  assert.equal(h.elements.historyDailyRows.children.length, 0);
  assert.equal(h.elements.historyDetails.hidden, true);
});

test('missing history is not silently zero or a fake lifetime total, and removes stale historical rows', async () => {
  const data = structuredClone(overview); data.downloads.history = structuredClone(history);
  const h = harness(async url => response(url.includes('/overview') ? data : emails)); await h.login();
  data.downloads.history = null;
  await h.elements.ownerRefresh.listeners.click();
  assert.equal(h.elements.metricRecorded.textContent, '—');
  assert.equal(h.elements.metricDownloads.textContent, '7');
  assert.match(h.elements.metricRecordedSources.textContent, /unavailable/);
  assert.equal(h.elements.historyDailyRows.children.length, 0);
  assert.equal(h.elements.historyDetails.hidden, true);
});

test('inconsistent, duplicated, out-of-window or overlapping history is never added to live totals', async () => {
  for (const corrupt of [
    d => { d.history.totals.all++; },
    d => { d.history.daily.push({...d.history.daily[0]}); },
    d => { d.history.daily[0].day = '2026-09-11'; },
    d => { d.history.daily[0].day = '2026-09-16'; }, // exclusive midnight bound
    d => { d.history.daily[0].day = '2026-02-30'; },
    d => { d.history.until = '2026-09-17T00:00:00Z'; }, // overlaps first live start
    d => { d.history.source = 'Unknown'; },
    d => { d.history.from = '2024-09-12T08:00:00Z'; },
    d => { d.history.daily = Array(731).fill(d.history.daily[0]); },
    d => { d.history.totals = {windows: Number.MAX_SAFE_INTEGER, android: 0, all: Number.MAX_SAFE_INTEGER}; d.history.daily = [{day: '2026-09-12', platform: 'windows', requests: Number.MAX_SAFE_INTEGER}]; },
  ]) {
    const data = structuredClone(overview); data.downloads.history = structuredClone(history); corrupt(data.downloads);
    const h = harness(async url => response(url.includes('/overview') ? data : emails)); await h.login();
    assert.equal(h.elements.ownerData.hidden, true);
    assert.equal(h.elements.metricRecorded.textContent, '—');
    assert.equal(h.elements.historyDailyRows.children.length, 0);
  }
});

test('versions render newest first across platforms and stay sorted after refresh', async () => {
  const data = structuredClone(overview);
  const fixtures = [
    ['windows', '4.9.8', 8], ['android', '5.5.0', 5], ['windows', 'unknown', 1],
    ['windows', '5.9.0', 9], ['windows', '5.10.0-rc.2', 2], ['windows', '5.5.0', 6],
    ['android', '5.10.0', 10], ['windows', '4.10.0', 4], ['windows', '5.10.0-rc.10', 3],
    ['windows', 'v5.10.0+build.7', 7], ['windows', '6.0.0-beta.1', 11],
  ];
  data.usage.versions = Object.freeze(fixtures.map(([platform, version, installations]) => Object.freeze({platform, version, installations})));
  const before = JSON.stringify(data.usage.versions);
  // Return the frozen report itself so an in-place sort would fail.
  const h = harness(async url => url.includes('/overview') ? {ok: true, status: 200, json: async () => data} : response(emails));
  const expected = [
    ['windows', '6.0.0-beta.1', '11'], ['windows', 'v5.10.0+build.7', '7'],
    ['android', '5.10.0', '10'], ['windows', '5.10.0-rc.10', '3'],
    ['windows', '5.10.0-rc.2', '2'], ['windows', '5.9.0', '9'],
    ['windows', '5.5.0', '6'], ['android', '5.5.0', '5'], ['windows', '4.10.0', '4'],
    ['windows', '4.9.8', '8'], ['windows', 'unknown', '1'],
  ];
  const rows = () => h.elements.versionRows.children.map(tr => tr.children.map(td => td.textContent));
  await h.login();
  assert.deepEqual(rows(), expected);
  assert.equal(JSON.stringify(data.usage.versions), before);
  await h.elements.ownerRefresh.listeners.click();
  assert.deepEqual(rows(), expected);
  h.elements.ownerLock.listeners.click();
  assert.deepEqual(rows(), []);
});

const profile = '12345678-1234-4123-8123-123456789abc', other = '87654321-4321-4321-8321-cba987654321';
const accounts = {metric: 'profile_sign_ins', retention_days: 180, window_days: 30, account_limit: 200,
  summary: {accounts: 2, starts: 5, returning: 1, new_accounts: 1},
  platforms: [{platform: 'android', accounts: 1, starts: 4}, {platform: 'windows', accounts: 1, starts: 1}],
  accounts: [
    {account_id: profile, platform: 'android', version: '5.7.1-android.43', starts: 6, starts_30d: 4, active_days: 3, first_start: 1700000000, last_start: 1700172800},
    {account_id: other, platform: 'windows', version: '5.7.1-windows', starts: 1, starts_30d: 1, active_days: 1, first_start: 1700086400, last_start: 1700086400},
  ],
  daily: [{day: '2023-11-16', accounts: 1, starts: 2}, {day: '2023-11-15', accounts: 2, starts: 3}]};
const profileHistory = {metric: 'profile_sign_in_history', account_id: profile, retention_days: 180, limit: 500,
  summary: {starts: 3, active_days: 2, first_start: 1700000000, last_start: 1700172800},
  sign_ins: [
    {occurred_at: 1700172800, received_at: 1700172860, version: '5.7.1-android.43', platform: 'android'},
    {occurred_at: 1700000100, received_at: 1700090000, version: '5.7.0-android.42', platform: 'android'},
    {occurred_at: 1700000000, received_at: 1700000005, version: '5.7.0-android.42', platform: 'android'},
  ]};
const cellsOf = element => element.children.map(tr => tr.children.map(td => td.textContent));
const routes = (overrides = {}) => async url => {
  for (const [path, body] of Object.entries(overrides)) if (url.endsWith(path)) return typeof body === 'function' ? body() : response(body);
  if (url.includes('/overview')) return response(overview);
  if (url.endsWith(`/owner/accounts/${profile}`)) return response(profileHistory);
  if (url.endsWith('/owner/accounts')) return response(accounts);
  return response(emails);
};

test('profile sign-ins show each profile with platform, 30- and 180-day counts and latest version', async () => {
  const h = harness(routes()); await h.login();
  assert.equal(h.elements.metricProfiles.textContent, '2'); assert.equal(h.elements.metricSignIns.textContent, '5');
  assert.equal(h.elements.metricReturning.textContent, '1'); assert.equal(h.elements.metricNewProfiles.textContent, '1');
  assert.deepEqual(h.elements.accountRows.children.map(tr => tr.children.slice(0, 8).map(td => td.textContent)), [
    [profile, 'Android', '4', '6', '3', '2023-11-14 22:13', '2023-11-16 22:13', '5.7.1-android.43'],
    [other, 'Windows', '1', '1', '1', '2023-11-15 22:13', '2023-11-15 22:13', '5.7.1-windows'],
  ]);
  assert.deepEqual(cellsOf(h.elements.accountPlatformRows), [['Android', '1', '4'], ['Windows', '1', '1']]);
  assert.deepEqual(cellsOf(h.elements.accountDailyRows), [['2023-11-16', '1', '2'], ['2023-11-15', '2', '3']]);
  assert.match(h.elements.accountStatus.textContent, /2 profiles signed in 5 times in the last 30 days/);
  const button = h.elements.accountRows.children[0].children[8].children[0];
  assert.equal(button.textContent, 'Sign-ins'); assert.equal(button.type, 'button');
  assert.match(button.attributes['aria-label'], new RegExp(profile));
  assert.equal(h.elements.accountHistory.hidden, true);
});

test('one profile\'s sign-ins open newest first, follow refreshes, and clear on close and lock', async () => {
  const h = harness(routes()); await h.login();
  await h.elements.accountRows.children[0].children[8].children[0].listeners.click(); await settle();
  const historyCalls = () => h.calls.filter(call => call.url.endsWith(`/owner/accounts/${profile}`));
  assert.equal(historyCalls().length, 1);
  assert.equal(historyCalls()[0].options.headers.Authorization, 'Bearer private-owner-fixture-key-not-a-secret');
  assert.equal(h.elements.accountHistory.hidden, false);
  assert.equal(h.elements.accountHistoryTitle.textContent, `Profile ${profile}`);
  assert.deepEqual(cellsOf(h.elements.accountHistoryRows), [
    ['2023-11-16 22:13:20', '2023-11-16 22:14:20', 'Android', '5.7.1-android.43'],
    ['2023-11-14 22:15:00', '2023-11-15 23:13:20', 'Android', '5.7.0-android.42'],
    ['2023-11-14 22:13:20', '2023-11-14 22:13:25', 'Android', '5.7.0-android.42'],
  ]);
  assert.match(h.elements.accountHistoryStatus.textContent, /3 sign-ins on 2 days, from 2023-11-14 22:13:20 to 2023-11-16 22:13:20\.$/);
  await h.elements.ownerRefresh.listeners.click(); await settle();
  assert.equal(historyCalls().length, 2); assert.equal(h.elements.accountHistoryRows.children.length, 3);
  h.elements.accountHistoryClose.listeners.click();
  assert.equal(h.elements.accountHistory.hidden, true); assert.equal(h.elements.accountHistoryRows.children.length, 0);
  await h.elements.ownerRefresh.listeners.click(); await settle();
  assert.equal(historyCalls().length, 2); // a closed panel is not reloaded
  await h.elements.accountRows.children[0].children[8].children[0].listeners.click(); await settle();
  h.elements.ownerLock.listeners.click();
  for (const id of ['accountRows', 'accountPlatformRows', 'accountDailyRows', 'accountHistoryRows']) assert.equal(h.elements[id].children.length, 0);
  for (const id of ['metricProfiles', 'metricSignIns', 'metricReturning', 'metricNewProfiles']) assert.equal(h.elements[id].textContent, '—');
  assert.equal(h.elements.accountHistory.hidden, true); assert.equal(h.elements.accountStatus.textContent, '');
  assert.equal(h.elements.accountHistoryStatus.textContent, '');
});

test('a profile history shows only what was asked for, and partial lists say so', async () => {
  const many = {...profileHistory, summary: {...profileHistory.summary, starts: 900}};
  const h = harness(routes({[`/owner/accounts/${profile}`]: many})); await h.login();
  await h.elements.accountRows.children[0].children[8].children[0].listeners.click(); await settle();
  assert.match(h.elements.accountHistoryStatus.textContent, /900 sign-ins.*Showing the latest 3\./);
  for (const wrong of [{...profileHistory, account_id: other}, {...profileHistory, sign_ins: [...profileHistory.sign_ins].reverse()},
    {...profileHistory, sign_ins: [{...profileHistory.sign_ins[0], platform: 'ios'}]}, {...profileHistory, metric: 'profile_sign_ins'}]) {
    const bad = harness(routes({[`/owner/accounts/${profile}`]: wrong})); await bad.login();
    await bad.elements.accountRows.children[0].children[8].children[0].listeners.click(); await settle();
    assert.equal(bad.elements.accountHistoryRows.children.length, 0);
    assert.match(bad.elements.accountHistoryStatus.textContent, /unavailable\. This does not mean zero/);
  }
  const failing = harness(routes({[`/owner/accounts/${profile}`]: () => ({ok: false, status: 503})})); await failing.login();
  await failing.elements.accountRows.children[0].children[8].children[0].listeners.click(); await settle();
  assert.match(failing.elements.accountHistoryStatus.textContent, /unavailable/); assert.equal(failing.elements.ownerData.hidden, false);
});

test('missing or malformed profile reports never show zero sign-ins', async () => {
  const missing = harness(); await missing.login();
  assert.match(missing.elements.accountStatus.textContent, /unavailable\. This does not mean zero sign-ins/);
  assert.equal(missing.elements.metricProfiles.textContent, '—'); assert.equal(missing.elements.accountRows.children.length, 0);
  const corruptions = [
    d => { delete d.summary.returning; },
    d => { d.summary.returning = 3; },
    d => { d.accounts[0].account_id = 'not-a-profile-id-but-thirty-six-char'; },
    d => { d.accounts[1].account_id = profile; },
    d => { d.accounts[0].starts_30d = 7; },
    d => { d.accounts[0].first_start = d.accounts[0].last_start + 1; },
    d => { d.accounts[0].platform = 'ios'; },
    d => { d.accounts[0].version = 'x'.repeat(65); },
    d => { d.platforms.push({...d.platforms[0]}); },
    d => { d.daily[0].day = '16/11/2023'; },
    d => { d.accounts = Array(201).fill(d.accounts[0]); },
  ];
  for (const corrupt of corruptions) {
    const data = structuredClone(accounts); corrupt(data);
    const h = harness(routes({'/owner/accounts': data})); await h.login();
    assert.match(h.elements.accountStatus.textContent, /unavailable/, corrupt.toString());
    assert.equal(h.elements.accountRows.children.length, 0); assert.equal(h.elements.metricSignIns.textContent, '—');
  }
  const empty = {...accounts, summary: {accounts: 0, starts: 0, returning: 0, new_accounts: 0}, platforms: [], accounts: [], daily: []};
  const none = harness(routes({'/owner/accounts': empty})); await none.login();
  assert.match(none.elements.accountStatus.textContent, /No profile sign-ins recorded yet/); assert.equal(none.elements.metricProfiles.textContent, '0');
});
