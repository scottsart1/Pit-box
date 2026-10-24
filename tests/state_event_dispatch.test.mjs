import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const html = fs.readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const DISPATCH = "dispatchEvent(new CustomEvent('pitwall:state'";

test('the live state event is dispatched once per frame, after the page has rendered', () => {
  assert.equal(html.split(DISPATCH).length - 1, 1);
  const wrapper = html.indexOf('const renderLegacy=render;render=function(s){');
  assert.ok(wrapper > 0);
  assert.ok(html.indexOf(DISPATCH) > wrapper, 'the one dispatch belongs to the wrapper that runs every render stage first');
});

test('per-frame lists only write innerHTML when the markup changed', () => {
  const start = html.indexOf('const setHTML=');
  const end = html.indexOf(';', html.indexOf('el.innerHTML=html}}', start)) + 1;
  assert.ok(start > 0 && end > start);
  const context = vm.createContext({});
  vm.runInContext(html.slice(start, end), context);
  const setHTML = vm.runInContext('setHTML', context);
  let writes = 0;
  const element = { set innerHTML(value) { writes++; this.markup = value; }, get innerHTML() { return this.markup; } };
  setHTML(element, '<b>1</b>');
  setHTML(element, '<b>1</b>');
  setHTML(element, '<b>2</b>');
  assert.equal(writes, 2);
  assert.equal(element.markup, '<b>2</b>');
  for (const id of ['timing', 'strategyPlans', 'currentSetup', 'damageRow']) {
    assert.ok(html.includes(`setHTML($('${id}')`), `${id} goes through setHTML`);
    assert.ok(!html.includes(`$('${id}').innerHTML=`), `${id} has no direct per-frame write left`);
  }
});
