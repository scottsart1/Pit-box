# Google Play launch status

Updated: 16 November 2026

[PR #66](https://github.com/scottsart1/Pit-box/pull/66) merged into `main` at `4b9bc0e`. The Play candidate is Your Pit Box 5.8.0, Android version code 45. The existing direct-download APK is revision 44.

## Verified

- [CI run 43796390204](https://github.com/scottsart1/Pit-box/actions/runs/43796390204) passed the shared checks, unsigned bundle native ELF alignment, and the ordinary and 16 KB Android 16 x86_64 emulator flows. Both flows verified startup, driver switching and separate storage, UDP reception, fullscreen behavior, transfer controls, native Setup and Test Engineer, and session-report export. The 16 KB run checked a page size of 16,384 bytes.
- Physical Wi-Fi and paired-device history copying were outside this smoke test. A prior API 35 image crashed in the WebView 124 renderer. The green API 36 run does not resolve that older configuration. The signed Play-delivered upgrade path and generated APK ZIP alignment still need verification.
- Play Console shows `com.yourpitbox.app` on a personal developer account. The internal track is inactive with an empty release draft. The dashboard shows zero opted-in closed testers and production access is unavailable.
- The active Play app-signing certificate still differs from the direct-download APK, verified on 16 November. Both signing workflows are on `main`. GitHub Actions shows no runs of either workflow on `main`.
- The English listing, support details, artwork and the dedicated [privacy policy](https://yourpitbox.com/privacy.html) were saved in Play Console. The last saved app-content setup included IARC Everyone/PEGI 3, a target audience of 13 and older, and a Data safety draft that discloses that some user-configured provider traffic can use HTTP. See [asset provenance](ASSETS.md) and the [Data safety review](DATA_SAFETY.md).

## Next actions

1. Run the [key transfer workflow](https://github.com/scottsart1/Pit-box/actions/workflows/prepare-play-key-transfer.yml) on `main` using the GitHub `release` environment. Import its encrypted output via the owner-approved Play key change. Match the **app signing certificate** in Play to the direct APK before any AAB upload. Follow [SIGNING.md](SIGNING.md).
2. Run the [signed bundle workflow](https://github.com/scottsart1/Pit-box/actions/workflows/prepare-play-bundle.yml) on `main`. Verify package, version code, signature, native ELF alignment and the ZIP alignment of generated APKs. Prepare the internal release and test a Play update over the public APK on a real device with saved history, settings, profiles and API keys. Verify those survive the upgrade and check fresh install and live Wi-Fi telemetry on phone and tablet.
3. In parallel, finish app-content review and confirm the optional AI and voice review route. [Google's sign-in guidance](https://support.google.com/googleplay/android-developer/answer/15748846) allows clear setup instructions for a user-set PIN. Document the local PIN and sample-data path. Use the owner's preferred reviewer-owned provider key only if it meets Google's access requirements. Confirm the account-deletion answer against the local profile implementation and review the screenshots against the signed candidate.
4. Recruit consenting Android users while release work progresses. Publish the closed test after setup is ready. Keep at least 12 testers opted in continuously for 14 days and collect real test feedback before applying for production access. Public production rollout requires explicit owner approval. See [Google's closed-testing requirements](https://support.google.com/googleplay/android-developer/answer/14151465).
