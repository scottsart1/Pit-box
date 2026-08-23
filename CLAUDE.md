# Working agreements for this repository

## Design requests

When the user mentions "design" (designing a screen, a dashboard, a page, a
card, deciding what belongs on which page, and similar), use the design
capabilities (the `design` skill / design canvas) rather than answering in
prose or jumping straight to code:

- Ground every mockup in the app's real visual system first: the tokens and
  components in `static/index.html` (inline styles) and `static/css/v42.css`
  — dark `#070b0f` background, `#121b24` cards with `#263542` hairlines,
  accents `#49d17d` green / `#3f86ff` blue / `#ff6969` red / `#ffc15c` amber,
  11px uppercase labels with `.12em` tracking, 900-weight heroes, Segoe UI
  stack, 44px minimum hit targets.
- The product's screenshots live in `docs/screenshots/` — treat them as the
  current-state reference.
- Board-organization principle established in the 4.7 design pass: DRIVE
  holds only what a driver reads mid-race; pit-lane admin (diagnostics,
  calibration, provider tests) lives on CONNECTION; briefs and planning live
  on STRATEGY; a one-line system strip summarizes health on every page.
- The 4.7 design canvas (DRIVE redesign, Strategy workspace, board map,
  alternates) is the standing reference for UI direction; extend it rather
  than starting a new aesthetic.

## Verification

Run `python -m pytest tests -q` for the app suite. `distribution/tests`
requires Windows packaging tools (PyInstaller, openpyxl) and fails on Linux;
that is environmental, not a regression.
