# Your Pit Box 5.7.1

5.7.1 is an Android release (revision 43) that lays the app out for a phone.
Windows stays on 5.7.0; on a computer the screens are unchanged.

## On a phone

- **More of the screen is the race.** The header is one row: the brand over
  the session and track, with Dashboard and Quit as icon buttons. Telemetry
  and radio state stay on the status strip at the bottom. On a 390×844 phone
  the page now has 699 px instead of 617.
- **Tabs you can read.** The tabs are one strip that scrolls sideways at a
  readable size and keeps the open tab in view. Analysis's views use the same
  strip instead of wrapping onto three rows.
- **DRIVE reads in race order.** Lap and position first, then the strategy
  call, the tyres (2×2), fuel and weather, the radio and the box to ask the
  engineer, then the timing tower, one line per car. The lap tools and driver
  control follow, and the radio, push-to-talk and provider setup cards come
  last. Cards keep the order chosen under Dashboard within each group. The
  same order applies on any screen narrow enough for one column, including
  a phone on its side.
- **Controls that fit.** Text fields take the width and buttons size to their
  label, so the box-lap field is no longer squeezed to one letter. Check
  boxes sit beside their labels at a full-size target.
- **Settings, Library and Strategy.** A setting's choice or value goes full
  width under its description. Each saved session in the Library is a card
  with its actions on screen. Strategy's decision log puts the lap above the
  call.
- **On its side.** With the phone in landscape, the header and the tabs share
  one row.

## Compatibility

The database schema is unchanged (5700). 5.7.0 and 5.7.1 open the same
history, and history transfers between Windows 5.7.0 and Android 5.7.1 work
as before. Install the APK over 5.7.0; saved settings and sessions are kept.

## For operators

The Android release is built, signed and uploaded by the "Release Android"
workflow, and the download switches when a commit on `main` points the Worker
and the website at the new APK. See `docs/release-plan-5.7.1.md`.
