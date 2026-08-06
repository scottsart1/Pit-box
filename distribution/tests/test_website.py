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


# ---------------------------------------------------------------------------
# Publishing safety
# ---------------------------------------------------------------------------


def test_the_unfilled_site_is_blocked_from_publishing():
    # As committed, the placeholders are present, so the check must fail. If
    # this ever passes, the real handle and email are committed to a public
    # repo, which is its own problem.
    result = build_site.check()
    assert not result.ok
    assert any("YOUR-VENMO-HANDLE" in problem for problem in result.problems)


def test_every_placeholder_names_the_fix():
    for token, fix in build_site.PLACEHOLDERS.items():
        assert fix.endswith("."), f"{token} has no actionable fix message"
        assert len(fix) > 20


def test_a_filled_site_passes(tmp_path, monkeypatch):
    site = tmp_path / "website"
    site.mkdir()
    filled_index = INDEX
    filled_eula = EULA
    for token in build_site.PLACEHOLDERS:
        filled_index = filled_index.replace(token, "filled")
        filled_eula = filled_eula.replace(token, "filled")
    (site / "index.html").write_text(filled_index, encoding="utf-8")
    (site / "eula.html").write_text(filled_eula, encoding="utf-8")

    monkeypatch.setattr(build_site, "SITE_DIR", site)
    result = build_site.check()
    assert result.ok, result.report()


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
