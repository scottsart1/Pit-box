# Reddit launch post: Your Pit Box

Written to be posted by the developer, in the first person. Everything in it
is checkable against the site, the videos or the app. Nothing in it is a
claim the product cannot back, because the one thing a racing subreddit
punishes faster than a bad tool is a post that oversells one.

Read the target subreddit's rules before posting. Most racing subs allow a
developer to show their own work if it is disclosed as such, but several
limit self-promotion to a share of your activity or a specific day, and
some want a mod's nod first. When in doubt, message the mods with the post
and the video; a permission reply in your pocket ends any "spam" report.

Post the 45-second captioned video as the post itself where the sub allows
video posts, with the text below as the first comment. Where the sub wants
text posts, use the body below and put the video link in it.

---

## Titles

Pick one. All are under Reddit's limit, say what the thing is, say it is
free, and say where the numbers come from, because that is the question
every reader is already asking.

1. I made a free race engineer for F1 25 (PS5 or PC) that reads the game's UDP telemetry and talks back over the radio. The strategy maths is ordinary code, not a language model guessing.

2. Your PS5 already broadcasts telemetry for all 24 cars, every lap. I built the thing that listens to it: a free race engineer for F1 25 that computes the strategy and only uses AI to read it out loud.

3. Free Windows app for F1 25 players: a race engineer that computes gaps, tyre wear and stop plans from your telemetry, then talks to you on the radio. No account, no subscription, your own API key for the voice.

4. I built a race engineer for F1 25 because the game's telemetry stream was going nowhere. It is free now. Every number is computed locally; the AI is only allowed to say them out loud.

For r/F1Game specifically, 1 or 4. For r/simracing, 2 or 3 (they care more
about the telemetry angle than the F1 licence). For a PS5 sub, 2.

---

## Body

**What it is**

Your Pit Box is a Windows app that sits beside F1 25: 2026 Season Pack (PS5 or PC) and acts as your race engineer. The game already sends UDP telemetry for all 24 cars, every packet. Your Pit Box reads that stream, works out gaps, tyre wear, degradation, undercut maths and ranked stop plans, and talks to you over the radio through your PC's mic and speakers. You can ask it things mid-lap ("Mark, what's the gap ahead?", "should I box or stay out?") and it calls things you didn't ask about: a rival's stop as it happens, a car closing on you, damage, safety-car deltas.

It is free to download: yourpitbox.com. No account, no subscription, no code.

**Where the numbers come from, because I know what you're thinking**

I am aware that "AI race engineer" reads as "a chatbot that makes up lap times". So here is exactly how this one is built.

Every number on screen is computed by ordinary, deterministic code from the telemetry packets. Gaps, tyre temperatures, wear, stint degradation, the cost of a stop, which plan finishes higher: all of it is calculated locally on your PC and covered by tests. The language model is never handed raw telemetry to interpret. It is only allowed to read out results the code has already produced, through a fixed set of tools with validated arguments. It cannot invent a lap time, and it has no file, shell or network access of any kind.

Common questions never reach a model at all. Gaps, temps, damage and strategy status are answered locally, which is why they come back instantly and cost nothing. The model only gets involved when a question genuinely needs judgement ("is it worth defending or should I let him through and undercut later?"), and even then it is narrating computed results.

The videos on the site are the real application running, screen-recorded. The engineer's answers in the longer demo were not written for the video; they are what the running app said. One thing I should be upfront about: the race in the videos is synthetic. It is generated as genuine 2026-format packets and sent to the real receiver, because I cannot record a PS5 race on a development box. The software reading those packets is exactly what you download.

**Optional paragraph: the code is public.** The repository at
github.com/scottsart1/Pit-box is public but carries no licence file, so it
is "source you can read", not open source. Decide before posting whether
you want to point people at it. If you do, this line is the strongest
answer to the slop question there is, because anyone can read the strategy
engine and its tests. If you would rather not, delete this paragraph and add
a licence (or make the repo private) before someone finds it anyway.

> If you want to check the "no invented numbers" claim rather than take my word for it, the code is public: github.com/scottsart1/Pit-box. The strategy engine and tyre model are plain Python with tests, and the model's tool list is in there too.

**What you need**

- F1 25: 2026 Season Pack on PS5 or PC, with UDP telemetry set to format 2026.
- A Windows 10 or 11 PC on the same home network as the PS5, or the one PC running both.
- Your own OpenAI API key for the spoken radio. The engineer's reasoning can also run on Claude, DeepSeek or Kimi with that provider's key. The API usage is billed by the provider to you, not to me. Most questions are answered locally and cost nothing; a long race with heavy radio use runs up a small charge.
- A mic and speakers if you want to talk to it. Everything else works without them.

**What it does not do**

- No macOS build. I am not going to say "coming soon" until it exists.
- The radio needs an internet connection, because the voice runs on OpenAI. Telemetry, strategy, analysis and the dashboard all work offline.
- PC support is based on the packet format being identical, not on a big PC hardware test. If something is off on PC, tell me and I will fix it.
- There are no reviews on the site, because nobody has reviewed it yet. I would rather have that gap than a fake one.

**Data**

Your telemetry stays on your PC. The session database, the raw traces and your Windows username are never uploaded. Only compact situation summaries go to the AI provider you chose, and only for questions that need judgement. Nothing is sent to me. The download page offers to take an email so I can tell you about updates; you can skip it and the download starts anyway.

**Setup**

There is an illustrated guide on the site that takes most people 10 to 15 minutes: install, create an OpenAI key, enter the PC's address in the game's telemetry settings, port 20777, format 2026, and do a radio check.

If you try it, I want to hear what broke, what was wrong, and what call you disagreed with. That feedback is worth more to a one-person project than anything else right now. There is a buy-me-a-coffee link on the site if it earns you a position, but it changes nothing about what you get.

---

## First comment (for video posts, or wherever the sub wants links in comments)

Site and download: https://yourpitbox.com
Setup guide: https://yourpitbox.com/guide.html
The longer demo with the spoken radio, if the 45-second cut leaves you wondering what the answers sound like: it is on the same page.

Windows only for now. Free. You bring your own OpenAI key for the voice, and that usage is billed to you by OpenAI, not by me.

---

## Replies to have ready

**"This is AI slop."**

Fair suspicion, and I would have it too. The test is whether any number on the screen can be wrong because a model guessed it, and here none can. The strategy engine, tyre model and gap maths are deterministic code with tests, and the model is only allowed to read out what that code produced through a fixed set of tools. If you find a number that is wrong, that is a bug in ordinary code and I will fix it. If you find one the model invented, I will eat the post.

**"Did you use AI to write the code?"**

Answer this one truthfully in your own words. A reasonable version: yes, I use AI tools while building it, like most people shipping software now. What I am claiming is not that no AI touched the code. It is that the app's numbers come from deterministic, tested code, and the model that talks to you is not allowed to make anything up.

**"Why do I need my own API key? Why not include it?"**

Because then I would be paying for everyone's radio and would have to charge for the app, and because your key never leaves your PC. Most questions never call the model at all, so for most races the cost is pennies.

**"Will this get me banned?"**

No. It reads the telemetry stream the game is designed to broadcast, the same one overlays, dashboards and motion rigs use. It never sends inputs to the game and never touches its files.

**"Does it work on PC or just PS5?"**

Both. The receiver reads standard F1 2026 UDP packets, and the packet format is the same from either. PC support is based on that rather than a large PC hardware test, which is why I say so on the site.

**"The race in the video looks fake."**

It is synthetic, and the site says so under the video. It is generated as genuine 2026-format packets and fed to the real receiver, because I cannot record a PS5 race on a development machine. What you are watching is the real app reacting to real packets, just not a real race.

---

## Things not to do

- Do not reply to every comment within a minute. It reads as a bot.
- Do not argue with a downvote. Answer the question underneath it, once.
- Do not post the same text to four subs in an hour. Space it out, and change the opening line for each.
- Do not promise a Mac build, a date, or a feature that is not in the download today.
