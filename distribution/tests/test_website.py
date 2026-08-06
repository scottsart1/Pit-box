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
EULA = (DIST / "website" / "eula.html").read_text(encoding="utf-8")
DOWNLOAD_JS = (DIST / "website" / "download.js").read_text(encoding="utf-8")

REAL_ENDPOINT = "https://activation.pitwall.app"

# download.js now carries the deployed Worker's URL, so the placeholder has to
# be reintroduced deliberately to test that the guard still catches it. Built
# by substitution rather than hardcoded so it cannot drift from the real file.
PLACEHOLDER_JS = re.sub(
    r'const ACTIVATION_API = "[^"]+";',
    'const ACTIVATION_API = "https://activation.example.invalid";',
    DOWNLOAD_JS,
)


def _stage(tmp_path, monkeypatch, *, index=None, eula=None, script=None):
    """A site directory with every scanned file present and filled in.

    Each test then breaks exactly one thing, so a failure names its own cause.
    """
    site = tmp_path / "website"
    site.mkdir(exist_ok=True)
    (site / "index.html").write_text(index if index is not None else INDEX, encoding="utf-8")
    (site / "eula.html").write_text(eula if eula is not None else EULA, encoding="utf-8")
    (site / "download.js").write_text(
        script if script is not None else DOWNLOAD_JS, encoding="utf-8"
    )
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


def test_the_buyer_is_told_to_put_their_email_in_the_venmo_note():
    # Fulfilment is manual, so the payment note is the only channel carrying
    # the address a code gets sent to. Losing it means a paid order with no
    # way to deliver.
    assert "payment note" in INDEX


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


def test_the_video_poster_is_checked_like_any_other_image():
    # A poster is referenced by `poster=`, not `src=`, so scanning only src
    # would let a missing one through and show a black box until play.
    posters = [i for i in build_site._referenced_images() if "hungaroring" in i]
    assert posters, "the demo video has no poster frame"


def test_the_demo_section_says_the_dialogue_is_not_scripted():
    # The claim that carries the whole demo. If it stops being true, the
    # sentence has to go with it.
    assert "No dialogue was written for this video" in INDEX


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


def test_the_reviews_section_is_empty_and_says_so():
    assert "No reviews yet" in INDEX
    # Nothing that could read as a fabricated testimonial.
    assert "★" not in INDEX
    assert not re.search(r'class="(review|testimonial)"', INDEX)


def test_the_independence_disclaimer_is_present():
    for owner in ("Formula", "FIA", "Electronic Arts", "Codemasters"):
        assert owner in INDEX, f"{owner} missing from the non-affiliation notice"
    assert "not affiliated" in EULA.lower()


def test_the_comparison_admits_where_pit_wall_loses():
    # A comparison table where the product wins every row is an advert, not a
    # comparison. These two rows are the honest losses.
    assert "Radio needs it" in INDEX
    assert "Your OpenAI usage" in INDEX


@pytest.mark.parametrize("page", [INDEX, EULA])
def test_pages_are_accessible_and_responsive(page):
    assert 'lang="en"' in page
    assert 'name="viewport"' in page
    # Every image needs alt text; a decorative one needs alt="".
    for tag in re.findall(r"<img\b[^>]*>", page):
        assert "alt=" in tag, f"image without alt text: {tag[:80]}"
