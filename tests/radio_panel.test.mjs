import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
// The radio labels go through the screen-language runtime, a page global.
import '../static/js/i18n.js';

/* Live frames carry the newest 40 radio entries plus radio_seq, which moves on
   every new entry (src/pitwall/state.py). These build frames the same way. */
function frames(count, cap = 40) {
  const log = [];
  const out = [];
  for (let n = 1; n <= count; n += 1) {
    log.push({ role: n % 2 ? 'driver' : 'engineer', text: `message ${n}` });
    out.push({ radio_log: log.slice(-cap), radio_seq: n });
  }
  return out;
}
const lines = (node) => node.textContent.split('\n\n');
const count = (node, line) => lines(node).filter((value) => value === line).length;

/* ---- STRATEGY panel (static/js/strategy.js) ------------------------------- */

const strategySource = fs.readFileSync(new URL('../static/js/strategy.js', import.meta.url), 'utf8');
let sequence = 0;
async function strategyFixture() {
  const strategy = await import('data:text/javascript;base64,' + Buffer.from(strategySource + `\n// ${sequence++}`).toString('base64'));
  const nodes = new Map(['stratRadio', 'stratAsk', 'stratPlanStatus'].map((id) => [id, { textContent: '', value: '', scrollTop: 0, scrollHeight: 0, dataset: {} }]));
  globalThis.document = { getElementById: (id) => nodes.get(id) };
  const ask = async (question, reply) => {
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ reply }) });
    nodes.get('stratAsk').value = question;
    await strategy.sendAsk();
  };
  return { radio: nodes.get('stratRadio'), render: strategy.renderRadio, ask };
}

test('the strategy radio keeps following the conversation once a frame holds 40 entries', async () => {
  const { radio, render } = await strategyFixture();
  for (const frame of frames(45)) render(frame);
  assert.equal(lines(radio).length, 40);
  assert.equal(lines(radio).at(-1), 'DRIVER: message 45');
  render(frames(46).at(-1));
  assert.equal(lines(radio).at(-1), 'ENGINEER: message 46');
});

test('a typed strategy question shows its answer once, whichever arrives first', async () => {
  const { radio, render, ask } = await strategyFixture();
  const history = frames(44);
  for (const frame of history) render(frame);
  const question = { role: 'driver', text: 'gap to Norris?' };
  const answer = { role: 'engineer', text: 'Norris is 1.2 seconds ahead.' };
  const log = history.at(-1).radio_log;

  // The answer comes back before any frame carrying it.
  await ask(question.text, answer.text);
  assert.equal(lines(radio).at(-1), `ENGINEER: ${answer.text}`);
  render({ radio_log: [...log, question].slice(-40), radio_seq: 45 });
  render({ radio_log: [...log, question, answer].slice(-40), radio_seq: 46 });
  assert.equal(count(radio, `ENGINEER: ${answer.text}`), 1);
  assert.equal(lines(radio).at(-1), `ENGINEER: ${answer.text}`);

  // The frame carrying the answer is drawn before the request resolves.
  const second = { role: 'engineer', text: 'Box this lap for hards.' };
  const after = [...log, question, answer, { role: 'driver', text: 'box?' }, second].slice(-40);
  render({ radio_log: after, radio_seq: 48 });
  await ask('box?', second.text);
  assert.equal(count(radio, `ENGINEER: ${second.text}`), 1);
  assert.equal(lines(radio).length, 40);
});

/* ---- DRIVE panel (static/index.html) -------------------------------------- */

const html = fs.readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
function driveFixture() {
  const start = html.indexOf('/* DRIVE radio panel.');
  const end = html.indexOf('/* End DRIVE radio panel. */', start);
  assert.ok(start > 0 && end > start, 'the DRIVE radio panel code is delimited');
  const handlerStart = html.indexOf("$('askBtn').onclick=");
  const handlerEnd = html.indexOf(";$('q').onkeydown=", handlerStart);
  assert.ok(handlerStart > 0 && handlerEnd > handlerStart, 'the DRIVE ask handler is present');
  const nodes = new Map(['radio', 'q', 'askBtn'].map((id) => [id, { textContent: '', value: '', scrollTop: 0, scrollHeight: 0 }]));
  const context = vm.createContext({ $: (id) => nodes.get(id), i18n: globalThis.i18n, JSON, String, alert: (message) => { throw new Error(`alert: ${message}`); } });
  vm.runInContext(html.slice(start, end), context);
  vm.runInContext(html.slice(handlerStart, handlerEnd), context);
  const ask = async (question, reply) => {
    context.fetch = async () => ({ ok: true, json: async () => ({ reply }) });
    nodes.get('q').value = question;
    await nodes.get('askBtn').onclick();
  };
  return { radio: nodes.get('radio'), render: (s) => context.renderRadioLog(s), ask };
}

test('the DRIVE radio keeps following the conversation once a frame holds 40 entries', () => {
  const { radio, render } = driveFixture();
  for (const frame of frames(45)) render(frame);
  assert.equal(lines(radio).length, 40);
  assert.equal(lines(radio).at(-1), 'DRIVER: message 45');
  render(frames(46).at(-1));
  assert.equal(lines(radio).at(-1), 'ENGINEER: message 46');
});

test('a typed DRIVE question shows its answer once, whichever arrives first', async () => {
  const { radio, render, ask } = driveFixture();
  const history = frames(44);
  for (const frame of history) render(frame);
  const question = { role: 'driver', text: "what's the gap to Norris?" };
  const answer = { role: 'engineer', text: 'Norris is 1.2 seconds ahead.' };
  const log = history.at(-1).radio_log;

  await ask(question.text, answer.text);
  assert.equal(lines(radio).at(-1), `ENGINEER: ${answer.text}`);
  render({ radio_log: [...log, question, answer].slice(-40), radio_seq: 46 });
  assert.equal(count(radio, `ENGINEER: ${answer.text}`), 1);
  assert.equal(lines(radio).at(-1), `ENGINEER: ${answer.text}`);

  const second = { role: 'engineer', text: 'Box this lap for hards.' };
  render({ radio_log: [...log, question, answer, { role: 'driver', text: 'box?' }, second].slice(-40), radio_seq: 48 });
  await ask('box?', second.text);
  assert.equal(count(radio, `ENGINEER: ${second.text}`), 1);
  assert.equal(lines(radio).length, 40);
});

test('an empty DRIVE answer adds nothing to the panel', async () => {
  const { radio, render, ask } = driveFixture();
  render({ radio_log: [{ role: 'engineer', text: 'Radio check.' }], radio_seq: 1 });
  await ask('anything?', '');
  assert.equal(radio.textContent, 'ENGINEER: Radio check.');
});
