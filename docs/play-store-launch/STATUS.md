# Google Play launch status

Updated: 13 November 2026

Release baseline: `origin/main` at `e23294c` (Your Pit Box 5.8.0, Android revision 44). The `play-store-launch` branch raises the Android revision to 45. The public website and Play listing are not yet updated.

## Confirmed

- The Play Console app exists as a draft with package `com.yourpitbox.app` under a personal developer account.
- Play Console currently shows 0 of 11 app information and store listing tasks complete, no internal release, and 0 closed testers opted in.
- The account must complete a closed test with 12 opted-in testers for 14 continuous days, then apply for production access. Production submission is not available yet.
- The Android project targets API 36, supports Android 7+ (`minSdk 24`), and packages `arm64-v8a` and `x86_64`.
- The public Android APK is signed with certificate SHA-256 `20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`. All 40 native libraries in that APK had ELF load-segment alignment of at least 16 KB and its uncompressed library entries were ZIP aligned to 16 KB. This is a binary inspection, not a 16 KB device run or AAB validation. The website currently claims 4 KB page support only.
- Driver profiles, sign-in reports, and encrypted API-key storage are on main. The first profile is designed to inherit an existing installation's data, but a signed APK to Play update with retained data has not been verified.
- Play listing copy, a privacy-policy draft, Data safety research, and branded artwork candidates are in this branch. The phone captures came from the owner; the tablet capture came from an Android emulator test. See `ASSETS.md` for provenance and version caveats.
- The first CI run for this branch reached 3,779 passing tests and failed one assertion that still required revision 44. That assertion now checks for revision 45 or later; the release bundle job had not run at the time of this update.

## Blocking work

1. Build and verify a signed AAB with a new version code using `prepare-play-bundle.yml` after merge. Confirm the Play **app signing certificate**, distinct from the upload certificate, preserves the existing APK upgrade path before the first Play upload. See `SIGNING.md`.
2. Run the release candidate on a 16 KB page-size device or emulator, and test upgrade from an installed signed APK with saved history, settings, profiles, and keys.
3. Publish and verify the privacy policy; complete Play's Data safety and account-deletion answers from the actual implementation. The local profile appears to meet Google's offline-account exemption, but the owner must confirm no unpublished cloud login or recovery exists. Local driver profiles also send pseudonymous sign-in events to the developer's server; the existing public licence and website wording that says “no account” is stale until this branch deploys.
4. Validate the prepared phone and tablet screenshot candidates, icon, and feature graphic against the final build; then complete the store listing and app-content forms.
5. Start a closed test with 12 real, consenting testers and keep them opted in for 14 continuous days. Apply for production access with actual results. Public production rollout requires owner approval.

## Next action

Complete the Play preparation branch and CI checks, then produce a private signed AAB candidate for internal testing. Do not upload a bundle until the app-signing identity is resolved.
