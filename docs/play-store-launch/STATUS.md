# Google Play launch status

Updated: 13 November 2026

Release baseline: `origin/main` at `644e5ea` (Your Pit Box 5.8.0, Android revision 44). The `play-store-launch` branch raises the Android revision to 45. The public website has not yet deployed this branch, and the Play listing remains a saved draft.

## Confirmed

- The Play Console app exists as a draft with package `com.yourpitbox.app` under a personal developer account.
- Play Console setup now has six saved declarations: Ads, Government apps, Financial features, Health, category/contact details, and Advertising ID (No). The default English store listing is also saved as a draft. All changes remain pending submission for Play review; there is no internal release and 0 closed testers opted in.
- The account must complete a closed test with 12 opted-in testers for 14 continuous days, then apply for production access. Production submission is not available yet.
- The Android project targets API 36, supports Android 7+ (`minSdk 24`), and packages `arm64-v8a` and `x86_64`.
- The public Android APK is signed with certificate SHA-256 `20c2751e5c0ede43a2442331336b990433e53d9e512f3f1bca3e8d5bee4c6983`. All 40 native libraries in that APK had ELF load-segment alignment of at least 16 KB and its uncompressed library entries were ZIP aligned to 16 KB. This is a binary inspection, not a 16 KB device run or AAB validation. The website currently claims 4 KB page support only.
- Driver profiles, sign-in reports, and encrypted API-key storage are on main. The first profile is designed to inherit an existing installation's data, but a signed APK to Play update with retained data has not been verified.
- Play listing copy, a privacy-policy draft, Data safety research, and branded artwork candidates are in this branch. The phone captures came from the owner; the tablet capture came from an Android emulator test. See `ASSETS.md` for provenance and version caveats. The default English Play listing has been saved as a draft with the icon, feature graphic, two phone captures and one 10-inch tablet capture. Category Sports, public support email and secure website URL have also been saved; none of these changes has been sent for Play review.
- Play Console already shows an app signing key in use. Its SHA-256 fingerprint, verified through the Console's certificate-copy control, differs from the existing APK. The Play key must be replaced with the existing APK signing key before uploading a release if direct-download users are to update without uninstalling. See `SIGNING.md`.
- CI found and fixed a stale revision-44 assertion and three missing translations for the new privacy link. The earlier full regression-check job in run 43393677618 passed. Its unsigned Play-format AAB was built and independently inspected: all 40 native libraries have at least 16 KB ELF load-segment alignment. That run's emulator job was cancelled when the branch was updated. The current branch also includes the latest `main` emulator fixes; its targeted website and Android project tests pass locally (123 tests). The workflow now runs the smoke suite on both ordinary and 16 KB Android emulators and asserts the latter's actual page size; a fresh full CI run is needed. The inspected AAB is unsigned and is not a Play upload candidate.
- The native app requires a new local driver profile on a clean install. Google's published review guidance explicitly permits a user-set PIN when clear setup instructions are provided. The Play form's generic warning about reviewers creating accounts therefore needs careful handling, but a guest mode is not proven necessary. Optional AI/voice requires a separate provider key; determine whether a reusable review key is necessary before attesting to full access. The target-audience declaration cannot start until sign-in details is completed. See [Google's sign-in review guidance](https://support.google.com/googleplay/android-developer/answer/15748846).
- A conservative Data safety form has been saved as a **draft**, with pseudonymous IDs and app interactions, optional AI requests, and optional voice data disclosed. It is not submitted. Its account-creation and transport answers need final factual review, and the privacy URL is not yet live. See `DATA_SAFETY.md`.

## Blocking work

1. Replace the mismatched Play app signing key with the existing APK app signing key. Build and verify a signed AAB with a new version code using `prepare-play-bundle.yml` after merge. Confirm the Play **app signing certificate**, distinct from the upload certificate, preserves the existing APK upgrade path before the first Play upload. See `SIGNING.md`.
2. Run the release candidate on a 16 KB page-size device or emulator, and test upgrade from an installed signed APK with saved history, settings, profiles, and keys.
3. Publish and verify the privacy policy; complete Play's Data safety and account-deletion answers from the actual implementation. The local profile appears to meet Google's offline-account exemption, but the owner must confirm no unpublished cloud login or recovery exists. Local driver profiles also send pseudonymous sign-in events to the developer's server; the existing public licence and website wording that says “no account” is stale until this branch deploys.
4. Finalize reviewer access instructions and any needed optional-provider test access, validate the prepared phone and tablet screenshot candidates, icon, and feature graphic against the final build, then complete the remaining app-content forms.
5. Start a closed test with 12 real, consenting testers and keep them opted in for 14 continuous days. Apply for production access with actual results. Public production rollout requires owner approval.

## Next action

Complete the Play preparation branch and CI checks, then produce a private signed AAB candidate for internal testing. Do not upload a bundle until the app-signing identity is resolved.
