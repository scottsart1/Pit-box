import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../src/worker.js', import.meta.url), 'utf8');
const {default: worker} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const windowsKey = 'PitWall-Setup.exe';
const androidKey = 'YourPitBox-4.12.3-android.22.apk';

function environment({missing = false} = {}) {
  const calls = [];
  const metadata = key => ({
    size: 6, httpEtag: '"fixture"',
    writeHttpMetadata(headers) {
      headers.set('content-type', key.endsWith('.apk') ? 'application/vnd.android.package-archive' : 'application/vnd.microsoft.portable-executable');
    },
  });
  const records = [];
  return {calls, records, DB: {prepare(sql) {
    return {bind(...values) { return {async run() { records.push({sql, values}); return {success: true}; }}; }};
  }}, DOWNLOADS: {
    async head(key) { calls.push(['head', key]); return missing ? null : metadata(key); },
    async get(key, options) {
      calls.push(['get', key]);
      if (missing) return null;
      const range = options?.range?.get('range');
      return {...metadata(key), body: range ? 'bc' : 'abcdef', ...(range ? {range: {offset: 1, length: 2}} : {})};
    },
  }};
}

for (const [route, key] of [['/installer', windowsKey], ['/android', androidKey]]) {
  test(`${route} GET streams only its pinned artifact`, async () => {
    const env = environment();
    const response = await worker.fetch(new Request('https://download.test' + route + '?key=private-secret'), env);
    assert.equal(response.status, 200);
    assert.equal(await response.text(), 'abcdef');
    assert.equal(response.headers.get('content-length'), '6');
    assert.equal(response.headers.get('content-disposition'), `attachment; filename="${key}"`);
    assert.deepEqual(env.calls, [['get', key]]);
    assert.equal(env.records.length, 1);
    assert.equal(env.records[0].values[1], route === '/installer' ? 'windows' : 'android');
    assert.equal(response.headers.get('cache-control'), 'private, no-store');
  });
  test(`${route} HEAD preserves metadata and has no body`, async () => {
    const env = environment();
    const response = await worker.fetch(new Request('https://download.test' + route, {method: 'HEAD'}), env);
    assert.equal(response.status, 200);
    assert.equal(await response.text(), '');
    assert.equal(response.headers.get('content-length'), '6');
    assert.deepEqual(env.calls, [['head', key]]);
    assert.equal(env.records.length, 0);
  });
  test(`${route} Range requests resume the selected download`, async () => {
    const response = await worker.fetch(new Request('https://download.test' + route, {headers: {Range: 'bytes=1-2'}}), environment());
    assert.equal(response.status, 206);
    assert.equal(response.headers.get('content-range'), 'bytes 1-2/6');
    assert.equal(await response.text(), 'bc');
  });
  test(`${route} missing artifact does not pretend success`, async () => {
    const response = await worker.fetch(new Request('https://download.test' + route), environment({missing: true}));
    assert.equal(response.status, 503);
  });
}

test('ranges, prefetches and failed downloads never increment', async () => {
  for (const route of ['/installer', '/android']) {
    for (const headers of [{Range: 'bytes=0-1'}, {Range: 'bytes=1-2'}, {Purpose: 'prefetch'}, {'Sec-Purpose': 'prefetch;prerender'}]) {
      const env = environment();
      await worker.fetch(new Request('https://download.test' + route, {headers}), env);
      assert.equal(env.records.length, 0);
    }
    const env = environment({missing: true});
    await worker.fetch(new Request('https://download.test' + route), env);
    assert.equal(env.records.length, 0);
  }
});

test('concurrent Windows and Android requests record once per full GET', async () => {
  const env = environment(), pending = [];
  const ctx = {waitUntil(promise) { pending.push(promise); }};
  await Promise.all(Array.from({length: 30}, (_, i) => worker.fetch(new Request(
    'https://download.test' + (i % 2 ? '/android' : '/installer')
  ), env, ctx)));
  await Promise.all(pending);
  assert.equal(env.records.length, 30);
  assert.equal(env.records.filter(record => record.values[1] === 'windows').length, 15);
  assert(env.records.every(record => record.values.length === 4));
});

test('slow or failing analytics never blocks the file response', async () => {
  const env = environment(), pending = [];
  let rejectWrite;
  env.DB = {prepare() { return {bind() { return {run() { return new Promise((_, reject) => { rejectWrite = reject; }); }}; }}; }};
  const response = await worker.fetch(new Request('https://download.test/android'), env, {
    waitUntil(promise) { pending.push(promise); },
  });
  assert.equal(await response.text(), 'abcdef');
  assert.equal(pending.length, 1);
  rejectWrite(new Error('database failure'));
  await Promise.all(pending);
});

const token = 'report-test-token-' + 'a'.repeat(32);
const reportRequest = (key = token, options = {}) => new Request('https://download.test/download-stats', {
  ...options, headers: key ? {Authorization: `Bearer ${key}`, Origin: 'https://yourpitbox.com'} : {},
});
test('download report fails closed for missing, wrong and unconfigured credentials', async () => {
  for (const [key, configured] of [[null, token], ['wrong', token], [token, undefined], [token, 'short']]) {
    const response = await worker.fetch(reportRequest(key), {DOWNLOAD_REPORT_TOKEN: configured});
    assert.equal(response.status, 401);
    assert.equal(response.headers.get('cache-control'), 'private, no-store');
  }
});

test('authorized report aggregates both platforms and stays private', async () => {
  const stamp = '2026-09-17T12:00:00.000Z';
  const totals = ['windows', 'android'].map((platform, i) => ({platform, starts: i + 2, first_started_at: stamp, last_started_at: stamp}));
  const daily = [{day: '2026-09-17', platform: 'android', starts: 3}];
  const env = {DOWNLOAD_REPORT_TOKEN: token, DB: {
    prepare(sql) { return {sql, bind(...values) { return {sql, values}; }}; },
    async batch(queries) {
      assert.equal(queries.length, 3);
      assert.match(queries[1].values[0], /^\d{4}-\d{2}-\d{2}$/);
      return [{success: true, results: totals}, {success: true, results: daily}, {success: true, results: []}];
    },
  }};
  const response = await worker.fetch(reportRequest(), env);
  assert.equal(response.status, 200);
  assert.equal(response.headers.get('access-control-allow-origin'), 'https://yourpitbox.com');
  assert.equal(response.headers.get('cache-control'), 'private, no-store');
  const data = await response.json();
  assert.deepEqual(data.totals, {windows: 2, android: 3, all: 5});
  assert.deepEqual(data.daily, daily);
  assert.equal(data.first_recorded_at, stamp);
  assert.equal(data.metric, 'download_starts');
  assert.equal(data.history, null);
  assert(!JSON.stringify(data).includes(token));
});

test('empty report is genuinely zero; database outage is unavailable, not zero', async () => {
  const env = {DOWNLOAD_REPORT_TOKEN: token, DB: {
    prepare() { return {bind() { return {}; }}; },
    async batch() { return Array.from({length: 3}, () => ({success: true, results: []})); },
  }};
  const response = await worker.fetch(reportRequest(), env);
  const data = await response.json();
  assert.deepEqual(data.totals, {windows: 0, android: 0, all: 0});
  assert.equal(data.first_recorded_at, null);
  env.DB.batch = async () => { throw new Error('unavailable'); };
  assert.equal((await worker.fetch(reportRequest(), env)).status, 503);
});

test('historical requests stay separate, reconcile and never expose import-only metadata', async () => {
  const history = {metric: 'historical_file_requests', source: 'Cloudflare R2 analytics',
    from: '2026-08-06T00:00:00Z', until: '2026-09-17T08:00:00Z', recovered_at: '2026-09-17T12:00:00Z',
    totals: {windows: 260, android: 10, all: 270},
    daily: [{day: '2026-09-16', platform: 'windows', requests: 260}, {day: '2026-09-17', platform: 'android', requests: 10}],
    private_import_metadata: 'not-for-response'};
  const env = {DOWNLOAD_REPORT_TOKEN: token, DB: {
    prepare(sql) { return {sql, bind() { return {sql}; }}; },
    async batch() { return [{success: true, results: [{platform: 'windows', starts: 1, first_started_at: '2026-09-17T10:00:00Z', last_started_at: '2026-09-17T10:00:00Z'}]},
      {success: true, results: []}, {success: true, results: [{report_json: JSON.stringify(history)}]}]; },
  }};
  const data = await (await worker.fetch(reportRequest(), env)).json();
  assert.equal(data.totals.all, 1);
  assert.equal(data.history.totals.all, 270);
  assert(!JSON.stringify(data).includes('not-for-response'));
  history.totals.all = 271;
  assert.equal((await worker.fetch(reportRequest(), env)).status, 503);
  history.totals.all = 270;
  history.daily.push({...history.daily[0]});
  assert.equal((await worker.fetch(reportRequest(), env)).status, 503);
});

test('report preflight permits the private Authorization header and no write methods', async () => {
  const response = await worker.fetch(reportRequest(null, {method: 'OPTIONS'}), {});
  assert.equal(response.status, 204);
  assert.equal(await response.text(), '');
  assert.equal(response.headers.get('access-control-allow-headers'), 'authorization');
  assert.equal((await worker.fetch(reportRequest(token, {method: 'POST'}), {})).status, 405);
});
