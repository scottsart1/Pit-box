"""Serve the dashboard pages in the chosen screen language.

The pages are written in English. A translation catalog maps each English
string to its translation, keyed by the English text itself, so English
needs no catalog and is served exactly as written. For another language the
catalog is embedded in the page as JSON, where ``static/js/i18n.js`` reads
it before the first script runs; the runtime then translates the static
markup and every string the scripts pass through ``i18n.t``.

Only the screens are translated. Stored data, API values and the engineer's
radio stay English.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SUPPORTED_LANGUAGES = ("en", "fr", "de", "es")
CATALOG_DIRNAME = "i18n"
# A request may name any language that has a catalog file, which is also how
# a pseudo-language for layout testing is served. Codes are plain letters so
# a query value can never become a path.
_LANG_CODE = re.compile(r"^[a-z]{2,3}$")
_HTML_OPEN = '<html lang="en">'

_cache: dict[Path, tuple[float, dict[str, Any]]] = {}


def catalog_path(static_root: Path, lang: str) -> Path:
    return Path(static_root) / CATALOG_DIRNAME / f"{lang}.json"


def load_catalog(static_root: Path, lang: str) -> dict[str, Any]:
    """The catalog for ``lang``, or an empty one when it does not exist.

    Read as UTF-8 explicitly: Windows defaults to a legacy code page, which
    would garble every accented letter.
    """
    path = catalog_path(static_root, lang)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    cached = _cache.get(path)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        catalog = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(catalog, dict):
        return {}
    _cache[path] = (mtime, catalog)
    return catalog


def resolve_language(static_root: Path, requested: str | None, configured: str | None) -> str:
    """The language to serve: an explicit ``?lang=`` first, then the setting."""
    for candidate in (requested, configured):
        code = str(candidate or "").strip().lower()
        if not code or not _LANG_CODE.match(code):
            continue
        if code == "en" or catalog_path(static_root, code).is_file():
            return code
    return "en"


def localize_page(html: str, lang: str, catalog: dict[str, Any]) -> str:
    """Mark the page with its language and embed the catalog.

    English is returned unchanged, byte for byte.
    """
    if lang == "en" or _HTML_OPEN not in html:
        return html
    payload = json.dumps(catalog, ensure_ascii=False, separators=(",", ":"))
    # "</" inside the JSON would end the script element early.
    payload = payload.replace("</", "<\\/")
    html = html.replace(_HTML_OPEN, f'<html lang="{lang}" data-ui-language="{lang}">', 1)
    embedded = f'<script id="pitbox-i18n-catalog" type="application/json">{payload}</script>'
    return html.replace("<head>", "<head>\n" + embedded, 1)


def render_page(static_root: Path, name: str, requested: str | None, configured: str | None) -> str:
    html = (Path(static_root) / name).read_text(encoding="utf-8")
    lang = resolve_language(static_root, requested, configured)
    if lang == "en":
        return html
    return localize_page(html, lang, load_catalog(static_root, lang))
