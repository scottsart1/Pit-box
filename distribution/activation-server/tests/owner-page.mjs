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
