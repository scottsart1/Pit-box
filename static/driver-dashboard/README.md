# Driver Dashboard

Six production layouts share the app's current telemetry: Cockpit, Race Focus,
Battle, Endurance, My Layout and Phone. Open the **Driver Dashboard** tab in
Your Pit Box. The embedded view reuses the application's existing WebSocket.
A standalone live view is available at `/static/driver-dashboard/index.html?mode=live`.

Auto display profiles use the available browser viewport in CSS pixels, touch
capability and orientation. They respond to folding, rotation, browser zoom and
split screen. They do not claim to identify an exact hardware model. Manual
profiles cover Galaxy Fold cover/open, Galaxy Tab, iPad mini, iPad/Air, large
Pro/Air, laptops, desktop and ultrawide. A narrow viewport always remains usable.
Choosing a layout persists independently of the profile; Auto layout restores
responsive layout selection. Customize units, number size, contrast, accent,
optional cards, and the order of My Layout cards with keyboard buttons or drag.

Race mode keeps flags at the top, hides configuration and requests a screen
wake lock when the browser supports it. Escape or Exit race mode leaves it.
Mobile operating systems may release wake locks; keep the racing app visible.

Red flags, wrong-way alerts, safety cars and yellow flags take priority over
normal status. Missing or disconnected telemetry is prominently labelled and
racing values are removed after 3.5 seconds without updates. Car telemetry,
status and damage values require their corresponding recent packet groups.
Unavailable RPM or other unsupported values remain blank. Race gaps represent
race order, not physical on-track proximity. Rival comparisons use completed
last laps. Predictions use the stated delta reference, including recorded PBs.

## Public demo and offline download

The standalone public page uses clearly labelled sample data, with eleven race
control/connection scenarios. It does not connect to a racing computer or upload
race data. Its home-screen installation supports offline demo use after the
initial load. Live telemetry remains inside the racing app.

`python tools/build_driver_dashboard.py` builds a self-contained
`static/driver-dashboard/downloads/driver-dashboard.html` plus PWA icons using
only the Python standard library. The HTML can be downloaded and opened without
Node, Python, a local server or an internet connection. Browser storage policy
may limit persistence for locally opened files.

Run `node --test tests/driver_dashboard.test.mjs` for the display, data,
flag-priority, stale-state, escaping and all-layout scenario regressions.
Run `python -m pytest tests -q` for the application suite.

Design references: [SimHub](https://www.simhubdash.com/),
[SIMRacingApps](https://www.simracingapps.com/),
[Samsung foldable guidance](https://developer.samsung.com/one-ui/largescreen-and-foldable/designing_for_foldable.html),
and [MDN viewport concepts](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/CSSOM_view/Viewport_concepts).
