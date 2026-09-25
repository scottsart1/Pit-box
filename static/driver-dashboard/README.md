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

## Build a custom race view

Open **My Layout** to combine any of 24 widgets: speed/gear, lap/delta, relative
timing, lap times, race status, tyre surface/core/pressure/wear, fuel/energy,
fuel plan, ERS/aero, pedal/steering inputs, G forces, damage, power-unit wear,
track conditions, pit plan, lap consistency, position/progress, penalties,
car ahead/behind and engineer radio. The widget library supports search.
The six starting presets cover race essentials, qualifying, long stints,
racecraft, car telemetry and a minimal view.

Drag the handle at the top left of a card to reorder it using mouse, touch or
pen. Drag its lower-right corner to resize across one to three grid columns
and rows. Drag near a viewport edge to scroll through a longer layout. Focus
the move or resize handle and press arrow keys for the keyboard equivalent;
the visible move buttons and Width/Height menus provide the same controls.
Widths adapt to a two-column tablet or single-column phone without changing
the saved desktop size. Escape cancels a drag. The remainder of each card
keeps native touch scrolling.

Every change saves automatically in this browser. Undo restores the previous
20 edits during a visit. **Saved layouts** keeps up to eight named sets of
widgets and dimensions. Presets, removal, clearing and loading can all be
undone. The old six-module order is migrated when existing preferences load.
Global optional-card settings affect the preset race views; the custom
workspace always shows the widgets explicitly selected in its library.

Race mode keeps flags at the top, hides configuration and requests a screen
wake lock when the browser supports it. Escape or Exit race mode leaves it.
Mobile operating systems may release wake locks; keep the racing app visible.

Red flags, wrong-way alerts, safety cars and yellow flags take priority over
normal status. Missing or disconnected telemetry is prominently labelled and
racing values are removed after 3.5 seconds without updates. Car telemetry,
status and damage values require their corresponding recent packet groups.
Inputs and tyre readings require telemetry packet 6, G forces motion packet
0, conditions session packet 1, warnings/penalties lap packet 2, and 2026 aero
and overtake availability telemetry packet 16. Unsupported readings show —.
Unavailable RPM or other unsupported values remain blank. Race gaps represent
race order, not physical on-track proximity. Rival comparisons use completed
last laps. Predictions use the stated delta reference, including recorded PBs.

## Public demo and offline download

The standalone public page uses clearly labelled sample data, with eleven race
control/connection scenarios. It does not connect to a racing computer or upload
race data. Its home-screen installation supports offline demo use after the
initial load. Live telemetry remains inside the racing app.

The embedded dashboard also starts with labelled sample values when no current
simulator data exists, so its complete editor works without UDP or a running
simulator. It follows the first available live session unless a source was
explicitly chosen. Use **Data source** to switch between samples and the app’s
telemetry at any time. After live telemetry has been selected, a disconnection
never silently replaces racing values with simulated ones. A custom layout
remains editable with unavailable readings while disconnected. Sample and
recorded replay sources carry a persistent banner, including in Race mode.
Race flags and source labels continue updating during a drag; losing pointer
capture, changing source, a disconnect or leaving the window cancels the drag.

`python tools/build_driver_dashboard.py` builds a self-contained
`static/driver-dashboard/downloads/driver-dashboard.html` plus PWA icons using
only the Python standard library. The HTML can be downloaded and opened without
Node, Python, a local server or an internet connection. Browser storage policy
may limit persistence for locally opened files.

Run `node --test tests/driver_dashboard.test.mjs` for the display, data,
flag-priority, stale-state, escaping, widget/preset coverage, preference
migration, movement/resizing, source labelling and offline-bundle regressions.
Run `python -m pytest tests -q` for the application suite.

Design references: [SimHub](https://www.simhubdash.com/),
[SIMRacingApps](https://www.simracingapps.com/),
[Samsung foldable guidance](https://developer.samsung.com/one-ui/largescreen-and-foldable/designing_for_foldable.html),
and [MDN viewport concepts](https://developer.mozilla.org/en-US/docs/Web/CSS/Guides/CSSOM_view/Viewport_concepts).
