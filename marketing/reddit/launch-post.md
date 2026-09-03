# Reddit launch post: Your Pit Box

Short on purpose. Nobody reads a wall of text on Reddit, and on a launch
post length itself reads as marketing. The post below is about 165 words.
Everything else in this file is a reply bank: detail goes in comments, one
answer at a time, when somebody actually asks.

The post says up front that the project was built with a heavy hand of AI,
Claude and GPT both. That is deliberate. The commit history is public and
shows it, so the choice is between saying it first and being caught at it
later, and only one of those survives a racing subreddit. Conceding it also
sharpens the claim that actually matters: how the source was written is a
different question from whether the app can invent a number at runtime.

Best format: post the 45-second captioned video, put the text in the first
comment. Where the sub wants text posts, use the text and link the video.

Check the sub's self-promotion rules first. Several want a mod's nod.

---

## Titles

1. I made a free race engineer for F1 25. It reads the game's telemetry and talks to you on the radio.
2. F1 25 broadcasts telemetry for all 24 cars. I built the thing that listens and talks back.
3. Free race engineer for F1 25 (PS5/PC): the strategy maths is real code, the AI only reads it out loud.

r/F1Game: 1 or 3. r/simracing: 2. Don't post the same title twice.

---

## The post

The game already broadcasts UDP telemetry for all 24 cars, every lap. Nothing was listening to it, so I built something that does.

It works out gaps, tyre wear, degradation and ranked stop plans on your PC, then talks to you over the radio. You can ask it things mid-lap, and it calls rival stops, damage and safety-car deltas without being asked.

Before anyone says it: I built this with a heavy hand of AI, Claude and GPT both, and the public commit history says so. That is a different thing from what runs during a race. Every number is computed by ordinary code with tests, the model is never handed telemetry to interpret, and it only reads out what the code already worked out. It can't invent a lap time.

Free, Windows, no account. You bring your own OpenAI key for the voice, billed to you by OpenAI, not me.

The race in the video is synthetic. The app reading it isn't.

yourpitbox.com

---

## Reply bank

Two to four sentences each. Post one when it is asked, not before.

**"This is AI slop."**
I built it with a lot of AI help and I say so in the post rather than waiting to be caught. The question that decides whether it's slop is different: can a number on screen be wrong because a model guessed it? No. The strategy engine and tyre model are deterministic code with tests, and the model can only read out what they produced. Find a number the model invented and I'll take the post down.

**"Is the code open?"**
It's public: github.com/scottsart1/Pit-box. Read the strategy engine and the tool list the model is limited to.
*(Only use this if you're happy with that. The repo has no licence file, so it's readable, not open source. Add a licence or make it private if you'd rather not.)*

**"Did you use AI to write it?"**
Yes, heavily. Claude and GPT both, across the whole project, and the commit history is public and shows it. I'm not going to pretend otherwise. What I'm claiming is narrower and checkable: the app's numbers come from tested, deterministic code, and the model that talks to you during a race can't make anything up.

**"Why do I need my own API key?"**
Otherwise I'd be paying for everyone's radio and would have to charge for the app. Your key stays on your PC. Most questions never reach a model at all, so a race usually costs pennies.

**"Will this get me banned?"**
No. It reads the telemetry stream the game is designed to broadcast, same as any overlay or motion rig. It never sends inputs to the game and never touches its files.

**"PC or just PS5?"**
Both. Same UDP packet format either way. I say on the site that PC rests on the format being identical rather than a big PC hardware test, so tell me if something's off.

**"The race looks fake."**
It is synthetic, and the site says so under the video. Genuine 2026-format packets fed to the real receiver, because I can't record a PS5 race on a dev box. Real app, real packets, not a real race.

**"What doesn't it do?"**
No macOS build, and I won't say "soon" until it exists. The radio needs internet because the voice runs on OpenAI; telemetry, strategy and the dashboard work offline. No reviews on the site yet because nobody has written one.

**"How do I set it up?"**
Illustrated guide on the site, 10 to 15 minutes: install, make an OpenAI key, put your PC's address in the game's telemetry settings, port 20777, format 2026, radio check.

**"What data leaves my machine?"**
Telemetry, the session database and your Windows username never leave your PC. Only short situation summaries go to the provider you picked, only for questions needing judgement. Nothing comes to me.

---

## Don't

- Reply to everything within a minute. Reads as a bot.
<!-- A line about reacting to downvotes was removed from here; added in error. -->
- Post to four subs in an hour.
- Promise a Mac build, a date, or a feature that isn't in the download today.
