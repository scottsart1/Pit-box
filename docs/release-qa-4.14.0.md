# Your Pit Box 4.14.0 — Dashboard editor and immersive Android

Verification in progress, 25 September 2026. This document records completed
checks separately from work still pending. It does not establish production
publication or final installed-device acceptance.

## Scope

- Driver Dashboard has a 24-widget library, six starting presets, add/remove,
  reorder and resize controls, undo, autosave and up to eight named layouts.
  Pointer handles support touch and mouse; arrow keys and size menus provide
  keyboard alternatives. Disconnected layouts remain editable.
- Sample data works without simulator telemetry and remains explicitly
  labelled, including in Race mode. Live, sample and replay provenance are
  distinguished. Packet-family freshness gates optional readings; missing
  values remain unavailable. Gesture cancellation handles lost capture,
  blur and pagehide, while flags and source labels continue updating during
  a drag. The standalone offline HTML and service worker are rebuilt.
- DRIVE restores tyre pace insight beside tyre wear and in its pace strip.
  Measured fuel-corrected trends, estimates for the fitted compound, learning,
  wet-condition uncertainty and unavailable telemetry have distinct wording.
  Negative trends and compound changes are handled without inventing values.
  Existing backend tyre-learning exclusions remain intact.
- Android uses native immersive status/navigation-bar hiding on launch,
  resume and focus return. Edge swipes reveal transient bars; cutout,
  caption and keyboard insets preserve usable controls. Keyboard dismissal
  restores immersive mode. The candidate is Android revision 27.
- The website and packaged application use the same dashboard source.
  Versioned application asset references are updated to 4.14.0.

## Verified checks to date

| Check | Result |
| --- | --- |
| `node --test tests/driver_dashboard.test.mjs` | 34 passed, including widget/preset validation, migration, saved layouts, immutable reorder/resize, freshness, provenance, stale editing, escaping, accessible markup and offline-bundle parsing. |
| `node --test tests/drive_degradation.test.mjs` | 9 passed, including DRIVE renderer integration, wet uncertainty, stale/offline state, compound changes, signed slopes and fitted-stint estimate selection. |
| `pytest -q tests/test_tyre_learning.py tests/test_strategy_weather_evidence.py` | 45 passed; backend learning safeguards remain intact. |
| Shipped JavaScript | `node --check` passed for dashboard code; DRIVE inline script parsed successfully. |
| Offline build | `python tools/build_driver_dashboard.py` succeeded after the output-writing fix; generated HTML is current and the service-worker version is 4.14.0. |
| Android project checks | `pytest distribution/tests/test_android_project.py -q`: 30 passed, including three InsetsPolicy parser scenarios. |
| Native Java compile | `:app:compileDebugJavaWithJavac`: BUILD SUCCESSFUL, 18 tasks. Python packaging tasks were excluded; this is not a full APK build. |

The initial application-wide run had 1,585 passes and one stale asset-version
assertion. Asset references were corrected; the targeted rerun passed all
11 tests. The initial distribution run had 314 passes, one skip and two stale
website assertions; those assertions were updated, with final distribution
verification still pending at this checkpoint.

Independent code review found incorrect packet families for active
aero/overtake and penalty readings. These now use packet 16 and packet 2,
respectively. The review also prompted explicit cancellation of interrupted
pointer gestures and continued flag/source updates while editing.

## Private real-recording adapter check

The current dashboard adapter was run offline against an existing private
recorded state, using its latest packet timestamp as the freshness clock.
Direct and converted values were checked against the source for driving
inputs, G forces, tyre core/pressure/wear, fuel, energy, damage, component
wear, weather, progress and penalties. Missing packet-family gates,
disconnected/stale states and all 24 widget renderers passed. No sample
numbers were substituted.

RPM intentionally remains unavailable: the backend's live state does not
expose `engine_rpm`, although archive telemetry can contain it. This release
does not add that backend field, and the widget describes RPM as available
only when supplied.

Only aggregate results are recorded here. The recording and detailed
evidence remain outside the repository in the private `release-4.14.0`
directory; `dashboard-recording-adapter-check.json` contains the adapter
receipt without personal telemetry values. No private recording was copied
into the repository or website.

## Pending release and device checks

- Final browser checks for real touch/pointer editing, keyboard focus,
  persistence, responsive layouts, sample/live switching, standalone offline
  behavior and the embedded dashboard; root-agent browser QA is in progress.
- Final website/distribution regression results and deployment validation.
- Complete Windows/Android candidate builds and packaged-runtime checks.
- Native fullscreen runtime checks on the candidate: launch, restart,
  foreground return, edge swipe/re-hide and text-entry keyboard restoration.
  These cases are added to emulator smoke coverage but have not yet been
  executed on this candidate. Java compilation and parser tests alone do not
  establish device behavior.
- Final signed Android candidate verification/install acceptance, production
  artifact hashes and public download readbacks, deployment identifiers and
  update-service verification.

Physical console/Wi-Fi/Bluetooth/audio endurance is not established by the
source, adapter or compile checks above. Production and device evidence must
be appended before this document is described as a completed release record.
