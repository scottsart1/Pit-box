# Setup reference research

The offline catalogue in `src/pitwall/setup_reference.py` contains attributed numeric starting points reviewed on **2 October 2026**. Publication and source-row dates are separate from that review date. These references have been checked against their sources, not driven or measured by Your PitBox, and carry no fastest-setup claim.

## Game compatibility

EA's [26 May 2026 features announcement](https://www.ea.com/games/f1/f1-25/news/f1-25-features-deep-dive) identifies the 2026 Season Pack as an expansion to F1 25, released on 3 June, with different cars and active aerodynamics. The catalogue therefore selects explicit **F1 26** spreadsheet tabs, not the authors' F1 25 or F1 25 Wet tabs. The telemetry packet's retained game branding does not make a 2025-car setup a verified 2026-car setup.

EA's [18 September 2026 driving guide](https://www.ea.com/games/f1/f1-25/news/f1-25-2026-season-edition-tips-and-tricks) also describes the changed car behaviour and recommends evaluating small setup changes. It supplies context, not the catalogue's circuit numbers.

## Sources and coverage

| Choice | Numeric source | Coverage | Source context |
| --- | --- | --- | --- |
| Stable race | [Matt212 — F1 26 Safe Setups](https://docs.google.com/spreadsheets/d/1mmFai7jDGYpZ2cc_PBk3PBrpUFgbjEzB7z-q5P2VlFE/edit#gid=673562173) | 24 calendar circuits, Imola, three reverse circuits | The tab describes easier control and supplies 50% race guidance alongside track/hotlap guides. Row dates, input device, team upgrades and equal-performance settings are unspecified. |
| More rotation | [Derp3339 — F1 26](https://docs.google.com/spreadsheets/d/1fUZKqMpARGJ1XEvsmGlOtN2_NVPOqLehPNiLH-YyYSI/edit#gid=2082870794) | 24 calendar circuits and Imola | League baselines with separate race/qualifying options. The creator reports predominantly wheel use and occasional controller testing; team/upgrades/equal-performance are unspecified. |

Matt212 identifies his own spreadsheet and intended audience in his [creator post](https://www.reddit.com/r/F1Game/comments/1tjlkm1/f1_25_26_setups_sheet_feedback_wanted/). The separate Advanced tab currently contains only four completed rows and is not mixed into the Stable choice. Each Stable reference links to its circuit guide.

Derp3339 confirms authorship, league intent and the meaning of the shorthand in his [4 August creator post](https://www.reddit.com/r/F1Game/comments/1vfjlse/f1_26_setup_spreadsheet/). Its 23 usable row creation dates range from 23 June to 2 September 2026. They are stored with `date_kind=creator_row_creation_date`; they do not establish a last-update date. China is marked as author theory (`status=adapted`, `verification=author_theory`); Monaco has no usable date. No row is presented as independently validated.

## Numeric interpretation

Only the 20 explicitly sourced adjustable fields are included. Engine braking, ballast and fuel load are not fabricated from legacy telemetry fields.

- Matt's tyre order is **front right, front left, rear right, rear left**. This matters for asymmetric pressures. The exact China row is corroborated by [MeetrsBuzz's individually labelled 2026 upload](https://www.f1laps.com/f1-26/setups/china/34be4732-6a34-4c94-ba01-4d48226e41dd/): FR 25.4, FL 26.9, RR 21.9, RL 22.6. That upload is a wheel time-trial result dated 11 September and is used only to corroborate field order, not race suitability. Two source cells, Las Vegas and reverse Austria, omit one slash; all four numeric pressures remain unambiguous and the normalization is disclosed.
- Matt's brake order is bias/pressure; Derp's is pressure/bias. Both are mapped to named fields.
- Derp's `LLLL` means all four geometry controls fully left, according to the author. The numeric interpretation is camber −3.50/−2.00 and toe 0.00/0.10. A [current 2026 China creator guide](https://www.rickf1racing.com/p/setud-circuito-de-china-2026.html) corroborates the camber endpoints and minimum rear toe. This is a documented notation interpretation, not a claim of independently viewing a game slider. The original `LLLL` remains in `source_notation`.
- Derp's race and qualifying values stay separate. Two axle pressure values apply to both wheels on that axle. Eight brake-bias ranges use their published lower endpoint, with `status=adapted`, the original range and an explanation. No brake pressure of 100 is inferred where the source gives one shared lower value.
- Hybrid uses race values. Matt supplies no separate qualifying values in the Safe tab, so its qualifying choice remains the unchanged race reference. Derp's qualifying references retain the creator's parc-ferme caution about race wings and brake pressure.

## Gaps and verification

No applicable complete published 2026 wet catalogue was verified. Wet/intermediate and unknown-condition requests return `status=unavailable` with no setup; dry numbers are not relabelled as wet. Unsupported circuits or missing styles also return no substitute, including Derp's missing reverse layouts. The authors' distinct setups are never averaged together.

The focused tests cover all 24 calendar circuits, extra-track identity, asymmetric wheel mapping, explicit race/qualifying differences, theory/range/date disclosure, unavailable cases and isolation of returned data. Source facts do not guarantee performance for a particular driver, car development state or later handling patch.

For reproducibility, public CSV exports fetched during the review had SHA-256 digests:

| Tab | SHA-256 |
| --- | --- |
| Matt212 F1 26 Safe Setups, gid 673562173 | `976069c9c4ac61838c90954cfd3bebcd8a5c64815017b04204e7e2d8808d4cde` |
| Derp3339 F1 26, gid 2082870794 | `f5691329d05db2fc4386efbaf6557f11ca78c97bf33a20fe7bc6dae90b2088ef` |
