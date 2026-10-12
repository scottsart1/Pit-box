# Your Pit Box 5.5.0

5.5.0 shows the screens in French, German or Spanish, and stops a refusal in
those languages from being read as a pit-stop commitment.

## Screen language

- **Settings → Application → Screen language** offers English, Français,
  Deutsch and Español. It translates every page of the app, the first-run
  walkthrough and app tour, the stream overlay and the Android start-up
  screens. The page reloads when the language changes.
- The **Driver Dashboard** has its own **Language** choice in its settings,
  so a tablet beside the wheel can use a different language from the PC.
  Inside the app it follows the app's choice.
- When the browser's language is French, German or Spanish, the setup
  walkthrough offers to switch, until the offer is taken or dismissed.
- A stream overlay can take its own language from its address, for example
  `/overlay?lang=de`.
- Counts read correctly in every language ("1 lap", "2 laps").
- The Strategy page's what-if sends the lap and tyre directly, so it works
  whatever the screen language.
- The engineer's radio stays in English, as do the engineer's own written
  explanations, driver names and other text that comes from the game or
  the model.

The translations were machine-drafted, then reviewed and proofread by AI.
Native speakers have not reviewed them yet; reports of awkward or wrong
wording are welcome.

English screens are unchanged, with one exception: on a narrow phone a
Settings text field now drops below its label instead of squeezing the
label into a one-word column.

## Radio

- **Refusals in French, German and Spanish no longer lock a stop.** A driver
  mixing English radio words into another language, such as "Non, je ne veux
  pas box lap 18 pour les mediums", "Nein, ich will nicht box lap 18 auf
  Medium" or "No quiero box lap 18 con los mediums", had the refusal taken as
  a commitment and the stop locked. Those refusals now leave the strategy
  unchanged and go to the engineer for an answer. English commitments such as
  "no problem, box lap 18 for mediums" lock as before.

Windows remains an explicitly unsigned direct-download installer, and Android
keeps the existing signing identity.
