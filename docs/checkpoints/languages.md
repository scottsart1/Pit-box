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
| 5 | Production release | done - see Release record |
| 6 | Native-speaker review of the catalogs | not started |

## Release record (5.5.0)

- `main` at the release: 5953bea (refusal fix, translations, version bump).
  CI Windows installer run 38206144568 (install smoke passed) and Android
  run 38206146758, both green on that commit.
- Windows `PitWall-Setup.exe` SHA-256
  `6bc5d078a2817a95215ddb54041d7f01614687063df40c99236c0d35c87863dc`,
  35,449,378 bytes, the CI artifact unchanged; unsigned as before.
- Android `YourPitBox-5.5.0-android.39.apk` SHA-256
  `682ffbc69a7b154a0ccc905464e7b71caaa819b9ba5368877c6b7579e8aded07`,
  73,629,017 bytes: the CI unsigned release APK (SHA-256 `79ba730f...6a07`,
  already zip-aligned) signed with the existing production certificate
  (SHA-256 `20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`),
  APK Signature Scheme v2 and v3. versionCode 39, `5.5.0-android.39`, with
  the French, German and Spanish catalogs.
- Checksums on the website in 661d3fe. Both artifacts uploaded to R2; Worker
  deployed (version 83e4d9da, `ANDROID_KEY` names the 5.5.0 APK); public
  `/installer` and `/android` serve exactly those bytes; website deployed to
  production and identical to the build apart from Cloudflare's email
  rewrite; update manifests published for Windows and Android, one notes
  file per platform.
- The test tablet was not upgraded. 5.5.0 changes no stored data (no schema
  or migration change; the only new setting, `ui_language`, defaults to
  English), and the in-place upgrade path is covered by versionCode 39 over
  38 with the unchanged production certificate. The translated screens have
  not been checked on a physical device.
