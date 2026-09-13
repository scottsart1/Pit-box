# Reddit update post: 4.9.0 to 4.9.6

Short on purpose. This is an update for people who already downloaded it, not
a launch. Detail goes in comments, one answer at a time, when somebody
actually asks.

The download figure comes from the Worker's `/installer` analytics; the email
figure is `SELECT COUNT(*) FROM subscribers` (13 at the time of writing).
Check both before posting and edit the first line to match.

---

## Titles

1. Update for the ~140 of you who downloaded my free F1 race engineer: only a handful left an email, so I'm posting it here
2. If you downloaded Your Pit Box and it crashed on first launch, that's fixed, plus what else changed
3. Six releases later: an update for anyone still running the free race engineer I posted about

Prefer 1, because it explains why the post exists in the title itself, which
is the thing most likely to keep it out of the self-promotion bucket.

---

## The post

About 140 of you have downloaded Your Pit Box. Thirteen left an email for
release news, so this is the only way I can reach the rest.

Six releases since then. The short version:

- **If it crashed right after the splash screen, that was a real bug.** Every
  fresh install before 4.9.1 hit it and there was no workaround. Run the
  current installer over the top and it starts, with sessions and settings
  kept.
- **Wet weather reads the track, not the sky.** It estimates how wet the
  surface actually is from your lap times and the rest of the field's, then
  tells you which lap to box on rather than just "box now".
- **Rain percentage was being read as how hard it's raining.** It's the chance
  of rain. Same sky, different tyre call, purely because the forecast number
  moved. Fixed.
- **Tyre degradation is fitted within a stint now.** The old fit could report
  *negative* degradation on runs that were genuinely losing two tenths a lap.
- **Only tyres you actually raced count towards the two-compound rule.** The
  grid softs were satisfying it before the lights went out, which is how a
  27-lap race got told to run no-stop on hards.
- There's also a Brutal mode toggle, if you want the engineer to swear at you
  when you're slow.

Same link, still free, no account, nothing to buy: yourpitbox.com

If something breaks or a call reads a race wrong, tell me, here or by reply.
Two real race weekends are the reason half that list exists.

If this kind of post isn't for you, just scroll past. Thanks to everyone who
gave it a go.

---

## Before posting

- **Check the sub's self-promotion rules.** Several want a mod's nod first,
  and an update post from an account that already posted a launch is exactly
  what those rules are aimed at. Ask a mod rather than find out afterwards.
- **Post it where the downloads came from**, and only there. One sub, one
  post. Cross-posting the same update reads as marketing even when it isn't.
- **Confirm the site is serving 4.9.6** before the post points anyone at it.
- **Do not re-pitch the app.** Everything above is addressed to people who
  already have it, and a features pitch bolted on is what gets it flagged.

## Reply bank

Two to four sentences each. Post one when it is asked, not before.

**"Did you use AI to write this?"**
Yes, heavily. Claude and GPT both, and the public commit history shows it.
That is a different question from what runs during a race: the strategy and
tyre maths are deterministic code with tests, and the model is only allowed
to read out numbers that code already computed. It cannot invent a lap time.

**"Is it actually free, what's the catch?"**
No catch. The download is the whole app, nothing is held back and nothing
expires. The only thing that costs money is your own AI provider usage,
billed to you by that provider. There is a coffee link on the site and that
is the entire business model.

**"Why do you need my email?"**
I don't. It's optional at the download and it only gets used for release news
like this. That is also why this post exists: most people skipped it, which
is fair enough.

**"Does it work on PC, or only PS5?"**
Both. It reads the standard F1 2026 UDP telemetry and runs on your Windows
PC, so it does not care whether the packets come from a console or the PC
version of the game.
