# Checkpoint: French, German and Spanish

Working document for the screen-language work released in 5.5.0. It records
the goal, the decisions, what is finished and what remains, so that another
engineer (or another AI coding agent) can resume without rediscovering
anything. Update the status table and the release record at every checkpoint.

## Goal

1. **Radio first.** A driver speaking French, German or Spanish with English
   radio words mixed in must never have a refusal read as a commitment.
2. **Screens.** Every screen of the app, the Driver Dashboard, the stream
   overlay and the Android start-up screens can be shown in French, German
   or Spanish.
3. Release to production (Windows + Android + site + update flags).

## Key decisions

- The engineer's radio and its written explanations stay in English. Only
  fixed words the server sends (weather, engineer status, radio latency stage,
  fixed review notices, strategy reasons) are translated; they are listed in
  `static/i18n/server-keys.json`.
- Catalogs are keyed by the English text, so English needs no catalog and is
  served byte-identical. `tools/i18n_extract.py` collects the keys.
- Languages are free in every build, like every other feature.
- The Driver Dashboard keeps its own language choice, so a tablet can differ
  from the PC; inside the app it follows the app.
- Translations are machine-drafted and AI-reviewed. A native-speaker review
  is still wanted.

## Status

| # | Checkpoint | Status |
|---|------------|--------|
| 1 | French, German and Spanish negations in `intent.py` | done - `tests/test_foreign_negation.py` |
| 2 | Screen language setting, runtime, catalogs | done - `tests/test_ui_i18n.py`, `tests/i18n_runtime.test.mjs` |
| 3 | Layout checked at 1280, 768, 390 and 344 px against English | done - labels shortened, three tight rows given room on translated pages |
| 4 | Version 5.5.0, Android revision 39, release notes | done - `docs/release-notes-5.5.0.md` |
| 5 | Production release | in progress - see Release record |
| 6 | Native-speaker review of the catalogs | not started |

## Release record (5.5.0)

- `main` carries the refusal fix, the translations and the version bump.
- Remaining: Windows installer from CI, Android APK signed with the
  production certificate, both uploaded to R2 with `/installer` and `/android`
  hashes verified, Worker deployed (its `ANDROID_KEY` names the 5.5.0 APK),
  website deployed with the checksums, update manifests published for
  Windows and Android, test tablet upgraded in place with its data preserved.
