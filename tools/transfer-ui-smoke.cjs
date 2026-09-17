// DOM behavior checks. Requires jsdom (npm install --no-save jsdom).
// This does not replace visual QA in the Android WebView.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { JSDOM } = require('jsdom');
const root = path.resolve(__dirname, '..');
const dom = new JSDOM(fs.readFileSync(path.join(root, 'static/index.html'), 'utf8'), {
  url: 'http://127.0.0.1:8000/', runScripts: 'outside-only', pretendToBeVisual: true,
});
const { window } = dom;
const byId = id => window.document.getElementById(id);
const requests = [];
const status = { running: false, device_name: 'Tablet', peers: [], jobs: [] };
let unavailable = false;
const settle = async () => { for (let i = 0; i < 8; i++) await new Promise(resolve => setImmediate(resolve)); };
window.HTMLElement.prototype.scrollIntoView = () => {};
window.fetch = async (url, options = {}) => {
  if (unavailable) throw new window.TypeError('offline');
  const body = options.body ? JSON.parse(options.body) : null;
  requests.push({ url, body, method: options.method || 'GET' });
  let payload = status;
  if (url.endsWith('/start')) status.running = true;
  if (url.endsWith('/stop')) status.running = false;
  if (url.endsWith('/invite')) payload = { invitation: 'pitwall-pair://safe', expires_at: Date.now() / 1000 + 300, qr_svg: '<svg xmlns="http://www.w3.org/2000/svg"/>' };
  if (url.endsWith('/pair')) status.peers = [{ id: 'peer1', name: '<img src=x onerror=alert(1)>', endpoint: 'https://192.168.1.4:20778' }];
  if (url.endsWith('/sessions')) payload = { sessions: [
    { id: 'session1', display_name: 'Spa race', transferable: true, completeness: 'cataloged-detail' },
    { id: 'session2', display_name: 'Old race', transferable: true, completeness: 'summary_only' },
    { id: 'active', display_name: 'Still recording', transferable: false },
  ] };
  if (url.endsWith('/pull')) status.jobs = [{ id: 'job1', status: 'downloading', session_ids: body.session_ids, bytes_received: 200, total_bytes: 1000 }];
  return { ok: true, status: 200, json: async () => structuredClone(payload) };
};
const submit = id => byId(id).dispatchEvent(new window.Event('submit', { bubbles: true, cancelable: true }));

(async () => {
  try {
    window.eval(fs.readFileSync(path.join(root, 'static/js/transfers.js'), 'utf8').replace(/^export /gm, ''));
    window.document.dispatchEvent(new window.Event('DOMContentLoaded'));
    window.dispatchEvent(new window.CustomEvent('pitwall:pagechange', { detail: { page: 'connection' } }));
    await settle();
    assert.equal(byId('transferBadge').textContent, 'Off');
    byId('recommendedIpv4').textContent = '192.168.1.20';
    submit('transferEnableForm'); await settle();
    assert.equal(requests.find(r => r.url.endsWith('/start')).body.advertise_host, '192.168.1.20');
    assert.equal(byId('transferWorkspace').hidden, false);
    byId('transferInvite').click(); await settle();
    assert.equal(byId('transferInvitation').value, 'pitwall-pair://safe');
    assert.match(byId('transferInvitationQr').src, /^data:image\/svg\+xml/);
    assert(window.PitWallTransfers.acceptInvitation('pitwall-pair://incoming'));
    assert.equal(byId('transferPairCode').value, 'pitwall-pair://incoming');
    submit('transferPairForm'); await settle();
    assert.equal(byId('transferPeers').querySelectorAll('img').length, 0, 'peer names must remain text');
    [...byId('transferPeers').querySelectorAll('button')].find(b => b.textContent === 'Browse sessions').click();
    await settle();
    assert.equal(byId('transferSessions').querySelectorAll('input:disabled').length, 1);
    byId('transferSelectAll').checked = true;
    byId('transferSelectAll').dispatchEvent(new window.Event('change'));
    byId('transferPull').click(); await settle();
    assert.deepEqual(requests.find(r => r.url.endsWith('/pull')).body.session_ids, ['session1', 'session2']);
    assert.equal(byId('transferJobs').querySelector('progress').value, 200);
    assert.equal(byId('transferDisable').disabled, false, 'an interrupted copy must be stoppable');
    status.jobs[0] = { ...status.jobs[0], status: 'conflict', result: { conflicts: [{ reason: 'Recording differs' }], preserved_path: '/history/conflict.pitbox' } };
    byId('transferRefresh').click(); await settle();
    assert.match(byId('transferJobs').textContent, /History needs review/);
    assert.equal(byId('transferJobs').querySelector('progress'), null);
    assert(!byId('transferJobs').textContent.includes('History copied'));
    let importedEvents = 0;
    window.addEventListener('pitwall:historyimported', () => importedEvents++);
    const refresh = async () => { byId('transferRefresh').click(); await settle(); };
    status.exports = [{ id: 'export1', peer_id: 'peer1', status: 'preparing', session_ids: ['session1'], progress: { phase: 'packing', done: 42, total: 100, unit: 'files' } }];
    await refresh();
    let outgoing = byId('transferJobs').querySelector('[data-direction="outgoing"]');
    assert.match(outgoing.textContent, /Sending to <img/);
    assert.equal(outgoing.querySelector('img'), null);
    assert.match(outgoing.textContent, /42% of this stage/);
    assert.match(outgoing.textContent, /42 of 100 file entries/);
    assert.equal(outgoing.querySelector('progress').value, 42);
    assert.equal(byId('transferPull').disabled, false, 'outgoing export must not block a separate incoming selection');
    status.jobs = [{ id: 'job2', peer_id: 'peer1', status: 'preparing', session_ids: ['session1'] }];
    await refresh();
    const incoming = () => byId('transferJobs').querySelector('[data-direction="incoming"]');
    assert(!incoming().querySelector('progress').hasAttribute('value'), 'unknown totals are indeterminate, including older peers');
    assert.match(incoming().textContent, /Preparing on the sending device/);
    unavailable = true; await refresh();
    assert.equal(incoming().querySelector('progress'), null, 'do not animate unknown work while disconnected');
    assert.match(incoming().textContent, /last known progress/);
    unavailable = false; await refresh();
    assert(incoming().querySelector('progress'));
    assert(!incoming().textContent.includes('last known progress'));
    status.jobs[0] = { ...status.jobs[0], status: 'importing', progress: { phase: 'importing_rows', done: 30, total: 40, unit: 'rows' } };
    status.exports[0] = { ...status.exports[0], status: 'sent', bytes_sent: 1000, total_bytes: 1000 };
    await refresh();
    assert.equal(incoming().querySelector('progress').value, 30);
    assert.match(incoming().textContent, /75% of this stage/);
    outgoing = byId('transferJobs').querySelector('[data-direction="outgoing"]');
    assert.match(outgoing.textContent, /receiving device still needs to verify and import/);
    assert(!outgoing.textContent.includes('History copied'));
    assert.equal(importedEvents, 0);
    assert.equal(outgoing.querySelector('button'), null, 'sender must not offer local import/retry controls');
    status.jobs[0] = { ...status.jobs[0], status: 'completed', result: { imported_sessions: 1 } };
    await refresh(); await refresh();
    assert.equal(importedEvents, 1);
    assert.equal(incoming().querySelector('progress'), null);
    console.log('PASS: pairing, selection, safe names, both directions, measured stages, old peers, disconnect/reconnect, sent versus imported, stop and conflict states.');
  } finally { window.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
