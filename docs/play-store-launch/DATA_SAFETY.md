# Data safety and Play declarations — draft for factual review

Use this as an evidence checklist while answering the actual Play Console form. Do not mark the form complete until the candidate AAB and the live privacy policy match. Google's definitions are in [Data safety guidance](https://support.google.com/googleplay/android-developer/answer/10787469) and its [account deletion guidance](https://support.google.com/googleplay/android-developer/answer/13327111).

## Off-device flows seen in the repository

| Flow | Data sent | Required? | Recipient / retention |
| --- | --- | --- | --- |
| Driver-profile opening | Random profile ID, device credential, event ID, time, app version | Yes, after profile opening; queued offline | Developer's Cloudflare Worker; credential hashed server-side, sign-in events retained 180 days |
| Optional usage reporting | Random installation ID, platform/version, daily yes/no feature-use flags | No, off until opt-in | Developer's Cloudflare Worker; daily events retained 90 days; installation metadata expires after 90 inactive days |
| Model-backed answers | User question, compact session context, selected computed results, provider API key | Only when feature used | User-selected AI provider; provider retention is governed by that provider |
| Cloud transcription / Realtime voice | Audio clips or live microphone stream and provider API key | Only when feature used | OpenAI; provider retention is governed by OpenAI |
| Update checks | Platform and request metadata | Default on; switchable | Developer's update service; verify server log retention |
| Manual history transfer / UDP forwarding | User-selected saved sessions or live game packets | Only when configured by user | Paired device or configured local-network destination |

Raw session databases, API keys, local profile names, PINs and email identifiers are not included in developer sign-in or optional usage reports. The app's local key vault and first-profile upgrade behavior are described in `docs/driver-profiles.md`.

## Conservative form mapping

- **Does the app collect or share user data?** Yes. Pseudonymous collection remains collection under Google's guidance. Do not choose “No data collected.”
- **Device or other IDs:** required random profile/device identifiers for sign-in reporting; optional random installation ID for usage statistics. Purpose: analytics and app functionality/account management as the form defines them. The developer service hashes credentials or installation IDs before storage, but they are sent off-device first.
- **App activity / other actions:** required profile-open event and optional daily feature-use flags. Purpose: analytics.
- **Audio:** optional microphone audio for cloud transcription or Realtime radio. Purpose: app functionality. Sent to OpenAI only when the user starts the feature.
- **Other user-generated content:** optional typed or spoken requests and selected race context sent to the configured AI provider for answers. The exact Play subcategory and whether the provider transfer qualifies for Google's user-initiated sharing exception need review in the form; do not omit the collection.
- **Personal info / email:** the app's local email-profile field is documented as staying on-device in the sign-in path. Verify all backend and WebView paths before selecting “not collected.” The separate website newsletter is not automatically app collection.
- **Account deletion:** Google's [app-account definition](https://support.google.com/googleplay/android-developer/answer/13327111) describes a user-facing identity serving the user across apps or devices and explicitly exempts accounts created and operated offline. The implemented driver profile is local to one installation: no cloud login, sync, or recovery exists; the backend credential authenticates only pseudonymous sign-in reports and does not let a user access a profile elsewhere. On that evidence, the Play account-deletion requirement appears not to apply. This is an inference, not a Google ruling. The owner should confirm that no unpublished cloud-account feature changes this fact before answering “no app account creation.” The app has no individual in-app profile deletion control or server record deletion path; do not claim it does. The privacy policy states how to clear device data and request help with developer-held data.
- **Encryption in transit:** known developer and provider endpoints use HTTPS, but inspect user-configurable compatible endpoints and local transfers before checking a universal encryption claim.
- **Ads:** no ad SDK or advertising behavior found in the Android module; verify against the final bundle. **Target audience:** adult/general racing-game companion, not designed for children; answer Play's age questions from intended audience, not an invented age rating.

## Other app-content declarations

- **Foreground services:** manifest declares `connectedDevice|microphone` and corresponding permissions. Explain continuous user-visible telemetry reception and optional voice capture; record a genuine app demo if Play requests one. Verify the user's stop control and runtime microphone prompt in the release build. See [Google's foreground-service guidance](https://support.google.com/googleplay/android-developer/answer/16965181).
- **Content rating:** complete the IARC questionnaire from actual content. The app displays game/racing data and allows optional AI conversations; do not guess an age rating in advance.
- **Sign-in details:** provide the reviewer steps in `STORE_LISTING.md`, including sample data and the lack of email verification.
- **Branding:** listing must identify the product as independent and avoid official Formula 1/FIA/EA/Codemasters branding or endorsement claims.
