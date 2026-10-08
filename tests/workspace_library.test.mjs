import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const source = readFileSync(new URL('../static/js/workspaces.js', import.meta.url), 'utf8')
  .replace(/^export \{[^\n]+\};?\s*$/m, '');
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
};
const session = (id, session_type = 'Practice 1') => ({ id, session_type, track_name: 'Spa' });
const reply = (items, next_cursor = null) => ({ ok: true, text: async () => JSON.stringify({ items, next_cursor }) });

function harness() {
  const node = () => ({ value: '', textContent: '', children: [], dataset: {}, hidden: false, disabled: false,
    append(...items) { this.children.push(...items); },
    replaceChildren(...items) { this.children = items; }, addEventListener() {} });
  const nodes = new Map(['librarySearch', 'librarySessionType', 'libraryStarred', 'libraryStatus',
    'libraryRows', 'libraryCount', 'libraryLoadMore', 'reviewSessionSelect', 'fieldSessionSelect',
    'lapLabSessionSelect'].map(id => [id, node()]));
  const calls = [];
  const context = vm.createContext({ URLSearchParams, document: {
    getElementById: id => nodes.get(id), createElement: node,
  }, fetch: path => { const pending = deferred(); calls.push({ path, ...pending }); return pending.promise; } });
  vm.runInContext(source, context);
  const state = vm.runInContext('state', context);
  const ids = () => Array.from(state.sessions, item => item.id);
  const filter = value => { nodes.get('librarySessionType').value = value; };
  return { context, state, nodes, calls, ids, filter };
}

test('A delayed previous filter cannot replace the newest Library results or selectors', async () => {
  const h = harness(); h.filter('Practice'); const old = h.context.loadSessions();
  h.filter('Race'); const current = h.context.loadSessions();
  h.calls[1].resolve(reply([session('race', 'Race')], 'race-page-2')); await current;
  h.calls[0].resolve(reply([session('practice')], 'practice-page-2')); await old;
  assert.deepEqual(h.ids(), ['race']); assert.equal(h.state.nextCursor, 'race-page-2');
  assert.equal(h.nodes.get('reviewSessionSelect').children[1].value, 'race');
  assert.equal(h.nodes.get('libraryStatus').textContent, 'Loaded 1 saved session.');
});

test('A late previous request failure cannot clear newer successful Library results', async () => {
  const h = harness(); const old = h.context.loadSessions(); const current = h.context.loadSessions();
  h.calls[1].resolve(reply([session('current')])); await current;
  h.calls[0].reject(new Error('Old connection failed')); await old;
  assert.deepEqual(h.ids(), ['current']); assert.equal(h.nodes.get('libraryStatus').dataset.tone, 'success');
});

test('An old response cannot unlock pagination while the latest refresh is still pending', async () => {
  const h = harness(); const old = h.context.loadSessions(); const current = h.context.loadSessions();
  h.calls[0].resolve(reply([session('old')], 'old-page-2')); await old;
  assert.equal(h.nodes.get('libraryLoadMore').disabled, true);
  const more = h.context.loadSessions({ append: true }); assert.equal(h.calls.length, 2); await more;
  h.calls[1].resolve(reply([session('current')], 'current-page-2')); await current;
  assert.deepEqual(h.ids(), ['current']); assert.equal(h.state.nextCursor, 'current-page-2');
  assert.equal(h.nodes.get('libraryLoadMore').disabled, false);
});

test('A delayed old pagination response cannot append sessions from a previous filter', async () => {
  const h = harness(); h.filter('Practice'); const initial = h.context.loadSessions();
  h.calls[0].resolve(reply([session('practice')], 'p2')); await initial;
  const oldPage = h.context.loadSessions({ append: true });
  h.filter('Race'); const current = h.context.loadSessions();
  h.calls[2].resolve(reply([session('race', 'Race')])); await current;
  h.calls[1].resolve(reply([session('old-practice')])); await oldPage;
  assert.deepEqual(h.ids(), ['race']); assert.equal(h.state.nextCursor, null);
});

test('Changing a filter before Load more starts that filter from its first page', async () => {
  const h = harness(); h.filter('Practice'); const initial = h.context.loadSessions();
  h.calls[0].resolve(reply([session('practice')], 'practice-cursor')); await initial;
  h.filter('Race'); const changed = h.context.loadSessions({ append: true });
  const url = new URL(h.calls[1].path, 'http://localhost');
  assert.equal(url.searchParams.get('session_type'), 'Race'); assert.equal(url.searchParams.has('cursor'), false);
  h.calls[1].resolve(reply([session('race', 'Race')])); await changed;
  assert.deepEqual(h.ids(), ['race']);
});

test('Repeated Load more taps issue one request and pagination preserves unique rows', async () => {
  const h = harness(); const initial = h.context.loadSessions();
  h.calls[0].resolve(reply([session('one')], 'p2')); await initial;
  const more = h.context.loadSessions({ append: true });
  const duplicate = h.context.loadSessions({ append: true });
  assert.equal(h.nodes.get('libraryLoadMore').disabled, true);
  assert.equal(h.calls.length, 2);
  h.calls[1].resolve(reply([session('one'), session('two')])); await Promise.all([more, duplicate]);
  assert.deepEqual(h.ids(), ['one', 'two']); assert.equal(h.nodes.get('libraryLoadMore').hidden, true);
  assert.equal(h.nodes.get('libraryLoadMore').disabled, false);
});

test('Load more is ignored while a replacement page is pending and after the final page', async () => {
  const h = harness(); const initial = h.context.loadSessions();
  const during = h.context.loadSessions({ append: true }); assert.equal(h.calls.length, 1); await during;
  h.calls[0].resolve(reply([session('one')])); await initial;
  const after = h.context.loadSessions({ append: true }); assert.equal(h.calls.length, 1); await after;
});

test('A current pagination failure preserves rows and the cursor for a successful retry', async () => {
  const h = harness(); const initial = h.context.loadSessions();
  h.calls[0].resolve(reply([session('one')], 'p2')); await initial;
  const failed = h.context.loadSessions({ append: true });
  h.calls[1].reject(new Error('Connection failed')); await failed;
  assert.deepEqual(h.ids(), ['one']); assert.equal(h.state.nextCursor, 'p2');
  assert.equal(h.nodes.get('libraryLoadMore').disabled, false);
  assert.equal(h.nodes.get('libraryStatus').dataset.tone, 'error');
  const retry = h.context.loadSessions({ append: true }); h.calls[2].resolve(reply([session('two')])); await retry;
  assert.deepEqual(h.ids(), ['one', 'two']); assert.equal(h.nodes.get('libraryStatus').dataset.tone, 'success');
});

test('A current replacement failure clears old rows and a refresh recovers', async () => {
  const h = harness(); const initial = h.context.loadSessions();
  h.calls[0].resolve(reply([session('one')], 'p2')); await initial;
  const failed = h.context.loadSessions(); h.calls[1].reject(new Error('Connection failed')); await failed;
  assert.deepEqual(h.ids(), []); assert.equal(h.state.nextCursor, null);
  assert.equal(h.nodes.get('libraryLoadMore').disabled, false);
  const retry = h.context.loadSessions(); h.calls[2].resolve(reply([session('new')])); await retry;
  assert.deepEqual(h.ids(), ['new']);
});
