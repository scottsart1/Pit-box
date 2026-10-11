"""The screens in French, German and Spanish.

Catalogs map English text to its translation, keyed by the English text, so
these tests check three things: English pages are served exactly as written;
every string the screens translate has a translation in every language; and
no translation can break the page it lands in (placeholders, markup).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

from pitwall.settings_service import SETTINGS_SPEC, coerce, settings_view
from pitwall.ui_i18n import load_catalog, localize_page, render_page, resolve_language

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
sys.path.insert(0, str(ROOT / "tools"))

import i18n_extract  # noqa: E402

LANGUAGES = ("fr", "de", "es")
PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")
TAG = re.compile(r"<(/?)([a-z0-9]+)([^>]*)>")


@pytest.fixture(scope="module")
def extracted():
    return {name: i18n_extract.collect(name) for name in i18n_extract.CATALOGS}


def _catalog(catalog: str, lang: str) -> dict:
    directory = i18n_extract.CATALOGS[catalog][0]
    return json.loads((directory / f"{lang}.json").read_text(encoding="utf-8"))


# Serving ---------------------------------------------------------------------


@pytest.mark.parametrize("page", ("index.html", "overlay.html"))
def test_english_pages_are_served_exactly_as_written(page):
    written = (STATIC / page).read_text(encoding="utf-8")
    assert render_page(STATIC, page, None, "en") == written
    assert render_page(STATIC, page, "en", "fr") == written


def test_a_language_is_marked_on_the_page_and_its_catalog_embedded(tmp_path):
    (tmp_path / "i18n").mkdir()
    (tmp_path / "i18n" / "fr.json").write_text(
        json.dumps({"Connection": "Connexion", "Tricky": "</script><b>"}), encoding="utf-8"
    )
    (tmp_path / "page.html").write_text(
        '<!doctype html>\n<html lang="en">\n<head>\n<title>x</title></head><body></body></html>',
        encoding="utf-8",
    )
    html = render_page(tmp_path, "page.html", None, "fr")
    assert '<html lang="fr" data-ui-language="fr">' in html
    match = re.search(r'<script id="pitbox-i18n-catalog" type="application/json">(.*?)</script>', html, re.S)
    assert match, html
    assert "</script><b>" not in match.group(1)
    assert json.loads(match.group(1).replace("<\\/", "</")) == {
        "Connection": "Connexion", "Tricky": "</script><b>",
    }


def test_a_requested_language_wins_over_the_setting_and_unknown_codes_fall_back(tmp_path):
    (tmp_path / "i18n").mkdir()
    (tmp_path / "i18n" / "de.json").write_text("{}", encoding="utf-8")
    assert resolve_language(tmp_path, "de", "en") == "de"
    assert resolve_language(tmp_path, None, "de") == "de"
    assert resolve_language(tmp_path, "xx", "de") == "de"
    assert resolve_language(tmp_path, "../../etc/passwd", None) == "en"
    assert resolve_language(tmp_path, "fr", None) == "en"  # no catalog file
    assert resolve_language(tmp_path, "EN", "de") == "en"


def test_a_missing_or_broken_catalog_means_english(tmp_path):
    (tmp_path / "i18n").mkdir()
    (tmp_path / "i18n" / "es.json").write_text("{not json", encoding="utf-8")
    assert load_catalog(tmp_path, "es") == {}
    assert load_catalog(tmp_path, "fr") == {}
    page = '<html lang="en"><head></head></html>'
    assert localize_page(page, "en", {"a": "b"}) == page


# The setting -----------------------------------------------------------------


def test_screen_language_is_a_live_setting_that_names_each_language_in_itself():
    spec = SETTINGS_SPEC["ui_language"]
    assert spec["choices"] == ("en", "fr", "de", "es")
    assert spec["choice_labels"] == {
        "en": "English", "fr": "Français", "de": "Deutsch", "es": "Español",
    }
    assert not spec.get("restart")
    assert coerce("ui_language", "FR") == "fr"
    with pytest.raises(ValueError):
        coerce("ui_language", "it")


def test_the_settings_view_carries_choice_labels_only_where_defined():
    from pitwall.config import Settings

    view = {entry["name"]: entry for entry in settings_view(Settings(), {})}
    assert view["ui_language"]["value"] == "en"
    assert view["ui_language"]["choice_labels"]["de"] == "Deutsch"
    assert "choice_labels" not in view["radio_verbosity"]


# Catalogs --------------------------------------------------------------------


@pytest.mark.parametrize("catalog", tuple(i18n_extract.CATALOGS))
@pytest.mark.parametrize("lang", LANGUAGES)
def test_every_screen_string_has_a_translation(extracted, catalog, lang):
    keys, _dynamic = extracted[catalog]
    entries = _catalog(catalog, lang)
    missing = sorted(set(keys) - set(entries))
    stale = sorted(set(entries) - set(keys))
    assert not missing, f"{len(missing)} untranslated in {catalog}/{lang}, e.g. {missing[:10]}"
    assert not stale, f"{len(stale)} unused in {catalog}/{lang}, e.g. {stale[:10]}"


@pytest.mark.parametrize("catalog", tuple(i18n_extract.CATALOGS))
@pytest.mark.parametrize("lang", LANGUAGES)
def test_translations_keep_placeholders_markup_and_plural_forms(extracted, catalog, lang):
    keys, _dynamic = extracted[catalog]
    problems = []
    for english, translated in _catalog(catalog, lang).items():
        key = keys.get(english)
        if key is None:
            continue
        expected = set(PLACEHOLDER.findall(english))
        if key.plural:
            if not isinstance(translated, dict) or not isinstance(translated.get("other"), str):
                problems.append((english, "plural needs an object with 'other'"))
                continue
            allowed = expected | set(PLACEHOLDER.findall(key.one or ""))
            for form, text in translated.items():
                if form not in {"zero", "one", "two", "few", "many", "other"} or not text:
                    problems.append((english, f"bad plural form {form!r}"))
                elif not set(PLACEHOLDER.findall(text)) <= allowed:
                    problems.append((english, f"unknown placeholder in {form}"))
            if set(PLACEHOLDER.findall(translated["other"])) != expected:
                problems.append((english, "placeholders differ in 'other'"))
            continue
        if not isinstance(translated, str) or not translated.strip():
            problems.append((english, "empty translation"))
            continue
        if set(PLACEHOLDER.findall(translated)) != expected:
            problems.append((english, f"placeholders {sorted(PLACEHOLDER.findall(translated))}"))
        if key.html:
            def tags(text):
                return sorted((m.group(1), m.group(2), m.group(3).strip()) for m in TAG.finditer(text))
            if tags(translated) != tags(english):
                problems.append((english, "markup differs"))
        elif "<" in translated and "<" not in english:
            problems.append((english, "markup in a plain-text translation"))
        if translated != translated.strip() and english == english.strip():
            problems.append((english, "leading or trailing space"))
    assert not problems, problems[:15]


@pytest.mark.parametrize("catalog", tuple(i18n_extract.CATALOGS))
@pytest.mark.parametrize("lang", LANGUAGES)
def test_catalogs_are_utf8_without_mojibake(catalog, lang):
    directory = i18n_extract.CATALOGS[catalog][0]
    raw = (directory / f"{lang}.json").read_bytes()
    text = raw.decode("utf-8")
    for marker in ("Ã©", "Ã¨", "Ã¼", "Ã¶", "Ã±", "â€", "�"):
        assert marker not in text, marker


def test_server_keys_are_strings_the_server_actually_sends():
    keys = json.loads((STATIC / "i18n" / "server-keys.json").read_text(encoding="utf-8"))
    source = "\n".join(
        path.read_text(encoding="utf-8") for path in (ROOT / "src" / "pitwall").rglob("*.py")
    )
    missing = [key for key in keys if key not in source]
    assert not missing, missing


def test_the_driver_dashboard_runtime_is_the_app_runtime():
    app = (STATIC / "js" / "i18n.js").read_text(encoding="utf-8")
    dashboard = (STATIC / "driver-dashboard" / "i18n.mjs").read_text(encoding="utf-8")
    assert dashboard == app + "export const i18n = globalThis.i18n;\n"


@pytest.mark.parametrize("page", (STATIC / "index.html", STATIC / "overlay.html"))
def test_pages_load_the_runtime_before_any_script_uses_it(page):
    html = page.read_text(encoding="utf-8")
    runtime = html.index('<script src="/static/js/i18n.js')
    translate = html.index("<script>i18n.translateDom(document)</script>")
    assert runtime < html.index("</head>") < translate
    first_use = html.find("i18n.t(")
    assert first_use == -1 or first_use > runtime
