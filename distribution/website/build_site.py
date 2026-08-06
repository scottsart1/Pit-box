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
ASSETS = ("styles.css",)

# Every value that must be replaced before the site is public. Each is paired
# with what to do about it, because "placeholder found" alone is not actionable.
PLACEHOLDERS: dict[str, str] = {
    "@YOUR-VENMO-HANDLE": "Set your real Venmo handle in index.html.",
    "YOUR-EMAIL@example.com": "Set the contact address in index.html and eula.html.",
    "[JURISDICTION]": "Name the governing-law jurisdiction in eula.html section 10.",
}


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

    combined = "\n".join((SITE_DIR / page).read_text(encoding="utf-8") for page in PAGES)
    for token, fix in PLACEHOLDERS.items():
        if token in combined:
            problems.append(f"Placeholder {token} is still on the page. {fix}")

    for image in sorted(_referenced_images()):
        if not (SCREENSHOT_DIR / image).exists():
            problems.append(f"{image} is referenced but not in docs/screenshots/.")

    return SiteCheck(not problems, tuple(problems))


def build() -> Path:
    if OUTPUT_DIR.exists():
        shutil.rmtree(OUTPUT_DIR)
    images = OUTPUT_DIR / "img"
    images.mkdir(parents=True)

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
