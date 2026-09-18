/* Real shipped HTML/scripts in a local DOM. No browser, network, keys or mic.
   jsdom does not implement native modal dialogs: the open/close attributes are
   shimmed here. Focus trapping, layout and native WebView need visual/device QA. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {JSDOM} = require('jsdom');
const root = path.resolve(__dirname, '..');
const html = fs.readFileSync(path.join(root, 'static/index.html'), 'utf8');
const script = name => fs.readFileSync(path.join(root, 'static/js', name), 'utf8');
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };

(async () => {
  const dom = new JSDOM(html, {url: 'http://127.0.0.1:8769/', runScripts: 'outside-only', pretendToBeVisual: true});
  const w = dom.window, calls = [], id = name => w.document.getElementById(name);
  w.HTMLDialogElement.prototype.showModal = function () { this.setAttribute('open', ''); };
  w.HTMLDialogElement.prototype.close = function () { this.removeAttribute('open'); };
  w.HTMLElement.prototype.scrollIntoView = function () {};
  let usage = {decided: false, enabled: false};
  let credential = {configured: false};
  const live = {connected: false, wake_enabled: false, ptt_mask: 0, wake_input_rms: 0, wake_phrase: 'mark'};
  w.setInterval = () => 0;
  w.fetch = async (url, options = {}) => {
    calls.push({url, options});
    if (url === '/api/v1/usage') {
      if (options.method === 'POST') usage = {decided: true, ...JSON.parse(options.body)};
      return {ok: true, json: async () => usage};
    }
    if (url === '/api/v1/credentials/openai') {
      if (options.method === 'PUT') credential = {configured: true};
      return {ok: true, json: async () => credential};
    }
    if (url === '/api/state') return {ok: true, json: async () => live};
    if (url === '/api/ptt/calibrate') return {ok: true, json: async () => ({ok: true})};
    if (url === '/api/wake/config') return {ok: true, json: async () => ({enabled: true})};
    if (url === '/api/v1/network/status') return {ok: true, json: async () => ({listener: {bind_host: '0.0.0.0', port: 20784, state: 'listening'}, recommendation: {console_destination_ipv4: '192.168.1.50'}})};
    if (url === '/api/v1/network/interfaces') return {ok: true, json: async () => ({})};
    if (url === '/api/v1/updates') return {ok: true, json: async () => ({enabled: true, current_version: '4.11.0', outcome: 'unsupported'})};
    throw new Error(`Unexpected network request ${url}`);
  };
  try {
    id('bootOverlay').classList.add('done');
    // Exercise the app's real workspace selectors, with external data loaders stubbed.
    w.eval("const $=id=>document.getElementById(id); const ANALYSIS_VIEWS=['library','session-review','lap-lab','field','review']; function loadTracks(){} function loadHistory(){} function loadAppSettings(){}\n" +
      ['selectPage', 'selectAnalysisView'].map(name => html.match(new RegExp(`^function ${name}\\([^\\n]+`, 'm'))[0]).join('\n'));
    w.document.querySelectorAll('.tab').forEach(button => { button.onclick = () => w.selectPage(button.dataset.page); });
    w.document.querySelectorAll('.analysis-subnav .field-tab').forEach(button => { button.onclick = () => w.selectAnalysisView(button.dataset.page); });
    w.selectPage('settings');
    w.eval(script('usage.js')); w.eval(script('updates.js')); w.eval(script('onboarding.js')); w.eval(script('app-tour.js')); await settle();
    assert.equal(id('onboardingDialog').open, false, 'privacy prompt remains independently actionable');
    id('usageNo').click(); await settle();
    assert.equal(usage.enabled, false);
    assert.equal(id('onboardingDialog').open, true);
    assert.equal(w.document.activeElement, id('onboardingTitle'));
    assert.equal(id('onboardingSettings').parentElement, id('settingsGroups').parentElement);
    assert.equal(id('settingsGroups').nextElementSibling, id('usageSettings'));
    assert.equal(id('usageSettings').nextElementSibling, id('updateSettings'));
    assert.equal(id('updateSettings').nextElementSibling, null);
    id('settingsGroups').innerHTML = '<section>Freshly loaded settings controls</section>';
    assert.equal(id('settingsGroups').nextElementSibling, id('usageSettings'), 'settings reload keeps footer cards at the bottom');
    assert.equal(id('onboardingKey').type, 'password');
    id('onboardingKey').value = 'test-only-not-a-real-key';
    id('onboardingKeyForm').dispatchEvent(new w.Event('submit', {cancelable: true})); await settle();
    assert.match(id('onboardingKeyStatus').textContent, /checked and saved/);
    assert.equal(id('onboardingKey').value, '');
    id('onboardingNext').click();
    await settle();
    assert.equal(id('onboardingStep0').hidden, true);
    assert.equal(id('onboardingStep1').hidden, false);
    assert.equal(id('onboardingUdpIp').textContent, '192.168.1.50');
    assert.equal(id('onboardingUdpPort').textContent, '20784');
    assert.equal(id('onboardingCalibrate').disabled, true);
    w.dispatchEvent(new w.CustomEvent('pitwall:state', {detail: {...live, connected: true}}));
    id('onboardingCalibrate').click(); await settle();
    assert.match(id('onboardingCalibrationResult').textContent, /not completed yet/);
    id('onboardingNext').click(); id('onboardingEnableMark').click(); await settle();
    assert.match(id('onboardingWakeResult').textContent, /radio enabled/);
    w.dispatchEvent(new w.CustomEvent('pitwall:state', {detail: {...live, wake_enabled: true, wake_input_rms: 60}}));
    assert.ok(id('onboardingMicMeter').value > 0);
    id('onboardingNext').click();
    assert.equal(id('onboardingStep3').hidden, false);
    assert.equal(id('appTour').hidden, true, 'tour requires an explicit Begin action');
    id('onboardingSkipStep').click();
    assert.equal(id('onboardingDialog').open, false);
    assert.equal(w.localStorage.getItem('pitwall.onboarding.v1'), 'finished');
    id('onboardingRestart').focus(); id('onboardingRestart').click(); await settle();
    id('onboardingKey').value = 'unsaved-key';
    const cancel = new w.Event('cancel', {cancelable: true});
    id('onboardingDialog').dispatchEvent(cancel);
    assert.equal(cancel.defaultPrevented, true);
    assert.equal(id('onboardingDialog').open, false);
    assert.equal(id('onboardingKey').value, '');
    assert.equal(w.document.activeElement, id('onboardingRestart'), 'explicitly captured focus is restored');
    assert.equal(usage.enabled, false, 'radio setup never changes usage consent');
    const writesBeforeTour = calls.filter(call => call.options.method).length;
    id('appTourBegin').focus(); id('appTourBegin').click();
    assert.equal(id('appTour').hidden, false);
    assert.equal(id('live').hidden, false);
    assert.equal(id('appTourBack').disabled, true);
    id('appTourNext').click(); id('appTourBack').click();
    assert.match(id('appTourProgress').textContent, /1 of 11/);
    const expectedPages = ['live', 'live', 'driver-dashboard', 'strategy', 'connection', 'connection', 'analysis', 'analysis', 'analysis', 'setup', 'settings'];
    expectedPages.forEach((page, index) => {
      assert.equal(id(page).hidden, false, `tour stop ${index + 1} opens ${page}`);
      assert.match(id('appTourProgress').textContent, new RegExp(`${index + 1} of 11$`));
      assert.ok(w.document.querySelector('.app-tour-highlight'));
      assert.ok(id('appTourText').textContent.trim().split(/\s+/).length <= 30, 'tour blurbs stay concise');
      id('appTourNext').click();
    });
    assert.equal(id('appTour').hidden, true);
    assert.equal(id('settings').hidden, false, 'return to the page where the tour was started');
    assert.equal(w.document.querySelector('.app-tour-highlight'), null);
    assert.equal(w.document.querySelector('.app').classList.contains('app-tour-active'), false);
    assert.equal(w.localStorage.getItem('pitwall.app-tour.v1'), 'finished');
    assert.equal(calls.filter(call => call.options.method).length, writesBeforeTour, 'tour navigation submits no action requests (read-only status refreshes are allowed)');
    id('appTourBegin').click(); id('appTourNext').click();
    w.dispatchEvent(new w.KeyboardEvent('keydown', {key: 'Escape', cancelable: true}));
    assert.equal(id('appTour').hidden, true);
    assert.equal(w.localStorage.getItem('pitwall.app-tour.v1'), 'skipped');
    id('onboardingRestart').click(); await settle();
    for (let i = 0; i < 3; i++) id('onboardingSkipStep').click();
    id('onboardingBeginTour').click();
    assert.equal(id('onboardingDialog').open, false);
    assert.equal(id('appTour').hidden, false);
    id('appTourSkip').click();
    assert.equal(id('appTour').hidden, true);
    assert.ok(!JSON.stringify(w.localStorage).includes('test-only'));
    assert.ok(!calls.some(call => call.url === '/api/ask'), 'never sends a paid radio request automatically');
    console.log('PASS: onboarding UDP details, independent usage choice, bottom settings cards, optional 11-stop tour, navigation/back/skip/Escape, key saving and radio fixtures; no real network or microphone.');
  } finally { await settle(); w.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
