import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {DatabaseSync} from 'node:sqlite';
import test from 'node:test';
const source = await readFile(new URL('../src/worker.js', import.meta.url), 'utf8');
const {default: worker} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const migrations = await Promise.all(['0002_subscribers.sql', '0008_release_announcements.sql'].map(name => readFile(new URL('../migrations/' + name, import.meta.url), 'utf8')));
const publisher = 'fixture-release-publisher-key-distinct-123456';
const owner = 'fixture-owner-only-read-key-12345678901';
const report = 'fixture-report-only-key-123456789012345';
function setup(email = false) {
  const db = new DatabaseSync(':memory:'); db.exec('PRAGMA foreign_keys = ON'); migrations.forEach(sql => db.exec(sql));
  const env = {RELEASE_PUBLISH_TOKEN: publisher, OWNER_DASHBOARD_TOKEN: owner, DOWNLOAD_REPORT_TOKEN: report,
    OWNER_RATE_LIMITER: {limit: async () => ({success: true})}, DOWNLOADS: {head: async () => ({size: 123})},
    ...(email ? {RELEASE_EMAIL_ENABLED: 'true', RESEND_API_KEY: 'fixture-not-a-provider-key', RELEASE_EMAIL_FROM: 'updates@example.test', RELEASE_EMAIL_FOOTER: 'Your Pit Box test fixture footer'} : {}),
    DB: {prepare(sql) { return {sql, values: [], bind(...values) { this.values = values; return this; },
      async first() { return db.prepare(sql).get(...this.values) ?? null; },
      async all() { return {success: true, results: db.prepare(sql).all(...this.values)}; },
      async run() { return {success: true, meta: db.prepare(sql).run(...this.values)}; }}; },
      async batch(items) { db.exec('BEGIN'); try { const results = items.map(item => ({success: true, results: db.prepare(item.sql).all(...item.values)})); db.exec('COMMIT'); return results; } catch (e) { db.exec('ROLLBACK'); throw e; } },
    }};
  return {db, env, close: () => db.close()};
}
const payload = (changes = {}) => ({platform: 'windows', version: '4.12.0', size: 123, sha256: 'a'.repeat(64), notes: 'An update with improvements.', announce: false, ...changes});
const call = (f, path, body, token = publisher, origin = 'https://yourpitbox.com') => worker.fetch(new Request('https://worker.test' + path, {method: body === undefined ? 'GET' : 'POST', headers: {Authorization: `Bearer ${token}`, Origin: origin, ...(body === undefined ? {} : {'content-type': 'application/json'})}, ...(body === undefined ? {} : {body: JSON.stringify(body)})}), f.env, {});
const publish = (f, changes = {}) => call(f, '/release-admin/publish', payload(changes));
const add = (f, email, source = 'website-download') => f.db.prepare('INSERT INTO subscribers VALUES (?, ?, ?)').run(email, new Date().toISOString(), source);
async function tick(f) { const tasks = []; await worker.scheduled({cron: '*/5 * * * *'}, f.env, {waitUntil: promise => tasks.push(promise)}); await Promise.all(tasks); }

test('public release metadata is platform-specific and contains no private identities or credentials', async () => {
  const f = setup(); add(f, 'private@example.test');
  assert.equal((await publish(f)).status, 200);
  const response = await call(f, '/releases?platform=windows'); const body = await response.json();
  assert.equal(body.release.version, '4.12.0'); assert.equal(body.release.download_url, 'https://yourpitbox.com/#windows-release');
  assert.ok(!JSON.stringify(body).includes('private@example.test')); assert.ok(!JSON.stringify(body).includes(publisher));
  assert.equal((await (await call(f, '/releases?platform=android')).json()).release, null);
  assert.equal((await call(f, '/releases?platform=windows&current=4.1.0')).status, 400);
  assert.match(response.headers.get('cache-control'), /max-age=300/); f.close();
});
test('publication rejects old read-only keys, bad origins and missing artifacts', async () => {
  const f = setup();
  for (const token of ['', 'bad', owner, report]) assert.equal((await call(f, '/release-admin/publish', payload(), token)).status, 401);
  assert.equal((await call(f, '/release-admin/publish', payload(), publisher, 'https://attacker.test')).status, 403);
  f.env.DOWNLOADS.head = async () => null; assert.equal((await publish(f)).status, 409);
  assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM app_releases').get().n, 0); f.close();
});
test('releases are immutable, cannot downgrade, and retries do not create multiple rows', async () => {
  const f = setup();
  assert.equal((await publish(f)).status, 200); assert.equal((await publish(f)).status, 200);
  assert.equal((await publish(f, {notes: 'different'})).status, 409);
  assert.equal((await publish(f, {version: '4.9.9'})).status, 409);
  assert.equal((await publish(f, {platform: 'android', version: '4.12.0'})).status, 409);
  assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM app_releases').get().n, 1);
  assert.equal((await publish(f, {version: '4.13.0'})).status, 200);
  assert.equal((await (await call(f, '/releases?platform=windows')).json()).release.version, '4.13.0'); f.close();
});
test('invalid and oversized release input cannot publish or inject arbitrary download destinations', async () => {
  const f = setup();
  for (const bad of [{version: '4.12.0-beta'}, {size: -1}, {sha256: 'bad'}, {notes: ''}, {download_url: 'https://attacker.test'}, {announce: 'true'}, {notes: 'x'.repeat(9000)}]) assert.equal((await publish(f, bad)).status, 400);
  assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM app_releases').get().n, 0); f.close();
});
test('email stays disabled without configuration and deployment does not queue existing subscribers', async () => {
  const f = setup(); add(f, 'one@example.test');
  const response = await (await publish(f, {announce: true})).json(); assert.equal(response.email, 'not_configured');
  await tick(f); assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM release_deliveries').get().n, 0);
  const state = await (await call(f, '/owner/releases', undefined, owner)).json(); assert.equal(state.email_configured, false);
  assert.equal((await call(f, '/release-admin/announce', {platform: 'windows', version: '4.12.0', confirm: 'SEND RELEASE EMAIL'})).status, 503); f.close();
});
test('one immutable campaign snapshots eligible subscribers and sends individually with stable idempotency keys', async () => {
  const f = setup(true); add(f, 'one@example.test'); add(f, 'two@example.test'); add(f, 'android@example.test', 'website-download-android');
  await publish(f, {announce: true}); add(f, 'later@example.test'); await publish(f, {announce: true});
  assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM release_campaigns').get().n, 1);
  assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM release_deliveries').get().n, 2);
  const calls = [], original = globalThis.fetch;
  globalThis.fetch = async (url, opts) => { calls.push({url, opts}); return new Response(JSON.stringify({id: 'accepted-fixture-id'}), {status: 200}); };
  try { await tick(f); await tick(f); } finally { globalThis.fetch = original; }
  assert.equal(calls.length, 2);
  for (const item of calls) { const body = JSON.parse(item.opts.body); assert.equal(item.url, 'https://api.resend.com/emails'); assert.equal(body.to.length, 1); assert.ok(!body.cc && !body.bcc); assert.match(body.text, /Unsubscribe:/); assert.ok(body.headers['List-Unsubscribe-Post']); assert.match(item.opts.headers['Idempotency-Key'], /^release\//); }
  assert.equal(f.db.prepare("SELECT COUNT(*) AS n FROM release_deliveries WHERE state = 'accepted'").get().n, 2); f.close();
});
test('uncertain provider attempts reuse payload/key and stop before duplicate-protection expires', async () => {
  const f = setup(true); add(f, 'one@example.test'); await publish(f, {announce: true});
  const calls = [], original = globalThis.fetch;
  globalThis.fetch = async (url, opts) => { calls.push(opts); throw new Error('Ambiguous network error'); };
  try { await tick(f); await tick(f); f.db.prepare('UPDATE release_deliveries SET first_attempt_ms = ?').run(Date.now() - 24 * 3600000); await tick(f); } finally { globalThis.fetch = original; }
  assert.equal(calls.length, 2); assert.equal(calls[0].body, calls[1].body); assert.equal(calls[0].headers['Idempotency-Key'], calls[1].headers['Idempotency-Key']);
  assert.equal(f.db.prepare('SELECT state FROM release_deliveries').get().state, 'uncertain'); f.close();
});
test('unsubscribe requires POST, excludes pending recipients, and prevents silent re-subscription', async () => {
  const f = setup(true); add(f, 'one@example.test'); await publish(f, {announce: true});
  const token = f.db.prepare('SELECT token FROM release_email_preferences').get().token;
  assert.equal((await call(f, '/unsubscribe?token=' + token)).status, 200);
  assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM subscribers').get().n, 1);
  assert.equal((await call(f, '/unsubscribe?token=' + token, {})).status, 200);
  assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM subscribers').get().n, 0);
  assert.equal((await call(f, '/subscribe', {email: 'one@example.test', source: 'website-download'})).status, 200);
  assert.equal(f.db.prepare('SELECT COUNT(*) AS n FROM subscribers').get().n, 0);
  let sent = false; const original = globalThis.fetch; globalThis.fetch = async () => { sent = true; throw new Error('Must not send'); };
  try { await tick(f); } finally { globalThis.fetch = original; }
  assert.equal(sent, false); assert.equal(f.db.prepare('SELECT state FROM release_deliveries').get().state, 'suppressed'); f.close();
});
