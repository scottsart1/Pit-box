# Google Play launch status

Updated: 13 November 2026

Release baseline: `origin/main` at `55f386b` (Your Pit Box 5.8.0, Android revision 44). This branch contains Play preparation only; the public website and Play listing are not yet updated.

## Confirmed

- The Play Console app exists as a draft with package `com.yourpitbox.app` under a personal developer account.
- Play Console currently shows 0 of 11 app information and store listing tasks complete, no internal release, and 0 closed testers opted in.
- The account must complete a closed test with 12 opted-in testers for 14 continuous days, then apply for production access. Production submission is not available yet.
- The Android project targets API 36, supports Android 7+ (`minSdk 24`), and packages `arm64-v8a` and `x86_64`.
- The public Android APK is signed with certificate SHA-256 `20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`. All 40 native libraries in that APK had ELF load-segment alignment of at least 16 KB and its uncompressed library entries were ZIP aligned to 16 KB. This is a binary inspection, not a 16 KB device run or AAB validation. The website currently claims 4 KB page support only.
- Driver profiles, sign-in reports, and encrypted API-key storage are on main. The first profile is designed to inherit an existing installation's data, but a signed APK to Play update with retained data has not been verified.

## Blocking work

1. Build and verify a signed AAB with a new version code. Confirm its certificate and Play App Signing choice preserve the existing APK upgrade path before the first Play upload.
2. Run the release candidate on a 16 KB page-size device or emulator, and test upgrade from an installed signed APK with saved history, settings, profiles, and keys.
3. Publish and verify the privacy policy; complete Play's Data safety and account-deletion answers from the actual implementation. Local driver profiles also send pseudonymous sign-in events to the developer's server; the existing licence and website wording that says “no account” is stale.
4. Prepare genuine current Android phone and tablet screenshots, a 512 × 512 icon, and a 1024 × 500 feature graphic, then complete the store listing and app-content forms.
5. Start a closed test with 12 real, consenting testers and keep them opted in for 14 continuous days. Apply for production access with actual results. Public production rollout requires owner approval.

## Next action

Complete the Play preparation branch and CI checks, then produce a private signed AAB candidate for internal testing. Do not upload a bundle until the app-signing identity is resolved.
