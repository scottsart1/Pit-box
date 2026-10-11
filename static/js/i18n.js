/* Screen language runtime for the dashboard and the stream overlay.

   Pages are written in English. The server embeds the chosen language's
   catalog as JSON (#pitbox-i18n-catalog) and marks <html data-ui-language>;
   with no catalog every call returns the English text, so English renders
   exactly as written.

   A catalog maps English text to its translation. Placeholders are written
   {name}. A plural entry is an object keyed by plural category ("one",
   "other", and "many" where the language has it), looked up under the
   English plural ("other") form.

   Usage:
     i18n.t('Telemetry live')
     i18n.t('Lap {lap} of {total}', {lap, total})
     i18n.tp(count, '{count} lap', '{count} laps', {count})
     i18n.translateDom(root)   // static markup: text, placeholder, title,
                               // aria-label and alt; [data-i18n-html] whole

   This file has no import or export so it runs as a classic script in the
   page and can also be imported for its side effect in Node tests. */
(function (root) {
  'use strict';

  const FORMAT = /\{([A-Za-z0-9_]+)\}/g;
  const ATTRIBUTES = ['placeholder', 'title', 'aria-label', 'alt'];
  const SKIP_TAGS = new Set(['SCRIPT', 'STYLE', 'TEXTAREA', 'CODE', 'PRE', 'NOSCRIPT', 'TEMPLATE']);

  let lang = 'en';
  let catalog = Object.create(null);
  let plurals = null;

  function format(text, params) {
    if (!params) return text;
    return text.replace(FORMAT, (whole, name) =>
      Object.prototype.hasOwnProperty.call(params, name) && params[name] != null ? String(params[name]) : whole);
  }

  function lookup(key) {
    return Object.prototype.hasOwnProperty.call(catalog, key) ? catalog[key] : undefined;
  }

  function t(english, params) {
    const key = String(english);
    const entry = lookup(key);
    if (typeof entry === 'string' && entry) return format(entry, params);
    if (entry && typeof entry === 'object' && typeof entry.other === 'string') return format(entry.other, params);
    return format(key, params);
  }

  function category(count) {
    if (!plurals) {
      try { plurals = new Intl.PluralRules(lang); } catch { plurals = new Intl.PluralRules('en'); }
    }
    return plurals.select(Number(count));
  }

  /* English chooses `one` only for exactly 1, as the existing English
     strings do; other languages use their own plural rules. */
  function tp(count, one, other, params) {
    const entry = lookup(String(other));
    if (entry && typeof entry === 'object') {
      const form = entry[category(count)] ?? entry.other;
      if (typeof form === 'string' && form) return format(form, params);
    } else if (typeof entry === 'string' && entry) {
      return format(entry, params);
    }
    return format(Number(count) === 1 ? String(one) : String(other), params);
  }

  function normalise(text) {
    return String(text).replace(/\s+/g, ' ').trim();
  }

  function escapeText(text) {
    return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function escapeAttribute(text) {
    return text.replace(/&/g, '&amp;').replace(/"/g, '&quot;');
  }

  /* The key of a [data-i18n-html] element: its children with whitespace
     collapsed, tags lower-case, attributes in source order and double-quoted.
     tools/i18n_extract.py computes the same form from the HTML source. */
  function canonicalHtml(element) {
    let out = '';
    for (const node of element.childNodes) {
      if (node.nodeType === 3) out += escapeText(node.nodeValue);
      else if (node.nodeType === 1) {
        const tag = node.tagName.toLowerCase();
        const attributes = Array.from(node.attributes).map(a => ` ${a.name}="${escapeAttribute(a.value)}"`).join('');
        out += `<${tag}${attributes}>${canonicalHtml(node)}</${tag}>`;
      }
    }
    return out;
  }

  function skipped(element) {
    for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
      if (SKIP_TAGS.has(node.tagName) || node.getAttribute('translate') === 'no' || node.hasAttribute('data-i18n-skip')) return true;
    }
    return false;
  }

  function translateText(node) {
    const original = node.nodeValue;
    const key = normalise(original);
    if (!key || !/[A-Za-z]/.test(key)) return;
    const entry = lookup(key);
    const translated = typeof entry === 'string' ? entry : entry && typeof entry === 'object' ? entry.other : '';
    if (!translated || translated === key) return;
    const lead = original.match(/^\s*/)[0];
    const trail = original.match(/\s*$/)[0];
    node.nodeValue = lead + translated + trail;
  }

  function translateDom(rootNode) {
    if (lang === 'en' || !rootNode) return;
    const isDocument = rootNode.nodeType === 9;
    const scope = isDocument ? rootNode.documentElement : rootNode;
    const doc = isDocument ? rootNode : rootNode.ownerDocument;
    if (!scope || !doc) return;
    for (const element of scope.querySelectorAll('[data-i18n-html]')) {
      if (skipped(element)) continue;
      const key = normalise(canonicalHtml(element));
      const entry = lookup(key);
      if (typeof entry === 'string' && entry && entry !== key) element.innerHTML = entry;
      element.setAttribute('data-i18n-done', '');
    }
    const walker = doc.createTreeWalker(scope, 1 | 4);
    const texts = [];
    for (let node = walker.currentNode; node; node = walker.nextNode()) {
      if (node.nodeType === 3) texts.push(node);
      else if (node.nodeType === 1 && !skipped(node) && !node.closest('[data-i18n-done]')) {
        for (const name of ATTRIBUTES) {
          const value = node.getAttribute(name);
          if (value == null) continue;
          const translated = t(normalise(value));
          if (translated !== normalise(value)) node.setAttribute(name, translated);
        }
      }
    }
    for (const node of texts) {
      const parent = node.parentElement;
      if (!parent || skipped(parent) || parent.closest('[data-i18n-done]')) continue;
      translateText(node);
    }
  }

  function configure(language, entries) {
    lang = String(language || 'en');
    catalog = Object.assign(Object.create(null), entries || {});
    plurals = null;
  }

  function fromPage(doc) {
    const language = doc.documentElement.getAttribute('data-ui-language');
    const embedded = doc.getElementById('pitbox-i18n-catalog');
    if (!language || language === 'en' || !embedded) return;
    try {
      configure(language, JSON.parse(embedded.textContent || '{}'));
    } catch {
      configure('en', {});
    }
  }

  const api = {
    get lang() { return lang; },
    t,
    tp,
    translateDom,
    configure,
    canonicalHtml,
    normalise,
  };
  root.i18n = api;
  if (typeof document !== 'undefined' && document.documentElement) fromPage(document);
})(typeof globalThis !== 'undefined' ? globalThis : this);
