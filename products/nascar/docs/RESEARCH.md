# Evidence and boundaries

Reviewed 2026-10-01. These are references, not bundled copies of their content.

- [NASCAR 26](https://nascar26.com/) and its
  [FAQ](https://nascar26.com/faq/): standalone PC, PS5 and Xbox game; four series;
  dynamic track and revised tire model. The FAQ does not specify an external
  telemetry API. Its publisher being iRacing does not establish SDK compatibility.
- [September 15 release notes](https://nascar26.com/release-notes-sept-15-2026/):
  stage-ending AI pit behavior and online pit options are material to strategy.
- [NASCAR overtime procedure](https://www.nascar.com/news-media/2017/08/02/nascar-overtime-line-start-finish-line/):
  reserve fuel for possible additional attempts; never assume exactly two extra
  laps or infer game race control from real-world rules alone.
- [NASCAR pit-road penalty card](https://rbfiles.ndms.nascar.com/2024/01/2024-PIT-ROAD-PENALTY-CARD_REVA.pdf):
  pit closure and eligibility matter. Historical rule context, not a claim that
  all these penalties are implemented identically in NASCAR 26.
- [iRacing Next Gen manual](https://s100.iracing.com/wp-content/uploads/2024/03/NASCAR-NextGen-Cars-Manual-V2.pdf)
  and [crossweight explanation](https://www.iracing.com/commodores-garage-19-crossweight/):
  general stock-car handling principles. NASCAR 26 garage availability, ranges
  and direction must be confirmed in the selected car; no copied paid setups.
- [Recorded NASCAR 26 gameplay](https://www.youtube.com/watch?v=3oOJMrMxrXo&t=1410s),
  Jeff Favignano, *NASCAR 26 Career Mode and Wheel Gameplay*: at 23:30, the visible
  overall lap is 17, position 3 and speed 174 mph; at 25:52 they are 21, 1 and
  171 mph. The overall and stage lap counters are separate. These two frames
  validate selected digits only; they do not establish current game behavior,
  fuel/tire units, live capture, race control or latency. Source frames are not
  redistributed with the app or repository.
- [RapidOCR](https://github.com/RapidAI/RapidOCR) and its
  [usage documentation](https://rapidai.github.io/RapidOCRDocs/main/en/install_usage/rapidocr/usage/):
  bundled local recognition of calibrated single-line crops using CPU inference.
  The original Windows OCR reader failed the stylized lap and position digits
  in the recorded frames. RapidOCR 3.9.2 with PP-OCRv6 recognition reads those
  tightly cropped values; full-frame detection did not. Model files are pinned
  by hash and must already exist, so runtime cannot download replacements.
  Cropped HUD evidence is not full telemetry and cannot provide a collision spotter.
- [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling):
  bounded read-only tools and structured arguments. The conversational engineer
  can inspect laps and compare scenarios; missing data remains explicit.

Game settings are authoritative: users enter race length, stage boundaries,
overtime reserve, tire/fuel multipliers and measured service times. Track types
provide driving context, never hardcoded race distances or unmeasured fuel burn.
