import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import test from 'node:test';

const source = await readFile(new URL('../../website/download.js', import.meta.url), 'utf8');
function element() {
  return {hidden: false, disabled: false, value: '', textContent: '', dataset: {}, listeners: {},
    addEventListener(event, fn) { this.listeners[event] = fn; },
    focus() { this.focused = true; }};
}
function harness({guide = false, fetcher} = {}) {
  const ids = ['downloadButton', 'downloadStatus', 'androidDownloadStatus', 'emailModal', 'emailForm',
    'emailField', 'emailStatus', 'skipEmail', 'codePanel', 'freeCode', 'copyCode', 'emailModalPlatform'];
  const e = Object.fromEntries(ids.map(id => [id, element()]));
  const links = [element(), element()], submit = element(), cancel = element();
  e.emailModal.hidden = true;
  e.emailForm.querySelector = () => submit;
  e.emailModal.querySelectorAll = selector => selector === '[data-close]' ? [cancel] : [e.emailField, submit, e.skipEmail, cancel];
  const events = {}, calls = [], navigations = [];
  const document = {activeElement: links[0],
    getElementById: id => guide && ['downloadButton', 'downloadStatus', 'codePanel', 'freeCode', 'copyCode'].includes(id) ? null : e[id],
    querySelectorAll: () => links,
    addEventListener: (type, fn) => { events[type] = fn; },
    removeEventListener: type => { delete events[type]; }};
  const location = {set href(value) { navigations.push(value); }};
  vm.runInNewContext(source, {document, window: {location}, navigator: {}, AbortController, setTimeout, clearTimeout,
    fetch: async (url, options) => {
      calls.push({url, options});
      return fetcher ? fetcher(url, options) : {ok: true, json: async () => ({needs_code: false})};
    }});
  const click = (link = links[0], extras = {}) => {
    let prevented = false;
    link.listeners.click({preventDefault() { prevented = true; }, button: 0, ...extras});
    return prevented;
  };
  return {e, links, submit, cancel, events, calls, navigations, document, click};
}

for (const guide of [false, true]) {
  test(`Android popup and skip select APK without Windows metadata (${guide ? 'guide' : 'homepage'})`, async () => {
    const app = harness({guide});
    assert.equal(app.click(), true);
    assert.equal(app.e.emailModal.hidden, false);
    assert.match(app.e.emailModalPlatform.textContent, /Android/);
    assert.equal(app.navigations.length, 0);
    await app.e.skipEmail.listeners.click();
    assert.deepEqual(app.navigations, ['https://pitwall-activation.sarthakvij123450.workers.dev/android']);
    assert.equal(app.calls.length, 0);
    assert.match(app.e.androidDownloadStatus.textContent, /downloaded APK/);
    assert(!app.e.androidDownloadStatus.textContent.includes('PitWall-Setup.exe'));
    assert.equal(app.e.emailModal.hidden, true);
  });
}

test('Android optional signup is platform-tagged and never downloads Windows', async () => {
  const app = harness();
  app.click(app.links[1]);
  app.e.emailField.value = 'test@example.invalid';
  await app.e.emailForm.listeners.submit({preventDefault() {}});
  assert.equal(app.calls.length, 1);
  assert.equal(app.calls[0].url.endsWith('/subscribe'), true);
  assert.equal(JSON.parse(app.calls[0].options.body).source, 'website-download-android');
  assert.equal(app.navigations[0].endsWith('/android'), true);
});

test('empty Android email skips signup; failed signup still permits APK', async () => {
  for (const email of ['', 'test@example.invalid']) {
    const app = harness({fetcher: async () => { throw new Error('offline'); }});
    app.click();
    app.e.emailField.value = email;
    await app.e.emailForm.listeners.submit({preventDefault() {}});
    assert.equal(app.navigations.length, 1);
    assert.equal(app.navigations[0].endsWith('/android'), true);
    assert.equal(app.calls.length, email ? 1 : 0);
  }
});

test('invalid email stays optional, while Cancel and Escape never download', async () => {
  const app = harness();
  app.click();
  app.e.emailField.value = 'bad';
  await app.e.emailForm.listeners.submit({preventDefault() {}});
  assert.equal(app.e.emailModal.hidden, false);
  assert.equal(app.navigations.length, 0);
  app.cancel.listeners.click();
  assert.equal(app.e.emailModal.hidden, true);
  app.click();
  app.events.keydown({key: 'Escape', preventDefault() {}});
  assert.equal(app.e.emailModal.hidden, true);
  assert.equal(app.navigations.length, 0);
});

test('Windows retains its own prompt, metadata check and installer route', async () => {
  const app = harness();
  app.e.downloadButton.listeners.click();
  assert.match(app.e.emailModalPlatform.textContent, /Windows/);
  await app.e.skipEmail.listeners.click();
  assert.equal(app.calls.length, 1);
  assert.equal(app.calls[0].url.endsWith('/installer-info'), true);
  assert.equal(app.navigations[0].endsWith('/installer'), true);
});

test('late signup response cannot trigger a second or wrong-platform download after Skip', async () => {
  let finish;
  const app = harness({fetcher: () => new Promise(resolve => { finish = resolve; })});
  app.click();
  app.e.emailField.value = 'test@example.invalid';
  const pending = app.e.emailForm.listeners.submit({preventDefault() {}});
  await app.e.skipEmail.listeners.click();
  app.e.downloadButton.listeners.click();
  finish({ok: true, json: async () => ({ok: true})});
  await pending;
  assert.equal(app.navigations.length, 1);
  assert.equal(app.navigations[0].endsWith('/android'), true);
  assert.equal(app.e.emailModal.hidden, false);
  assert.match(app.e.emailModalPlatform.textContent, /Windows/);
});

test('modified Android links keep native new-tab behavior', () => {
  const app = harness();
  assert.equal(app.click(app.links[0], {ctrlKey: true}), false);
  assert.equal(app.e.emailModal.hidden, true);
});
