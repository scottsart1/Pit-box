# Release-news email: 4.9.0 to 4.9.6

Draft for the update list (`subscribers` in the activation Worker's D1
database), addresses left at the Download button by people who installed
Your Pit Box. Send as plain text or light HTML; every link below is public.

---

**Subject:** Your Pit Box 4.9.6: wet-weather strategy, brutal mode, and a first-run fix

Hi,

Six releases have gone out since 4.9.0. Here is what is in them.

**If your install never got past the splash screen, that was a bug.** Every
fresh install before 4.9.1 crashed there, with no workaround. Run the current
installer over the top and it starts.

**Wet weather now reads the track, not the sky.** The engineer estimates how
wet the surface actually is, using the declared weather, your lap times and
the rest of the field's, and whatever you tell it on the radio. That estimate
is projected over the laps you have left. It knows a spin from a shower, names
the lap to box on instead of "box now", and leaves you out when the rain lands
after the flag.

**Rain percentage is a chance, not an intensity.** It was being read as how
hard the rain is falling, so the same sky produced different tyre calls purely
because the forecast number moved.

**Brutal mode.** One toggle on DRIVE and the engineer stops being polite:
bottom five, or 0.4s off the target lap, and you hear about it, always with
the number that earned it. Safety, flag and pit calls are unchanged.

**Tyre learning you can trust.** Degradation is fitted within a stint now.
The old fit could report *negative* degradation on runs genuinely losing
0.20s a lap, and only clean laps teach it anything.

**Race-weekend fixes.** Only tyres you actually raced count towards the
two-compound rule. The grid softs were satisfying it before the lights went
out. The spoken call stops flapping between plans, stated tyre preferences
actually lock, and nothing is called after you retire.

**Download:** https://yourpitbox.com. Run it over your existing install and
your sessions and settings are kept. Still free, no code, no account.

Thank you for installing it and for sticking with it. **If something breaks or
a call reads a race wrong, just reply to this email.** Track, session and what
you expected is plenty to go on. Two real race weekends are the reason 4.9.3
exists, so it works.

Scott

*I build this for fun. No financial motive: it is free, nothing is held back,
and there is nothing to buy. If you get value out of it, a coffee is welcome.
PayPal: https://paypal.me/sarthakvij298 · Venmo: @scott-v-sv. Entirely
optional, and it changes nothing about what you download.*

---

## Before sending

- Confirm the site is serving 4.9.6 (`/installer-info`) so the download the
  email points at is the build it describes.
- Send to the update list only. Addresses were given for release news, so
  keep the frequency to actual releases.
- Send as BCC, or one message per address. The list should not be visible
  to the people on it.
