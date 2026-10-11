import test from 'node:test';
import assert from 'node:assert/strict';

await import('../static/js/i18n.js');
const { i18n } = globalThis;

test('English needs no catalog and is returned exactly', () => {
  i18n.configure('en', {});
  assert.equal(i18n.lang, 'en');
  assert.equal(i18n.t('Telemetry live'), 'Telemetry live');
  assert.equal(i18n.t('Lap {lap} of {total}', { lap: 3, total: 20 }), 'Lap 3 of 20');
  assert.equal(i18n.tp(1, '{n} lap', '{n} laps', { n: 1 }), '1 lap');
  assert.equal(i18n.tp(0, '{n} lap', '{n} laps', { n: 0 }), '0 laps');
  assert.equal(i18n.tp(2, '{n} lap', '{n} laps', { n: 2 }), '2 laps');
});

test('A missing placeholder value leaves the placeholder visible', () => {
  i18n.configure('en', {});
  assert.equal(i18n.t('Box {lap}', {}), 'Box {lap}');
  assert.equal(i18n.t('Box {lap}', { lap: null }), 'Box {lap}');
  assert.equal(i18n.t('Box {lap}', { lap: 0 }), 'Box 0');
});

test('A translation is used when present and English otherwise', () => {
  i18n.configure('fr', { 'Lap {lap} of {total}': 'Tour {lap} sur {total}', Empty: '' });
  assert.equal(i18n.t('Lap {lap} of {total}', { lap: 3, total: 20 }), 'Tour 3 sur 20');
  assert.equal(i18n.t('Not in the catalog'), 'Not in the catalog');
  assert.equal(i18n.t('Empty'), 'Empty');
  assert.equal(i18n.t('toString'), 'toString');
  assert.equal(i18n.t('__proto__'), '__proto__');
});

test('Plurals follow each language, not English', () => {
  const laps = { '{n} laps': { one: '{n} tour', other: '{n} tours' } };
  i18n.configure('fr', laps);
  assert.equal(i18n.tp(0, '{n} lap', '{n} laps', { n: 0 }), '0 tour');
  assert.equal(i18n.tp(1, '{n} lap', '{n} laps', { n: 1 }), '1 tour');
  assert.equal(i18n.tp(2, '{n} lap', '{n} laps', { n: 2 }), '2 tours');
  i18n.configure('de', { '{n} laps': { one: '{n} Runde', other: '{n} Runden' } });
  assert.equal(i18n.tp(0, '{n} lap', '{n} laps', { n: 0 }), '0 Runden');
  assert.equal(i18n.tp(1, '{n} lap', '{n} laps', { n: 1 }), '1 Runde');
  i18n.configure('es', { '{n} laps': { one: '{n} vuelta', other: '{n} vueltas' } });
  assert.equal(i18n.tp(1000000, '{n} lap', '{n} laps', { n: 1000000 }), '1000000 vueltas');
  assert.equal(i18n.t('{n} laps', { n: 5 }), '5 vueltas');
});

test('A data-i18n-html key matches the form tools/i18n_extract.py builds', () => {
  // <p data-i18n-html>Open the <b class="x">Driver &amp; Dashboard</b> tab<br> now <a href="/a?b=1&amp;c=&quot;2&quot;">here</a></p>
  const text = value => ({ nodeType: 3, nodeValue: value });
  const element = (tag, attributes, children) => ({
    nodeType: 1, tagName: tag.toUpperCase(), childNodes: children,
    attributes: Object.entries(attributes).map(([name, value]) => ({ name, value })),
  });
  const paragraph = element('p', { 'data-i18n-html': '' }, [
    text('Open the '),
    element('b', { class: 'x' }, [text('Driver & Dashboard')]),
    text(' tab'),
    element('br', {}, []),
    text(' now '),
    element('a', { href: '/a?b=1&c="2"' }, [text('here')]),
  ]);
  assert.equal(
    i18n.normalise(i18n.canonicalHtml(paragraph)),
    'Open the <b class="x">Driver &amp; Dashboard</b> tab<br> now <a href="/a?b=1&amp;c=&quot;2&quot;">here</a>',
  );
});
