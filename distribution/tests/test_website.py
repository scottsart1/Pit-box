"""The site must not go live with placeholders, dead images, or false claims.

The claim tests are the point of this file. Marketing copy is the one place in
the project where a sentence can quietly become untrue — a feature gets cut, a
platform slips — and nothing fails. These pin the statements that would be
dishonest rather than merely stale.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

DIST = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(DIST.parent))

from distribution.website import build_site  # noqa: E402

INDEX = (DIST / "website" / "index.html").read_text(encoding="utf-8")
GUIDE = (DIST / "website" / "guide.html").read_text(encoding="utf-8")
EULA = (DIST / "website" / "eula.html").read_text(encoding="utf-8")
DIAGNOSTICS = (DIST / "website" / "diagnostics.html").read_text(encoding="utf-8")
DOWNLOAD_JS = (DIST / "website" / "download.js").read_text(encoding="utf-8")
REVIEWS_JS = (DIST / "website" / "reviews.js").read_text(encoding="utf-8")

REAL_ENDPOINT = "https://activation.pitwall.app"

# download.js now carries the deployed Worker's URL, so the placeholder has to
# be reintroduced deliberately to test that the guard still catches it. Built
# by substitution rather than hardcoded so it cannot drift from the real file.
PLACEHOLDER_JS = re.sub(
    r'const ACTIVATION_API = "[^"]+";',
    'const ACTIVATION_API = "https://activation.example.invalid";',
    DOWNLOAD_JS,
)


def _stage(tmp_path, monkeypatch, *, index=None, guide=None, eula=None, script=None,
           diagnostics=None):
    """A site directory with every scanned file present and filled in.

    Each test then breaks exactly one thing, so a failure names its own cause.
    """
    site = tmp_path / "website"
    site.mkdir(exist_ok=True)
    (site / "index.html").write_text(index if index is not None else INDEX, encoding="utf-8")
    (site / "guide.html").write_text(guide if guide is not None else GUIDE, encoding="utf-8")
    (site / "diagnostics.html").write_text(
        diagnostics if diagnostics is not None else DIAGNOSTICS, encoding="utf-8"
    )
    (site / "eula.html").write_text(eula if eula is not None else EULA, encoding="utf-8")
    (site / "download.js").write_text(
        script if script is not None else DOWNLOAD_JS, encoding="utf-8"
    )
    (site / "reviews.js").write_text(REVIEWS_JS, encoding="utf-8")
    monkeypatch.setattr(build_site, "SITE_DIR", site)
    return site


# ---------------------------------------------------------------------------
# Publishing safety
# ---------------------------------------------------------------------------


def test_the_site_is_blocked_if_the_endpoint_regresses(tmp_path, monkeypatch):
    # The Worker is deployed and download.js points at it. Reverting to a
    # placeholder would ship a Download button that fails with a network error
    # for every buyer, so the guard must still refuse to publish.
    _stage(tmp_path, monkeypatch, script=PLACEHOLDER_JS)
    result = build_site.check()
    assert not result.ok
    assert any("example.invalid" in problem for problem in result.problems)


def test_the_committed_site_points_at_a_real_endpoint():
    endpoint = re.search(r'const ACTIVATION_API = "([^"]+)";', DOWNLOAD_JS).group(1)
    assert endpoint.startswith("https://"), endpoint
    assert "example" not in endpoint and ".invalid" not in endpoint, endpoint


def test_the_site_passes_once_the_endpoint_is_set(tmp_path, monkeypatch):
    _stage(tmp_path, monkeypatch)
    result = build_site.check()
    assert result.ok, result.report()


def test_every_registered_placeholder_names_the_fix():
    for token, fix in build_site.PLACEHOLDERS.items():
        assert fix.endswith("."), f"{token} has no actionable fix message"
        assert len(fix) > 20


@pytest.mark.parametrize(
    "sneaked",
    [
        "<p>Pay @YOUR-HANDLE today</p>",
        "<p>Email me at hello@example.com</p>",
        "<p>Governed by the laws of [SOME STATE]</p>",
        "<p>TODO: write this bit</p>",
    ],
)
def test_a_newly_introduced_placeholder_is_caught_by_shape(tmp_path, monkeypatch, sneaked):
    # The registered-token list only catches placeholders someone remembered to
    # register. This is the net under it.
    _stage(tmp_path, monkeypatch, index=INDEX + sneaked)

    result = build_site.check()
    assert not result.ok, f"{sneaked} was not caught"


def test_comments_discussing_placeholders_do_not_trip_the_check(tmp_path, monkeypatch):
    # A build note may legitimately mention YOUR-EMAIL@example.com to explain
    # what was replaced. Only the visible page is checked; a check that cried
    # wolf on its own documentation would be turned off within a week.
    note = "<!-- was: YOUR-EMAIL@example.com, see [OLD NOTES], TODO tidy -->"
    _stage(tmp_path, monkeypatch, index=note + INDEX)

    assert build_site.check().ok


def test_the_real_contact_details_are_in_place():
    # These were the other two placeholders; they must not regress.
    assert "@scott-v-sv" in INDEX
    assert "vale.scott00@gmail.com" in INDEX
    assert "vale.scott00@gmail.com" in EULA
    assert "example.com" not in INDEX


def test_the_partner_link_is_gone():
    # The racewithme.net header link was removed at the owner's request; a
    # stray copy in one page's header would put it straight back on the site.
    for page in (INDEX, GUIDE, EULA, DIAGNOSTICS):
        assert "racewithme" not in page
        assert "site-promo" not in page


def test_the_download_is_free_and_the_coffee_is_optional():
    # 4.9 dropped the price. A leftover "$5" anywhere would be a lie on a page
    # that says "free", and the coffee has to read as a thank-you, not a fee.
    for page in (INDEX, GUIDE, EULA, DIAGNOSTICS):
        assert not re.search(r"\$\s?\d", page)
    assert "Buy me a coffee" in INDEX
    assert "https://paypal.me/sarthakvij298" in INDEX
    assert "not a purchase" in INDEX
    assert "purchase email" not in INDEX and "purchase email" not in GUIDE


def test_the_download_button_needs_no_code():
    # The Download button navigates straight to the public installer route.
    # The code-gated /download endpoint is never called by the site now.
    assert 'id="downloadButton"' in INDEX
    assert 'id="downloadCode"' not in INDEX
    assert "/installer" in DOWNLOAD_JS
    assert "/download`" not in DOWNLOAD_JS and '"/download"' not in DOWNLOAD_JS


def test_the_bridge_code_panel_is_hidden_until_the_worker_asks_for_it():
    # Between the site going free and the free installer being uploaded, the
    # installer online still asks for an activation code on first start. The
    # page carries a panel for the shared code, hidden by default; only the
    # Worker's /installer-info answer reveals it, and the release script turns
    # that answer off the moment a free-edition build is uploaded.
    panel = re.search(r'<div id="codePanel"[^>]*>', INDEX).group(0)
    assert "hidden" in panel
    assert 'id="freeCode"' in INDEX
    assert "/installer-info" in DOWNLOAD_JS
    assert "needs_code" in DOWNLOAD_JS
    # The panel explains itself, and the guide covers the window it leads to.
    assert "built before Your Pit Box went free" in INDEX
    assert "Activate Your Pit Box" in GUIDE and "shared code" in GUIDE


def test_the_email_prompt_is_optional_and_says_what_it_is_for():
    # The prompt collects an address for release news and nothing else. The
    # page and the licence terms must both say so, and the prompt must have a
    # skip that still downloads.
    assert 'id="emailModal"' in INDEX
    assert 'id="skipEmail"' in INDEX
    assert "optional" in INDEX
    assert "new version" in INDEX
    assert "/subscribe" in DOWNLOAD_JS
    assert "skip it" in EULA and "new version" in EULA


def test_reviews_can_be_posted_and_are_read_before_they_appear():
    # Anyone can leave a review from the page; nothing is shown until the
    # owner has read it, and the page says so. The script renders review
    # text as text only, so a review can never inject markup into the site.
    assert 'id="reviews"' in INDEX and 'href="#reviews"' in INDEX
    assert 'id="reviewForm"' in INDEX and 'id="reviewList"' in INDEX
    assert 'name="rating"' in INDEX and 'name="body"' in INDEX
    assert "read every review" in INDEX
    assert 'src="reviews.js"' in INDEX
    assert "reviews.js" in build_site.ASSETS and "reviews.js" in build_site.SCANNED
    assert "/reviews" in REVIEWS_JS
    assert "innerHTML" not in REVIEWS_JS and "insertAdjacentHTML" not in REVIEWS_JS
    # The honeypot is present and hidden from people.
    assert 'name="website"' in INDEX and 'class="review-trap" aria-hidden="true"' in INDEX
    # The licence terms say what happens to a review and its optional email.
    assert "leave a review" in EULA and "not shown" in EULA
    # Both scripts are classic scripts on one page and share a global scope;
    # a top-level `status` or `form` in reviews.js would collide with
    # download.js and stop the file from running at all.
    assert not re.search(r"^const (status|form|button) ", REVIEWS_JS, re.M)


def test_the_eula_sections_are_numbered_contiguously():
    # A section was removed; the numbering must not have gaps or duplicates.
    numbers = [int(n) for n in re.findall(r"<h2>(\d+)\.", EULA)]
    assert numbers == list(range(1, len(numbers) + 1)), numbers


def test_the_javascript_is_scanned_for_placeholders_too(tmp_path, monkeypatch):
    # The endpoint lives in download.js, not in the HTML. Scanning only the
    # pages would ship a Download button that silently does nothing.
    _stage(tmp_path, monkeypatch, script=PLACEHOLDER_JS)
    result = build_site.check()
    assert not result.ok
    assert any("example.invalid" in problem for problem in result.problems)


def test_the_demo_video_is_referenced_and_present():
    # A missing video is a black rectangle where the main pitch should be.
    assert 'src="assets/pitwall-demo.mp4"' in INDEX
    assert "pitwall-demo.mp4" in build_site._referenced_assets()
    assert (build_site.ASSET_DIR / "pitwall-demo.mp4").exists()


def test_the_captioned_promo_is_referenced_and_present():
    # The short cut is the one most people will actually watch through.
    assert 'src="assets/pitwall-promo-45s.mp4"' in INDEX
    assert "pitwall-promo-45s.mp4" in build_site._referenced_assets()
    assert (build_site.ASSET_DIR / "pitwall-promo-45s.mp4").exists()


def _mp4_seconds(path: Path) -> float:
    """Duration from the MP4 movie header, without a media library.

    `mvhd` sits inside `moov` and carries a timescale and a duration in that
    scale. Boxes are length-prefixed, so the header can be found by walking
    them; version 1 widens both fields to 64 bits.
    """
    data = path.read_bytes()
    index = data.find(b"mvhd")
    assert index != -1, f"{path.name} has no mvhd box"
    version = data[index + 4]
    if version == 1:
        timescale = int.from_bytes(data[index + 24:index + 28], "big")
        duration = int.from_bytes(data[index + 28:index + 36], "big")
    else:
        timescale = int.from_bytes(data[index + 16:index + 20], "big")
        duration = int.from_bytes(data[index + 20:index + 24], "big")
    assert timescale, f"{path.name} declares no timescale"
    return duration / timescale


def test_the_captioned_promo_stays_under_the_length_it_claims():
    # The page calls it a forty-second tour, and short is the entire point of
    # cutting it. A re-record that runs long makes the copy a lie.
    seconds = _mp4_seconds(build_site.ASSET_DIR / "pitwall-promo-45s.mp4")
    assert 20.0 < seconds <= 45.0, f"the promo cut is {seconds:.1f}s"


def test_the_setup_guide_is_published_and_linked():
    assert "guide.html" in build_site.PAGES
    assert 'href="guide.html"' in INDEX
    assert "illustrated setup guide" in INDEX


def test_the_setup_guide_covers_the_customer_journey():
    for required in (
        "Download for Windows",
        "API billing is separate",
        "Create new secret key",
        "Welcome to Your Pit Box",
        "Test saved key",
        "All configured providers ready",
        "UDP IP Address",
        "20777",
        "format <strong>2026</strong>",
        "Receiving telemetry",
        "Mark, what is the gap ahead?",
    ):
        assert required in GUIDE
    assert "Your Pit Box runs beside the game, not inside it" in GUIDE


def _executable_source(page: str) -> str:
    """The page with commentary removed.

    The diagnostics page explains in a comment *why* it must never call
    /activate, and that sentence would otherwise trip the very check the
    comment describes. Only what actually runs is scanned, for the same
    reason build_site.check() strips comments before hunting placeholders.
    """
    without_html_comments = re.sub(r"<!--.*?-->", "", page, flags=re.DOTALL)
    return re.sub(r"^\s*//.*$", "", without_html_comments, flags=re.MULTILINE)


def test_the_diagnostics_page_never_claims_a_code():
    # The safety property this page exists under. /activate atomically claims a
    # code and binds it to the caller's device hash; a browser cannot produce
    # the buyer's real PC hash, so "testing activation" here would permanently
    # bind a paid code to a value that PC can never present again. There is no
    # un-claim endpoint, so this must never regress into a convenience feature.
    runnable = _executable_source(DIAGNOSTICS)
    assert "/activate" not in runnable
    # Only the two endpoints that cannot mutate anything are permitted.
    called = set(re.findall(r'ACTIVATION_API \+ "(/[a-z]+)"', runnable))
    assert called == {"/health", "/download"}, called


def test_the_diagnostics_page_is_published_and_linked():
    assert "diagnostics.html" in build_site.PAGES
    assert 'href="diagnostics.html"' in INDEX
    assert 'href="diagnostics.html"' in GUIDE


def test_the_diagnostics_page_says_it_cannot_check_claim_state():
    # Overstating what the checks prove would send buyers away believing their
    # code is fine when the claim step is exactly what failed.
    assert "cannot tell" in DIAGNOSTICS


def test_the_setup_guide_links_to_official_openai_account_pages():
    assert "https://platform.openai.com/api-keys" in GUIDE
    assert "https://platform.openai.com/settings/organization/billing/overview" in GUIDE
    assert "ChatGPT Plus, Pro, or Business does not include API usage" in GUIDE


def test_guide_examples_do_not_expose_reusable_secrets():
    assert not re.search(r"sk-[A-Za-z0-9_-]{16,}", GUIDE)
    code_like = re.findall(
        r"PITW-[0-9A-HJKMNP-TV-Z]{5}(?:-[0-9A-HJKMNP-TV-Z]{5}){2}", GUIDE
    )
    assert set(code_like) <= {"PITW-XXXXX-XXXXX-XXXXX"}


def test_the_video_poster_is_checked_like_any_other_image():
    # A poster is referenced by `poster=`, not `src=`, so scanning only src
    # would let a missing one through and show a black box until play.
    posters = [i for i in build_site._referenced_images() if "hungaroring" in i]
    assert posters, "the demo video has no poster frame"


def test_every_video_on_the_page_has_a_poster():
    # Both players sit above the fold on a phone; an unposted one is a black
    # rectangle until the viewer taps it.
    players = re.findall(r"<video\b[^>]*>", INDEX, flags=re.DOTALL)
    assert len(players) >= 2
    for player in players:
        assert "poster=" in player, player


def test_the_demo_section_says_the_engineer_responses_are_not_scripted():
    # The claim that carries the whole demo. If it stops being true, the
    # sentence has to go with it.
    assert "No engineer response was written for this video" in INDEX


def test_every_referenced_asset_exists():
    missing = [
        name for name in build_site._referenced_assets()
        if not (build_site.ASSET_DIR / name).exists()
    ]
    assert not missing, f"referenced but absent from website/assets/: {missing}"


def test_every_referenced_screenshot_exists():
    missing = [
        name for name in build_site._referenced_images()
        if not (build_site.SCREENSHOT_DIR / name).exists()
    ]
    assert not missing, f"referenced but absent from docs/screenshots/: {missing}"


def test_the_build_copies_only_what_the_pages_use(tmp_path, monkeypatch):
    monkeypatch.setattr(build_site, "OUTPUT_DIR", tmp_path / "_site")
    output = build_site.build()
    copied = {path.name for path in (output / "img").iterdir()}
    assert copied == build_site._referenced_images()
    assert (output / "index.html").exists()
    assert (output / "guide.html").exists()
    assert (output / "styles.css").exists()


def test_rebuilding_over_an_existing_output_refreshes_it(tmp_path, monkeypatch):
    # A rebuild once crashed here: rmtree cannot remove a directory that a
    # preview server holds as its working directory, so the build died and
    # left the previous version in place, looking published.
    out = tmp_path / "_site"
    monkeypatch.setattr(build_site, "OUTPUT_DIR", out)
    build_site.build()

    stale = out / "index.html"
    stale.write_text("STALE", encoding="utf-8")
    orphan = out / "img" / "removed-screenshot.png"
    orphan.write_bytes(b"old")

    build_site.build()

    assert "STALE" not in stale.read_text(encoding="utf-8")
    assert not orphan.exists(), "a screenshot no longer referenced was left behind"


def test_a_locked_output_file_fails_loudly(tmp_path, monkeypatch):
    out = tmp_path / "_site"
    monkeypatch.setattr(build_site, "OUTPUT_DIR", out)
    build_site.build()

    real_unlink = Path.unlink

    def refuse(self, *args, **kwargs):
        if self.name == "index.html":
            raise PermissionError("held open by another process")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", refuse)
    # Silently publishing a stale page is the failure worth preventing.
    with pytest.raises(SystemExit, match="index.html"):
        build_site.build()


# ---------------------------------------------------------------------------
# Claims that must stay true
# ---------------------------------------------------------------------------


def test_the_buyer_is_told_they_need_their_own_openai_key():
    # The single most refund-generating surprise if omitted.
    assert "OpenAI API key" in INDEX
    assert "billed" in INDEX.lower()
    assert "your own OpenAI account" in EULA


def test_realtime_voice_audio_is_disclosed():
    assert "Realtime voice streams microphone audio to OpenAI" in INDEX
    assert "microphone audio is streamed to OpenAI" in EULA


def test_the_site_uses_the_official_2026_game_name():
    assert "F1 25: 2026 Season Pack" in INDEX
    assert "F1 25: 2026 Season Pack" in GUIDE
    assert "F1 26" not in INDEX
    assert "UDP telemetry set to format <strong>2026</strong>" in INDEX


def test_macos_is_not_advertised_as_available():
    # There is no macOS build yet. Selling one would be a lie.
    assert "macOS is not ready yet" in INDEX
    assert "Windows 10 or 11" in INDEX


def test_no_destructive_tamper_response_is_claimed():
    # The kill-switch was dropped for refuse-to-run; the EULA must match the
    # code, and must not disclose a behaviour that no longer exists.
    assert "refuses to start" in EULA
    assert "never deletes" in EULA
    assert not re.search(r"kill.?switch", EULA, re.IGNORECASE)
    assert not re.search(r"kill.?switch", INDEX, re.IGNORECASE)


def test_the_page_carries_no_reviews_of_its_own():
    # Reviews come from the Worker at load time; the page itself holds only
    # the empty state and the form. Nothing static could read as a fabricated
    # testimonial: no review cards, and the only stars are the five rating
    # buttons inside the form.
    assert "No reviews published yet" in INDEX
    assert not re.search(r'class="(review|testimonial)"', INDEX)
    stars = INDEX.count("★")
    form = INDEX.split('<div class="star-options">')[1].split("</div>")[0]
    assert stars == 5 and form.count("★") == 5


def test_the_independence_disclaimer_is_present():
    for owner in ("Formula", "FIA", "Electronic Arts", "Codemasters"):
        assert owner in INDEX, f"{owner} missing from the non-affiliation notice"
    assert "not affiliated" in EULA.lower()


def test_the_comparison_admits_where_pit_wall_loses():
    # A comparison table where the product wins every row is an advert, not a
    # comparison. These two rows are the honest losses.
    assert "Radio needs it" in INDEX
    assert "Your AI provider usage" in INDEX


def test_multi_provider_claims_carry_the_voice_caveat():
    # 4.7 sells provider choice. The claim is only honest with its limit
    # attached: reasoning is switchable, the spoken radio is OpenAI-backed.
    for provider in ("Claude", "DeepSeek", "Kimi"):
        assert provider in INDEX, f"{provider} missing from the provider choice"
    assert "spoken radio" in INDEX.lower()
    # Wherever another provider is offered, the OpenAI voice dependency is
    # stated on the same page, in requirements, FAQ and the licence terms.
    assert "always uses the OpenAI key" in INDEX or "always uses OpenAI" in INDEX
    assert "always uses OpenAI" in EULA
    assert "Claude, DeepSeek or Kimi" in GUIDE


@pytest.mark.parametrize("page", [INDEX, GUIDE, DIAGNOSTICS, EULA])
def test_pages_are_accessible_and_responsive(page):
    assert 'lang="en"' in page
    assert 'name="viewport"' in page
    # Every image needs alt text; a decorative one needs alt="".
    for tag in re.findall(r"<img\b[^>]*>", page):
        assert "alt=" in tag, f"image without alt text: {tag[:80]}"
