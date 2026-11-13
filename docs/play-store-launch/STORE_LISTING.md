# Google Play listing draft

- App: **Your Pit Box**
- Application ID: `com.yourpitbox.app`
- Price: free
- Suggested category: Sports
- Support email: `vale.scott00@gmail.com`
- Website: `https://yourpitbox.com`
- Privacy policy: `https://yourpitbox.com/privacy.html` (live, verified 13 November 2026; enter in Play Console)

## Short description

Live racing telemetry, strategy, lap analysis and optional AI radio.

## Full description

Your Pit Box is an independent companion for supported F1 racing game telemetry. Run it on an Android phone or tablet beside your game. It receives the game's UDP telemetry over your local network, calculates race and car information on the device, and keeps your sessions for later review.

See live timing, tyre and fuel information, pace traces, race gaps and strategy options. Review saved sessions, compare laps, explore setup changes and adjust your dashboard for the screen you use. Built-in sample data lets you explore the interface before connecting a game.

Create a local driver profile so several people can use one device with separate history, settings and provider keys. Choose a four-digit PIN for an app lock, or an email address as a local identifier. Email-only access does not verify ownership of the address. Profiles are local to the device; they are not cloud accounts or automatic backups.

Core telemetry, strategy and analysis are free and do not require an AI key. Optional model-backed answers require an API key from a supported provider. Spoken radio uses OpenAI services and your own OpenAI API key. Your provider may charge you separately for usage. Voice features also need internet, microphone permission and audio output. You control when to use them.

For live game data, connect your Android device and the supported game over a network that can deliver its telemetry packets, then set the game's UDP destination to this device. Features and results depend on the telemetry the game actually sends. You can also inspect sample data without a game connection.

Your Pit Box is independently developed and is not affiliated with, endorsed by or connected to Formula 1, the FIA, Electronic Arts or Codemasters. Their marks belong to their owners.

## Release notes for first Play test

First Google Play test build of Your Pit Box 5.8.0. Includes local driver profiles, live telemetry and strategy, saved-session analysis, and optional voice and AI features. Android revision 45 is a Play preparation build; the production release notes must reflect the exact verified bundle.

## Reviewer access

**Draft only. Do not submit this section as Play's access declaration yet.** Google's [review guidance](https://support.google.com/googleplay/android-developer/answer/15748846) allows instructions for a user-set PIN, so the local profile can be explained with exact steps. Confirm whether optional AI/voice requires a reusable provider test key before attesting to full access.

1. On a clean installation, enter `Play Review` as the driver name, choose the four-digit PIN method, enter `2468` twice, and tap Create. This makes a profile stored only on the review device, without email verification or a server account. On later starts, open `Play Review` with `2468`. The PIN is review guidance for a fresh install, not a credential for any existing user profile.
2. Open the dashboard and choose sample data to inspect Drive, strategy and analysis without racing hardware. The sample feed is labeled as sample data.
3. Live reception needs a supported game sending F1 2026 UDP telemetry to the Android device on the same reachable network. This hardware/game setup is optional for reviewing the interface and local sample flow.
4. AI reasoning and spoken radio need the reviewer's own provider API key and may create provider charges. Do not require those services for core telemetry review.
5. Profile openings send pseudonymous sign-in reports to the developer service. Optional usage reports stay off until enabled in Settings. The privacy policy explains both.

## Artwork candidates

The source and limitations of the prepared phone and tablet screenshots, icon and feature graphic are recorded in `ASSETS.md`. The phone screenshots show 5.7.1, while the intended Play candidate is 5.8.0. Refresh screenshots after candidate validation if the interface changes materially. The third phone image is a backup because its telemetry status could confuse a new user.

## Open listing items

- Review the candidate Android screenshots in `assets/` against the final signed build. The supplied 5.7.0 desktop-sized screenshots show real features but must not be labeled Android phone captures.
- Verify whether the game compatibility name in the final description exactly matches the current supported release.
- Confirm support email and public policy page, final country distribution and category before submitting the listing.
