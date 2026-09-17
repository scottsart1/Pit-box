import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

const source = await readFile(new URL('../src/worker.js', import.meta.url), 'utf8');
const {default: worker} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
const windowsKey = 'PitWall-Setup.exe';
const androidKey = 'YourPitBox-4.10.1-android.14.apk';

function environment({missing = false} = {}) {
  const calls = [];
  const metadata = key => ({
    size: 6, httpEtag: '"fixture"',
    writeHttpMetadata(headers) {
      headers.set('content-type', key.endsWith('.apk') ? 'application/vnd.android.package-archive' : 'application/vnd.microsoft.portable-executable');
    },
  });
  return {calls, DOWNLOADS: {
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
  });
  test(`${route} HEAD preserves metadata and has no body`, async () => {
    const env = environment();
    const response = await worker.fetch(new Request('https://download.test' + route, {method: 'HEAD'}), env);
    assert.equal(response.status, 200);
    assert.equal(await response.text(), '');
    assert.equal(response.headers.get('content-length'), '6');
    assert.deepEqual(env.calls, [['head', key]]);
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
