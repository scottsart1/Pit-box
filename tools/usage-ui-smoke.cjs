/* Local DOM checks; no real browser, user data or network requests. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');
const source = fs.readFileSync(path.join(root, 'static/js/usage.js'), 'utf8');
const html = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
const settle = () => new Promise(resolve => setImmediate(resolve));

(async () => {
  const dom = new JSDOM(html, {url: 'http://127.0.0.1:8769/', runScripts: 'outside-only', pretendToBeVisual: true});
  const window = dom.window, calls = [];
  let state = {decided: false, enabled: false}, failSave = false;
  window.setInterval = () => 0;
  window.fetch = async (url, options = {}) => {
    calls.push({url, options});
    if (options.method === 'POST' && url === '/api/v1/usage') {
      if (failSave) return {ok: false, status: 503};
      state = {decided: true, ...JSON.parse(options.body)};
    }
    return {ok: true, json: async () => state};
  };
  window.eval(source);
  await settle();
  const id = name => window.document.getElementById(name);
  assert.equal(id('usagePrompt').hidden, false);
  assert.equal(id('usageToggle').checked, false);
  assert.equal(calls.filter(call => call.options.method === 'POST').length, 0);
  assert.equal(id('usageSettings').parentElement, id('settingsGroups').parentElement);
  id('usageNo').click();
  await settle();
  assert.equal(id('usagePrompt').hidden, true);
  assert.equal(state.enabled, false);
  id('usageToggle').click();
  await settle();
  assert.equal(state.enabled, true);
  assert.equal(id('usageToggle').checked, true);
  failSave = true;
  id('usageToggle').click();
  await settle();
  assert.equal(state.enabled, true);
  assert.equal(id('usageToggle').checked, true);
  assert.match(id('usageStatus').textContent, /not saved/);
  failSave = false;
  id('usageToggle').click();
  await settle();
  assert.equal(state.enabled, false);
  assert.equal(id('usageToggle').checked, false);
  assert.match(id('usageStatus').textContent, /Sharing is off/);
  dom.window.close();
  console.log('Usage UI: default off, explicit decline/enable/disable, backend persistence and failed-save recovery passed.');
})().catch(error => {console.error(error); process.exitCode = 1;});
