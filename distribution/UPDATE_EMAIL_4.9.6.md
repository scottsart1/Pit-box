# Release-news email: 4.9.0 → 4.9.6

Draft for the update list (`subscribers` in the activation Worker's D1
database) — addresses left at the Download button by people who installed
Your Pit Box. Send as plain text or light HTML; every link below is public.

---

**Subject:** Your Pit Box 4.9.6 — wet-weather strategy, brutal mode, and a first-run crash fix

Hi,

You downloaded Your Pit Box and left your address for release news. This is
that email. Six releases have gone out since 4.9.0 on 1 September, and the
current build is **4.9.6**.

**First, the one that matters most: if your install never got past the splash
screen, that was a bug, and it is fixed.** Every fresh install before 4.9.1
crashed straight after the splash, before the welcome window ever opened.
There was no workaround for an affected install — the fix is to run the
current installer over the top of it. Your sessions and settings are kept.

Here is what the six releases added.

**The engineer reads the track in the wet, not the sky.**
The rain call used to be as blunt as "it is raining and you are on slicks, so
box". A track lags the weather in both directions — rain starts and the
surface is still quick on slicks for a lap or two; rain stops and it stays wet
long after the cloud has gone. The engineer now estimates how wet the surface
actually is, from four things at once: the declared conditions, a surface
model that soaks fast and dries slowly, your lap times and the whole field's
measured against their dry pace, and whatever you tell it on the radio. That
estimate is projected over the laps you actually have left, and every compound
is priced against it.

In practice that means it knows a spin from a shower (a lap that loses time in
one sector is a mistake, not weather, and is thrown away). It tells you which
lap to box on rather than just "box now". It leaves you out when the shower
arrives after the flag. When the call is too close to make for you, it asks
what only you can see — standing water, a dry line coming through — instead of
committing a stop to a coin toss. And full wets are treated as the
standing-water tyre they really are, rather than being reached for every time
the sky says heavy rain. DRIVE's weather readout now shows the track state and
which way it is heading, next to the sky and the rain percentage.

**Rain percentage is a chance, not an intensity.**
The game's rain percentage is the probability that it rains, not how hard it
is falling. It was being read as an intensity, so the same sky produced a
different tyre call purely because the forecast number moved. Observation and
forecast are now separate channels, and future weather is priced as scenarios:
heavy rain at 20% is a heavy-rain outcome weighted at 20%, not a fifth of a
wet track. Being on the wrong tyre does not hurt in proportion to how wet it
is, so averaging the weather first and pricing it after was the wrong order.

**Brutal mode.**
A toggle on DRIVE's Proactive engineer card, and on the Settings page. Switch
it on and the engineer stops being polite: if you are classified in the bottom
five, or lap more than 0.4s off the target, you hear about it — directly, with
swearing, and always carrying the number that earned it. Tone is the only
thing that changes: safety, flag, penalty and pit calls still say exactly what
the data says, and it is aimed at the driving, never at you. Saying "stop
roasting me" on the radio silences it, or just flick the toggle back.

**Tyre learning you can actually trust.**
Degradation is now fitted within a single stint rather than across a session.
Fuel burn and tyre age move together inside a stint, and the old fit could not
tell them apart — it could return a *negative* degradation slope for runs that
were genuinely losing 0.20s a lap. Only clean laps teach it anything now: time
trials and qualifying are excluded, along with invalid laps, pit laps, and
anything under a yellow, red, safety car or VSC. Wear needs real evidence
rather than one packet. And confidence is graded on the weakest stint, so
eight measured laps on mediums no longer make an untested hard final stint
"high confidence" — the Strategy screen shows how many laps are behind each
number and where the final stint's estimates came from.

**What two real race weekends fixed.**
Suzuka and Sakhir exposed a set of things that 4.9.3 put right:

- Only tyres you have actually raced count towards the two-compound rule. The
  qualifying softs the car sat on before the start were satisfying the
  mandatory change before the lights had even gone out — which is how a
  27-lap race got recommended a no-stop on hards.
- The spoken call stops flapping. A faster plan has to stay the faster plan
  for 20 seconds before the radio moves to it. At Sakhir it was swinging
  between plans several times a lap.
- "I would actually prefer the soft two-stop" now genuinely locks that
  preference. It used to get a "copy" and lock nothing.
- The pre-race discussion closes when the race starts, so a mid-race question
  about hards is answered about the race you are in.
- Nothing is called after you retire or are classified. Fuel, tyre, penalty
  and damage calls wait until you are out of the garage and moving, and calls
  undone by a flashback are not spoken.
- Queued radio is tighter: a strategy call whose plan has since moved on is
  dropped rather than spoken stale, and a call that cannot be spoken no longer
  blocks the next one in the queue.

**Getting it:** https://yourpitbox.com — the Download button gives you the
complete application. Run the installer over your existing install; your
sessions and settings are kept. Still free, still no code, no account, no
subscription, nothing held back and nothing that expires. The only thing that
ever costs money is your own AI provider usage, billed to you by that
provider.

Thank you for installing it, and for staying on this list. A one-person
project lives or dies on whether anyone is actually racing with it, and you
are the reason there is a 4.9.6 at all.

**If anything breaks, just reply to this email.** Bugs, a call that read a
race wrong, a number that looks off, anything the engineer says that makes no
sense — reply and tell me. Track, session type and what you expected versus
what it said is plenty to go on. Everything above came out of exactly that
kind of report: two real race weekends are the reason 4.9.3 exists. I read
every reply.

If you have raced with it and want to say so publicly, there are reviews on
the site now: https://yourpitbox.com/#reviews

— Scott

*I build this for fun. There is no financial motive behind it — the app is
free, nothing is held back for a paid tier, and I am not selling anything. If
you like it and it earns you a position, you can buy me a coffee:*

- *PayPal: https://paypal.me/sarthakvij298*
- *Venmo: @scott-v-sv*

*Entirely optional, any amount. It unlocks nothing and changes nothing about
what you download.*

---

## Before sending

- Confirm the site is serving 4.9.6 (`/installer-info`) so the download the
  email points at is the build it describes.
- Send to the update list only. Addresses were given for release news, so
  keep the frequency to actual releases.
- Send as BCC, or one message per address — the list should not be visible
  to the people on it.
