"""Assemble the deployable marketing site.

    python -m distribution.website.build_site --check   # verify only
    python -m distribution.website.build_site           # write _site/

The screenshots live in `docs/screenshots/` and are copied in at build time
rather than committed twice: they are ~860 KB, and a second copy in the repo
would drift from the real ones the moment a screen is recaptured.

The placeholder check exists because the failure it prevents is embarrassing
and silent — a live page asking buyers to Venmo `@YOUR-VENMO-HANDLE` looks
exactly like a working page.
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

SITE_DIR = Path(__file__).resolve().parent
REPO_ROOT = SITE_DIR.parents[1]
SCREENSHOT_DIR = REPO_ROOT / "docs" / "screenshots"
OUTPUT_DIR = SITE_DIR / "_site"

PAGES = ("index.html", "eula.html")
ASSETS = ("styles.css", "download.js")

# Scanned for placeholders alongside the pages. download.js carries the
# activation endpoint, and a site published with the placeholder host has a
# Download button that silently fails — which looks like a working page.
SCANNED = PAGES + ("download.js",)

# Every value that must be replaced before the site is public. Each is paired
# with what to do about it, because "placeholder found" alone is not actionable.
PLACEHOLDERS: dict[str, str] = {
    # Empty: the Venmo handle, contact email and governing-law clause are all
    # settled. Add an entry here if a new fill-in-later value is introduced.
}

# Catches placeholder *shapes* rather than known strings, so a value added
# later cannot slip out unfilled just because nobody remembered to register
# it above. The failure this prevents is silent: a live page reading
# "email YOUR-EMAIL@example.com" looks exactly like a working page.
PLACEHOLDER_PATTERNS: tuple[tuple[str, str], ...] = (
    (r"\[[A-Z][A-Z _-]{2,}\]", "a [BRACKETED] placeholder"),
    (r"\bYOUR[-_][A-Z]", "a YOUR-SOMETHING placeholder"),
    (r"\bexample\.(?:com|invalid)\b", "an example.com address"),
    (r"\bTODO\b|\bFIXME\b", "a TODO marker"),
)


@dataclass(frozen=True, slots=True)
class SiteCheck:
    ok: bool
    problems: tuple[str, ...]

    def report(self) -> str:
        return "\n".join(f"  BLOCKED  {p}" for p in self.problems) or "  ready to publish"


def _referenced_images() -> set[str]:
    """Every img src the pages actually use, so a missing one fails the build."""
    found: set[str] = set()
    for page in PAGES:
        html = (SITE_DIR / page).read_text(encoding="utf-8")
        found.update(re.findall(r'src="img/([^"]+)"', html))
    return found


def check() -> SiteCheck:
    problems: list[str] = []

    for page in PAGES:
        if not (SITE_DIR / page).exists():
            problems.append(f"{page} is missing.")
    if problems:
        return SiteCheck(False, tuple(problems))

    # Only the visible page matters: the HTML comments in eula.html discuss
    # placeholders on purpose, and flagging those would make the check cry wolf.
    combined = "\n".join(
        re.sub(r"<!--.*?-->", "", (SITE_DIR / name).read_text(encoding="utf-8"), flags=re.DOTALL)
        for name in SCANNED
    )
    for token, fix in PLACEHOLDERS.items():
        if token in combined:
            problems.append(f"Placeholder {token} is still on the page. {fix}")
    for pattern, described in PLACEHOLDER_PATTERNS:
        for hit in sorted(set(re.findall(pattern, combined))):
            problems.append(f"{hit!r} looks like {described}. Replace it with a real value.")

    for image in sorted(_referenced_images()):
        if not (SCREENSHOT_DIR / image).exists():
            problems.append(f"{image} is referenced but not in docs/screenshots/.")

    return SiteCheck(not problems, tuple(problems))


def _reset_output() -> Path:
    """Empty the output directory without requiring the directory itself to go.

    `shutil.rmtree` fails on Windows if anything holds a handle to a folder
    inside it — a preview server using it as its working directory, or an open
    Explorer window. Deleting the *files* and leaving the directories achieves
    the same result and cannot be blocked that way, because every file is
    rewritten immediately afterwards.
    """
    if OUTPUT_DIR.exists():
        stale = [path for path in OUTPUT_DIR.rglob("*") if path.is_file()]
        undeletable = []
        for path in stale:
            try:
                path.unlink()
            except OSError:
                undeletable.append(path.name)
        if undeletable:
            raise SystemExit(
                "Could not replace "
                + ", ".join(sorted(undeletable))
                + f" in {OUTPUT_DIR}. Close whatever has the file open "
                "(a preview server, an editor) and run this again."
            )
    images = OUTPUT_DIR / "img"
    images.mkdir(parents=True, exist_ok=True)
    return images


def build() -> Path:
    images = _reset_output()

    for name in (*PAGES, *ASSETS):
        shutil.copy2(SITE_DIR / name, OUTPUT_DIR / name)
    for image in sorted(_referenced_images()):
        shutil.copy2(SCREENSHOT_DIR / image, images / image)

    return OUTPUT_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    parser.add_argument(
        "--preview", action="store_true",
        help="build for local review even with placeholders unfilled (never deploy this)",
    )
    args = parser.parse_args(argv)

    result = check()
    print("Site check:")
    print(result.report())

    if args.check:
        return 0 if result.ok else 1

    if not result.ok:
        if not args.preview:
            print("\nRefusing to build: fix the BLOCKED items above.")
            return 1
        print("\n--preview: building anyway. DO NOT DEPLOY this output.")

    output = build()
    total = sum(f.stat().st_size for f in output.rglob("*") if f.is_file())
    print(f"\nBuilt {output} ({total / 1024:.0f} KB)")
    print("Deploy by uploading that folder to Cloudflare Pages, Netlify or GitHub Pages.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
