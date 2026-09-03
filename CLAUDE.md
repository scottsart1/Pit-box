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
- The 4.8.0 in-app implementation of that canvas (the New view toggle on
  DRIVE) was reverted in 4.8.1 at the owner's request — the owner prefers
  the original board in the running app. Do not reintroduce an in-app
  redesign without being asked; canvas work stays on the canvas.

## Commit messages, PR descriptions and comments

This repository is public and these are permanent. Write them about the
change, for someone reading the repo later with no knowledge of how it was
produced.

- **Never include a session or chat-transcript URL** (`claude.ai/code/...`
  or similar), in a commit trailer, a PR body, or a file. It is a link to a
  private resource that is useless to every reader.
- **Do not narrate the conversation that produced the change**: no
  reasoning about instructions, no "the owner asked for", no "this was the
  wrong instinct", no recounting of corrections. Describe what changed and
  why it is right. An exception only when the owner explicitly asks for such
  a note, and then keep it to one line.
- Do not report work that is not in the diff (cleanups done elsewhere,
  scans run, conversation outcomes). A PR describes its own changes.
- A `Co-Authored-By:` trailer is fine. The project discloses its AI use
  openly in the README's "How this was built" section.

## Verification

Run `python -m pytest tests -q` for the app suite. `distribution/tests`
requires Windows packaging tools (PyInstaller, openpyxl) and fails on Linux;
that is environmental, not a regression.
