# Driver Dashboard design prototypes

Six interactive UI concepts for a proposed **Driver Dashboard** tab in Your Pit Box. The design board is self-contained and uses scripted race data. It does not connect to telemetry, call an AI provider, or change the existing Drive screen.

Open `/static/driver-dashboard/index.html` on the running Your Pit Box server. To preview without starting the application, serve the repository with `python -m http.server 4173 --bind 127.0.0.1`, then open `http://127.0.0.1:4173/static/driver-dashboard/`.

| Concept | Main purpose | Direct link |
| --- | --- | --- |
| Cockpit | A fixed instrument cluster with gear, speed, delta and immediate rivals | `#cockpit` |
| Focus | Large delta and minimal distractions | `#focus` |
| Battle | Relative timing tower and detailed ahead/behind pace comparison | `#battle` |
| Endurance | Pit window, fuel budget, tyre condition and stint consistency | `#endurance` |
| Modular | Reorderable cards and individually configurable supporting modules | `#modular` |
| Portrait | A narrow second-screen layout for a phone or tablet | `#portrait` |

## Interactions

- Switch between eleven sample race-control states: green, yellow, double yellow, safety car, virtual safety car, blue, red, drive-through, pit lane, chequered, and telemetry lost.
- Play or pause a ten-second loop of sample speed, relative gaps and lap timing. The loop updates text without rebuilding controls or repeating the flag announcement.
- Set speed units, number size, contrast, interface accent, and visibility of supporting data. Flags and immediate rivals stay visible. Preferences are saved only in this browser, with invalid stored values falling back to defaults.
- Use arrow buttons or drag handles to reorder Modular cards. The arrows support keyboard and touch input.
- Enter Race mode to hide the design and customization controls. Use the visible Exit button or Escape to return. Race mode pauses the sample loop for a steady layout comparison.
- Lost telemetry replaces all readouts with an explicit unavailable state. It does not retain sample race values as if they were current.

## Design basis

The layouts extend the application's Segoe UI typography, dark `#070b0f` canvas, `#121b24` cards, `#263542` borders, and established green/blue/red/amber status colors. Essential numbers are larger and use tabular numerals. Flags use written instructions as well as color, and race-mode layout positions stay fixed.

The app navigation at the top is a context mockup. It is not wired into the existing application's navigation. The standalone page is served by the application's existing static-file mount, so no backend changes are necessary to review the designs.

Inspiration, reviewed September 17, 2026:

- [Lovely Dashboard](https://store.lsr.gg/pages/lovely-dashboard): instrument hierarchy, focused modules, and different display formats.
- [RaceLab](https://racelab.app/): relative timing, independent flag alerts, and layout customization.
- [SimHub Dash Studio](https://github.com/SHWotever/SimHub/wiki/Dash-Studio-Overlays): movable overlay surfaces.

These are original layouts; no dashboard assets or source code from those projects are included. Driver names, numbers, timings, flags, and engineering messages are illustrative samples.

## Implementation boundary

This is a design-review prototype, not a racing-ready telemetry integration. Production integration still needs a selected layout, a navigation entry, packet-backed field mapping and freshness, race-control priority rules, and testing with live telemetry and the intended mounted display. The optional preference controls affect only modules that exist in a given concept. In Endurance, fuel visibility also covers the fuel-budget card; tyre visibility covers the tyre-condition card.

No build step, new dependencies, or installation is required. The HTML, CSS, and JavaScript are contained in this directory.
