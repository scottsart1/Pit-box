# Your Pit Box 5.8.0

5.8.0 is a release for Android (revision 44) and Windows. It adds driver
profiles: one device can be shared by several drivers, each with their own
history, settings and keys.

## Driver profiles

- **Your existing setup becomes your profile.** The first launch after
  updating opens *Set up your driver profile*. Enter a name and choose
  **Email or PIN**; a PIN is four digits, typed twice. Everything already on
  the device (race history, settings, dashboard layout, API keys, pairings)
  is attached to this first profile.
- **Every launch opens on your profile.** With one profile, *Welcome back*
  goes straight to your email or PIN. With several, *Choose your driver*
  lists them, with **+ New driver** to add another.
- **Each driver is separate.** A new driver starts with their own race
  history, settings, dashboard layout, API keys, captures and Wi-Fi history
  transfer pairings. Wi-Fi transfers copy only the open driver's history.
- **Switch driver.** CONNECTION names the open driver and has **Switch
  driver**. The session is saved first. On Android the app closes; reopen it
  and choose the driver. On a computer the app restarts on the profile
  screen in the same browser tab.
- **PIN and email.** Five wrong PINs lock that profile for 15 minutes, and
  twenty in a day lock it until the next day. A forgotten PIN can't be reset.
  An email address isn't verified and no mail is sent: it identifies the
  profile, and anyone who knows it can open that profile, so choose a PIN to
  lock a profile. A device holds up to 20 profiles.

## Faster to the dashboard

The libraries every driver shares now load while the profile screen is open,
so after the PIN only that driver's own settings, keys and history are left to
load. On Android the opening animation plays while the app starts.

## Compatibility

The database schema is unchanged (5700). 5.7.x and 5.8.0 open the same
history, and history transfers between them work as before. Install over
5.7.0 (Windows) or 5.7.1 (Android); everything already saved is kept and
attached to the first profile.

## For operators

See `docs/release-plan-5.8.0.md` for the release steps and
`docs/driver-profiles.md` for how profiles are stored and reported.
