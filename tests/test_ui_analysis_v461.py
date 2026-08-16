"""Pins for the blank-ANALYSIS-subpage bug and the stale-frontend trap.

2026-08-12, reported from a real 4.3.1→4.6.0 upgrade: clicking any ANALYSIS
sub-tab blanked the whole app. Two routers both owned the DOM — the inline
script showed the sub-view while connection.js's syncTabs collected the
sub-tab buttons through the loose '[role="tab"][data-page]' selector, took
the sub-view name as a page id, matched no <main>, and hid every page.
Separately, the asset cache-bust params were frozen at an old version and
nothing forced revalidation, so an upgraded install could run last week's
JavaScript against this week's HTML. These tests pin all three fixes.
"""

import re
from pathlib import Path

import pitwall

ROOT = Path(__file__).resolve().parents[1]
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
CONNECTION_JS = (ROOT / "static" / "js" / "connection.js").read_text(encoding="utf-8")
APP_PY = (ROOT / "src" / "pitwall" / "app.py").read_text(encoding="utf-8")


def test_asset_cache_bust_params_match_the_app_version():
    params = set(re.findall(r"/static/[^\"']+\?v=([0-9.]+)", INDEX))
    assert params, "static assets must carry ?v= cache-bust params"
    assert params == {pitwall.__version__}, (
        f"asset params {params} out of step with version {pitwall.__version__} — "
        "a stale browser cache would mix frontend versions after an upgrade"
    )
    # Every script and stylesheet must be versioned, none left bare.
    for asset in re.findall(r"(?:src|href)=\"(/static/[^\"]+)\"", INDEX):
        assert "?v=" in asset, f"unversioned asset reference: {asset}"


def test_synctabs_never_collects_analysis_subtab_buttons():
    sync = CONNECTION_JS[CONNECTION_JS.index("function syncTabs") :]
    sync = sync[: sync.index("\n}") + 2]
    assert ".tab[role=\"tab\"][data-page]" in sync, (
        "syncTabs must scope to top-level .tab buttons; the loose selector "
        "matched analysis sub-tabs and hid every page"
    )
    assert "ANALYSIS_SUBVIEWS" in sync, (
        "syncTabs must resolve analysis sub-view names to the analysis page"
    )
    init = CONNECTION_JS[CONNECTION_JS.index("function initializeTabs") :]
    init = init[: init.index("\n}") + 2]
    assert "querySelectorAll('.tab[role=\"tab\"][data-page]')" in init


def test_subtab_buttons_are_not_top_level_tabs():
    subnav = INDEX[INDEX.index("analysis-subnav") :]
    subnav = subnav[: subnav.index("</div>")]
    for button in re.findall(r"<button[^>]+>", subnav):
        assert 'class="field-tab' in button, button
        assert 'class="tab"' not in button, (
            "a sub-tab carrying the top-level tab class would be collected "
            "by every top-nav handler"
        )


def test_connection_js_subview_list_matches_the_inline_router():
    inline = re.search(r"ANALYSIS_VIEWS=\[([^\]]*)\]", INDEX)
    connection = re.search(r"ANALYSIS_SUBVIEWS = \[([^\]]*)\]", CONNECTION_JS)
    assert inline and connection
    parse = lambda raw: sorted(v.strip("'\" ") for v in raw.group(1).split(","))
    assert parse(inline) == parse(connection)


def test_render_setup_result_owns_every_name_it_uses():
    """renderSetupResult was extracted from generateSetup and kept using the
    outer function's ``profile`` parameter — every profile click then died on
    a ReferenceError before the first DOM write (reported live, 2026-08-12).
    The function must derive profile from the payload it is given."""
    body = INDEX[INDEX.index("function renderSetupResult") :]
    body = body[: body.index("\n")]
    assert "const profile=String(j.profile" in body
    assert "renderSetupResult(j)" in INDEX[INDEX.index("async function generateSetup") :][:2700]


def test_dashboard_responses_forbid_stale_caching():
    assert "_no_stale_frontend" in APP_PY
    middleware = APP_PY[APP_PY.index("async def _no_stale_frontend") :]
    middleware = middleware[: middleware.index("return response")]
    assert '"Cache-Control"' in middleware and "no-cache" in middleware
