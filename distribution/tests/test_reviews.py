"""Reviews on the website: posted by anyone, shown only once read.

The Worker has no JS test harness, so these pin its source the same way
test_ledger_sync.py pins the retired-code checks: the list route must filter
on approval, the insert must start unapproved, and the schema and migration
must agree on the table.
"""

from __future__ import annotations

import re

from pathlib import Path

SERVER = Path(__file__).resolve().parents[1] / "activation-server"
WORKER = (SERVER / "src" / "worker.js").read_text(encoding="utf-8")


def _handler(name: str) -> str:
    return WORKER.split(f"async function {name}")[1].split("\nasync function")[0]


def test_the_worker_lists_only_reviews_the_owner_has_approved():
    listing = _handler("handleReviewsList")
    assert "WHERE approved = 1" in listing
    # The reply address is private: it must never be selected for the page.
    select = listing.split("SELECT")[1].split("FROM")[0]
    assert "email" not in select and "address_hash" not in select


def test_a_new_review_is_stored_unapproved():
    submit = _handler("handleReviewSubmit")
    insert = submit.split("INSERT INTO reviews")[1].split(".bind")[0]
    assert "approved" in insert and "0)" in insert


def test_a_new_review_is_validated_and_rate_limited():
    submit = _handler("handleReviewSubmit")
    assert "rating < 1 || rating > 5" in submit
    assert "REVIEW_BODY_MIN" in submit and "REVIEW_BODY_MAX" in submit
    assert "address_hash = ?" in submit and "429" in submit
    # Honeypot: a filled "website" field is dropped without an error, so a
    # bot cannot tell it was caught.
    assert "payload.website" in submit


def test_both_review_routes_are_wired():
    assert 'request.method === "GET" && url.pathname === "/reviews"' in WORKER
    assert 'request.method === "POST" && url.pathname === "/reviews"' in WORKER


def test_schema_and_migration_agree_on_the_reviews_table():
    schema = (SERVER / "schema.sql").read_text(encoding="utf-8")
    migration = (SERVER / "migrations" / "0004_reviews.sql").read_text(encoding="utf-8")
    for column in ("name", "rating", "body", "email", "created_at", "address_hash", "approved"):
        assert column in schema and column in migration
    assert "CHECK (rating BETWEEN 1 AND 5)" in migration
    assert "approved     INTEGER NOT NULL DEFAULT 0" in migration


def test_the_star_rating_reads_one_to_five_from_the_left():
    """The scale must not silently invert.

    The stars are a CSS trick: the labels follow their inputs in source, so
    `input:checked ~ label` can only colour the checked star and the ones
    AFTER it. To make that read as "fill up to the one chosen", the row is
    reversed on screen, which means the inputs have to be listed in
    DESCENDING order. Listed ascending, the whole scale flips: the rightmost
    star lights all five and submits 1, and a happy reviewer files one star.
    That shipped once, so pin both halves of the arrangement together.
    """
    site = Path(__file__).resolve().parents[1] / "website"
    markup = (site / "index.html").read_text(encoding="utf-8")
    styles = (site / "styles.css").read_text(encoding="utf-8")

    options = markup.split('class="star-options"')[1].split("</div>")[0]
    values = re.findall(r'name="rating" value="(\d)"', options)
    assert values == ["5", "4", "3", "2", "1"], (
        f"star inputs must be listed 5 down to 1, found {values}"
    )

    # The descending order is only correct because the row is reversed; if the
    # reversal ever goes, this ordering becomes the bug instead of the fix.
    assert re.search(
        r"\.star-options\s*\{[^}]*flex-direction:\s*row-reverse", styles
    ), "the reversed row is what puts the descending inputs back in 1..5 order"

    # Each star still announces its own value to a screen reader, which reads
    # source order and so is unaffected by the visual reversal.
    for n in range(1, 6):
        assert f'<span class="sr-only">{n} out of 5</span>' in options
