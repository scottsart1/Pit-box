import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {DatabaseSync} from 'node:sqlite';
import test from 'node:test';

const source = await readFile(new URL('../src/worker.js', import.meta.url), 'utf8');
const {default: worker} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const migrations = await Promise.all(['0002_subscribers.sql', '0005_download_counts.sql', '0006_download_history.sql', '0007_optional_usage.sql'].map(name => readFile(new URL('../migrations/' + name, import.meta.url), 'utf8')));
const ownerKey = 'fixture-owner-key-distinct-from-report-123456';
const reportKey = 'fixture-aggregate-report-key-no-emails-12345';
const today = new Date().toISOString().slice(0, 10);
function environment() {
  const db = new DatabaseSync(':memory:');
  for (const migration of migrations) db.exec(migration);
  let queries = 0, limited = false, failed = false;
  const env = {OWNER_DASHBOARD_TOKEN: ownerKey, DOWNLOAD_REPORT_TOKEN: reportKey,
    OWNER_RATE_LIMITER: {limit: async () => ({success: !limited})}, DB: {
      prepare(sql) { queries++; if (failed) throw new Error('Fixture unavailable'); return {sql, values: [], bind(...values) { this.values = values; return this; }, async first() { return db.prepare(sql).get(...this.values); }}; },
      async batch(items) { return items.map(item => ({success: true, results: db.prepare(item.sql).all(...item.values)})); },
    }};
  return {env, db, queries: () => queries, deny: () => { limited = true; }, fail: () => { failed = true; }};
}
const request = (env, path = '/owner/overview', token = ownerKey, options = {}) => worker.fetch(new Request('https://worker.test' + path, {
  method: options.method || 'GET', headers: {...(token ? {authorization: `Bearer ${token}`} : {}), ...(options.origin ? {origin: options.origin} : {})},
}), env);
function add(db, email, source = 'website-download') {
  db.prepare('INSERT INTO subscribers VALUES (?, ?, ?)').run(email, today + 'T12:00:00.000Z', source);
}

test('owner data denies missing, wrong, aggregate-only and query-string keys before SQL', async () => {
  const fixture = environment();
  for (const token of ['', 'wrong', reportKey]) for (const path of ['/owner/overview', '/owner/subscribers']) {
    const result = await request(fixture.env, path, token);
    assert.equal(result.status, 401); assert.equal(result.headers.get('cache-control'), 'private, no-store');
  }
  assert.equal((await request(fixture.env, '/owner/subscribers?key=' + ownerKey, '')).status, 401);
  fixture.env.OWNER_DASHBOARD_TOKEN = reportKey;
  assert.equal((await request(fixture.env, '/owner/subscribers', reportKey)).status, 401);
  assert.equal(fixture.queries(), 0);
  fixture.db.close();
});
test('owner origins, methods and rate limits fail closed without exposing addresses', async () => {
  const f = environment(); add(f.db, 'private@example.test');
  assert.equal((await request(f.env, '/owner/subscribers', ownerKey, {origin: 'https://attacker.test'})).status, 403);
  assert.equal((await request(f.env, '/owner/subscribers', ownerKey, {method: 'POST'})).status, 405);
  for (const origin of ['https://yourpitbox.com', 'https://www.yourpitbox.com']) {
    const response = await request(f.env, '/owner/subscribers', ownerKey, {origin});
    assert.equal(response.status, 200); assert.equal(response.headers.get('access-control-allow-origin'), origin);
    assert.equal(response.headers.get('x-robots-tag'), 'noindex, nofollow, noarchive');
  }
  const preflight = await request(f.env, '/owner/subscribers', '', {method: 'OPTIONS', origin: 'https://yourpitbox.com'});
  assert.equal(preflight.status, 204); assert.equal(await preflight.text(), '');
  f.deny(); assert.equal((await request(f.env)).status, 429);
  delete f.env.OWNER_RATE_LIMITER; assert.equal((await request(f.env)).status, 503);
  f.db.close();
});
test('overview combines genuine independent metrics without including email identities', async () => {
  const f = environment(); add(f.db, 'one@example.test'); add(f.db, 'two@example.test', 'website-download-android');
  f.db.prepare('INSERT INTO download_daily VALUES (?, ?, ?, ?, ?)').run(today, 'windows', 5, today, today);
  const response = await request(f.env), data = await response.json();
  assert.equal(response.status, 200); assert.equal(data.downloads.totals.all, 5);
  assert.equal(data.subscribers.total, 2); assert.equal(data.subscribers.new_30d, 2);
  assert.equal(data.usage.metric, 'opted_in_installations'); assert.deepEqual(data.usage.installations, []);
  assert.equal(JSON.stringify(data).includes('@'), false);
  f.fail(); const failed = await (await request(f.env)).json();
  assert.equal(failed.downloads, null); assert.equal(failed.usage, null); assert.equal(failed.subscribers, null);
  assert.equal((await request(f.env, '/owner/subscribers')).status, 503);
  f.db.close();
});
test('email pagination includes every row once and excludes new signups from the snapshot', async () => {
  const f = environment();
  for (let i = 0; i < 205; i++) add(f.db, `person${i}@example.test`);
  const first = await (await request(f.env, '/owner/subscribers?limit=100')).json();
  assert.equal(first.total, 205); assert.equal(first.subscribers.length, 100);
  add(f.db, 'new-after-snapshot@example.test');
  const second = await (await request(f.env, `/owner/subscribers?limit=100&snapshot=${first.snapshot}&before=${first.next}`)).json();
  const third = await (await request(f.env, `/owner/subscribers?limit=100&snapshot=${first.snapshot}&before=${second.next}`)).json();
  assert.equal(third.subscribers.length, 5); assert.equal(third.next, null);
  const all = [...first.subscribers, ...second.subscribers, ...third.subscribers];
  assert.equal(new Set(all.map(row => row.email)).size, 205);
  assert.ok(!all.some(row => row.email.startsWith('new-after')));
  assert.deepEqual(Object.keys(all[0]).sort(), ['created_at', 'email', 'source']);
  f.db.close();
});
test('invalid cursors and unbounded reads are rejected; empty data is a real zero', async () => {
  const f = environment();
  for (const query of ['limit=0', 'limit=201', 'limit=1&limit=2', 'snapshot=-1', 'before=1', 'snapshot=2&before=3', 'snapshot=not-a-number', 'snapshot=1&before=0', 'email=secret', 'limit=1%20OR%201%3D1']) {
    assert.equal((await request(f.env, '/owner/subscribers?' + query)).status, 400, query);
  }
  const empty = await (await request(f.env, '/owner/subscribers')).json();
  assert.equal(empty.total, 0); assert.deepEqual(empty.subscribers, []); assert.equal(empty.next, null);
  assert.equal((await request(f.env, '/owner/overview?extra=1')).status, 400);
  assert.equal((await request(f.env, '/owner/not-a-route')).status, 404);
  f.db.close();
});
