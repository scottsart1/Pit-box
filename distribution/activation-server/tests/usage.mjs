import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {DatabaseSync} from 'node:sqlite';
import {randomUUID} from 'node:crypto';
import test from 'node:test';

const source = await readFile(new URL('../src/worker.js', import.meta.url), 'utf8');
const {default: worker} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const migration = await readFile(new URL('../migrations/0007_optional_usage.sql', import.meta.url), 'utf8');
const day = ago => new Date(Date.now() - ago * 86400000).toISOString().slice(0, 10);
const reportKey = 'local-usage-test-key-never-a-real-secret';

function environment() {
  const db = new DatabaseSync(':memory:');
  db.exec('PRAGMA foreign_keys = ON');
  db.exec(migration);
  let allowed = true, fail = false;
  const env = {DOWNLOAD_REPORT_TOKEN: reportKey, USAGE_RATE_LIMITER: {limit: async () => ({success: allowed})}, DB: {
    prepare(sql) {
      const item = {values: [], sql, bind(...values) { this.values = values; return this; },
        async first() { return db.prepare(sql).get(...this.values) ?? null; }};
      return item;
    },
    async batch(items) {
      if (fail) throw new Error('Fixture storage unavailable');
      db.exec('BEGIN');
      try {
        const results = items.map(item => {
          const statement = db.prepare(item.sql);
          return /^\s*SELECT/i.test(item.sql)
            ? {success: true, results: statement.all(...item.values)}
            : {success: true, results: [], meta: statement.run(...item.values)};
        });
        db.exec('COMMIT');
        return results;
      } catch (error) { db.exec('ROLLBACK'); throw error; }
    },
  }};
  return {env, db, deny: () => { allowed = false; }, fail: () => { fail = true; }};
}
function payload(changes = {}) {
  return {consent_version: 1, installation_id: randomUUID(), platform: 'windows', version: '4.10.2',
    events: [{day: day(0), event: 'app_started'}, {day: day(0), event: 'app_used'}], ...changes};
}
const send = (env, data) => worker.fetch(new Request('https://local.test/usage', {
  method: 'POST', headers: {'content-type': 'application/json'}, body: JSON.stringify(data),
}), env);
const report = env => worker.fetch(new Request('https://local.test/usage-stats', {headers: {authorization: `Bearer ${reportKey}`}}), env);

test('opt-in report stores allowlisted daily flags; retries cannot inflate totals', async () => {
  const {env, db} = environment(), data = payload();
  assert.equal((await send(env, data)).status, 200);
  assert.equal((await send(env, data)).status, 200);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM usage_daily').get().n, 2);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM usage_installations').get().n, 1);
  const identity = db.prepare('SELECT installation_hash FROM usage_installations').get().installation_hash;
  assert.match(identity, /^[a-f0-9]{64}$/);
  assert.notEqual(identity, data.installation_id);
  const response = await report(env), result = await response.json();
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
  assert.equal(result.installations[0].new_30d, 1);
  assert.equal(result.features.find(row => row.event === 'app_used').installations, 1);
  assert.ok(!JSON.stringify(result).includes(identity));
  assert.ok(!JSON.stringify(result).includes(data.installation_id));
  db.close();
});

test('bad consent, identities, content, dates and unknown fields are rejected before storage', async () => {
  const {env, db} = environment();
  const cases = [null, [], payload({consent_version: 0}), payload({consent_version: '1'}),
    payload({installation_id: 'hardware-id'}), payload({platform: 'ios'}), payload({version: 'my email'}),
    payload({events: []}), payload({events: Array(29).fill({day: day(0), event: 'app_used'})}),
    payload({email: 'private@example.test'}), payload({events: [{day: day(0), event: 'transcript'}]}),
    payload({events: [{day: day(0), event: 'racing', speed: 120}]}),
    payload({events: [{day: day(7), event: 'racing'}]}), payload({events: [{day: day(-1), event: 'racing'}]})];
  for (const data of cases) assert.equal((await send(env, data)).status, 400);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM usage_installations').get().n, 0);
  db.close();
});

test('oversize bodies, non-JSON requests and alternate methods cannot write', async () => {
  const {env, db} = environment();
  assert.equal((await send(env, payload({padding: 'x'.repeat(9000)}))).status, 400);
  assert.equal((await worker.fetch(new Request('https://local.test/usage', {method: 'POST', body: JSON.stringify(payload())}), env)).status, 400);
  for (const method of ['GET', 'PUT', 'OPTIONS']) assert.equal((await worker.fetch(new Request('https://local.test/usage', {method}), env)).status, 405);
  db.close();
});

test('report authentication is independent of public ingestion; fails closed', async () => {
  const {env, db} = environment();
  for (const token of ['', 'wrong']) {
    const response = await worker.fetch(new Request('https://local.test/usage-stats', {headers: {authorization: token}}), env);
    assert.equal(response.status, 401);
  }
  assert.equal((await worker.fetch(new Request('https://local.test/usage-stats', {method: 'POST'}), env)).status, 405);
  env.DOWNLOAD_REPORT_TOKEN = '';
  assert.equal((await report(env)).status, 401);
  db.close();
});

test('rate limiting and storage outages are recoverable without claiming success', async () => {
  const limited = environment();
  limited.deny();
  const response = await send(limited.env, payload());
  assert.equal(response.status, 429);
  assert.equal(response.headers.get('retry-after'), '60');
  assert.equal(limited.db.prepare('SELECT COUNT(*) n FROM usage_installations').get().n, 0);
  delete limited.env.USAGE_RATE_LIMITER;
  assert.equal((await send(limited.env, payload())).status, 503);
  limited.db.close();
  const failed = environment();
  failed.fail();
  assert.equal((await send(failed.env, payload())).status, 503);
  assert.equal((await report(failed.env)).status, 503);
  failed.db.close();
});

test('platform identity is stable and multiple devices are not conflated', async () => {
  const {env, db} = environment();
  const first = payload();
  await send(env, first);
  assert.equal((await send(env, {...first, platform: 'android'})).status, 409);
  await send(env, payload({platform: 'android'}));
  const result = await (await report(env)).json();
  assert.equal(result.installations.length, 2);
  assert.equal(result.installations.reduce((n, row) => n + row.installations, 0), 2);
  db.close();
});

test('retention uses eligible exact-day cohorts; later activity does not substitute', async () => {
  const {env, db} = environment();
  const insert = db.prepare('INSERT INTO usage_installations VALUES (?, ?, ?, ?, ?, 1)');
  const active = db.prepare("INSERT INTO usage_daily VALUES (?, ?, 'app_used')");
  insert.run('a', 'windows', '4.10.2', day(8), day(1));
  insert.run('b', 'windows', '4.10.2', day(8), day(0));
  insert.run('c', 'android', '4.10.2', day(0), day(0));
  active.run('a', day(1)); // Exactly day 7 after day -8.
  active.run('b', day(0)); // Day 8, not day 7.
  active.run('c', day(0)); // Too new for all retention rows.
  const result = await (await report(env)).json();
  assert.deepEqual(result.retention[1].platforms, [{platform: 'windows', eligible: 2, returned: 1}]);
  assert.deepEqual(result.retention[2].platforms, []);
  assert.equal(result.recent.find(row => row.platform === 'windows').active_7d, 2);
  db.close();
});

test('scheduled retention removes old flags and inactive identities, keeps active metadata', async () => {
  const {env, db} = environment();
  const insert = db.prepare('INSERT INTO usage_installations VALUES (?, ?, ?, ?, ?, 1)');
  insert.run('old', 'windows', '4.10.2', day(100), day(90));
  insert.run('active', 'android', '4.10.2', day(100), day(0));
  db.prepare("INSERT INTO usage_daily VALUES (?, ?, 'app_used')").run('old', day(90));
  db.prepare("INSERT INTO usage_daily VALUES (?, ?, 'app_used')").run('active', day(90));
  db.prepare("INSERT INTO usage_daily VALUES (?, ?, 'app_used')").run('active', day(0));
  const jobs = [];
  await worker.scheduled({}, env, {waitUntil: job => jobs.push(job)});
  await Promise.all(jobs);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM usage_daily').get().n, 1);
  assert.equal(db.prepare('SELECT COUNT(*) n FROM usage_installations').get().n, 1);
  assert.equal(db.prepare('SELECT installation_hash FROM usage_installations').get().installation_hash, 'active');
  db.close();
});
