# Your Pit Box 5.6.0

5.6.0 changes what the race engineer says on the radio and when. A
shortened answer keeps its instruction, a question is no longer taken as a
command, a flag or penalty call you talk over is said again, and a number
the engineer volunteers is checked before it is spoken.

## Answers

- **The instruction survives.** When a reply is longer than the radio
  allows, the cut now drops a bare "Copy." first and keeps any closing
  question, the pit call in an answer about strategy, and a short opener
  such as "No." with its instruction. Ordinary phrases such as "hold on"
  are no longer removed as leaked model reasoning, so "Stay out. Hold on,
  Safety Car is being deployed, box box box." is no longer cut to "Stay
  out.".
- **Settings → Engineer → Radio verbosity applies.** Terse or chatty there
  now changes the replies; before, it changed nothing. Settings shows the
  length in force, including one set by radio.
- **More questions answered straight from telemetry.** "How are the tyres",
  "what's the gap ahead", "are we OK on fuel", "can you hear me", "check the
  rain" and, outside qualifying, "best lap" and "pole" no longer wait on the
  model. Tyre, gap, fuel and weather answers still refuse stale data. Once
  the game has sent the final classification, "what position am I" answers
  from it.
- **"Say again"** replays the engineer's last line word for word, with no
  model call, when it came just before your request, was said on the
  current lap and heard whole, and race control has not changed this lap or
  in the last two minutes. Otherwise the engineer answers from the current
  state.
- **The lap and the car you asked about.** Lap analysis compares the lap you
  name with the reference you ask for, not the latest lap with your best.
  Session events name the drivers and decode penalties. Every answer about
  whether a gap is growing reads it over about the last lap, so a rival is
  no longer "closing" in one answer and "dropping back" in the next.
- **Typed answers are shown.** The DRIVE and STRATEGY radio panels stopped
  updating after a session's 40th radio entry, and a typed question's answer
  then appeared nowhere. Both panels now redraw on every entry, and both Ask
  boxes show the answer they get.
- **When a provider fails.** With a fallback provider, the whole request
  keeps to one radio deadline. Claude, DeepSeek, Kimi and custom endpoints
  answer on their last round from the tool results already gathered, as
  OpenAI did. A strategy question gets the computed plan on any temporary
  failure, not only a timeout.

## Pit calls

- **More ways of asking get the plan.** In a race, "should I box", "when do
  we box" and "what's the plan" get the ranked plan, as "should I pit" did.
  "What's my fuel and should I pit" or "should I pit if the safety car comes
  out" goes to the engineer with the plan instead of getting the standing
  call back.
- **Confidence once, after the call.** A low-confidence plan's "provisional,
  confidence low" now follows the call in the same sentence, is not added
  when the reply already hedges, and lands on your call rather than on a
  rival's stop or a penalty. A volunteered call spoken from its template
  carries it too.
- **What-ifs for every question.** The tools that price a stop, a
  front-wing change or the points at stake are now offered for every
  question, not only one with a planning word such as "what if". "This lap"
  and "in three laps" are priced at the lap they mean, and the result is set
  against the ranked plan, which it does not replace. Undercut and overcut
  accept "teammate", a car number or a first name.

## What you decide stays decided

- **Questions are questions.** "Okay, should we stay out?", "what if we
  switch to the hards" and "is this stint too long" no longer hold pit
  calls, lock a compound or switch on short answers. "Do it" now agrees to
  the grid plan.
- **The grid plan locks only on agreement.** "Radio check, do you copy?" no
  longer locks the race plan.
- **Spoken lap numbers.** "Box lap twenty two for hards" sets lap 22. A stop
  whose lap cannot be read gets a question back and locks nothing.
- **A declined stop stays declined.** After "I'm not boxing", no call the
  engineer makes on its own carries that stop, and asking when to box gets
  the next option. The five-lap hold ends early at a stop, including one for
  the same compound, or when the race changes (a flag change, the track
  turning wet, new damage, a tyre past its hard wear limit), whether or not
  unsolicited calls are on. "Stay out of trouble" no longer declines the
  stop.
- **An agreed tactic ends with its stop.** "We'll go long" no longer binds
  the engineer, or shows on the dashboard, after the stop it was about.
- **Standing instructions.** "What's the gap behind? And stop telling me
  about the tyres." keeps both. "You can talk about the tyres again" lifts a
  rule, and "clear my standing instructions" lifts them all. A rule about
  gearbox damage no longer silences other damage.
- **Units.** "Can I have the temperatures in Fahrenheit?" switches the unit;
  a question about the unit does not.

## Calls the engineer makes on its own

- **Flags first, and on time.** Flag calls are spoken before other calls and
  wait only while you or the engineer are talking, not for the reply window
  that delayed yellows and safety cars. A yellow is dropped once you reach
  the zone, and a restart call once the race has gone green.
- **Talked-over calls come back.** Pressing push-to-talk over a call used to
  stop every later call until the app restarted. A flag, penalty or damage
  call you talk over is now said once more after your exchange, if it is
  still true. A red flag no longer drops penalty, warning and damage calls.
- **Repeats judged by content.** "VSC ending" after "VSC" and a 5-second
  penalty becoming 10 are said; an unchanged tyre or fuel warning is not
  repeated. Damage is tracked per component, and the call leads with what
  is new.
- **The pit-entry stint debrief is spoken in practice only.** In a race it
  is still saved and shown on DRIVE and STRATEGY.
- **Numbers are checked.** When the model words a call with a number that
  nothing it was shown supports, the template is spoken instead, and the
  saved call records why.

## Realtime speech radio

- **Commands are stored.** "I'm not boxing" or "stop telling me about fuel"
  said in a speech session now changes the same state as push-to-talk, and
  the session's brief carries the recent radio, any hold and the pit call as
  push-to-talk words it. A strategy question there no longer gets "too large
  for radio".
- **Pit-wall calls wait their turn.** A pit-wall call waits while the
  engineer replies or you talk, and is said again if you talked over it
  before any of it played. A reply that fails before saying anything useful
  is answered on the standard radio; one you already heard is not repeated
  in a second voice.

## For operators

- **Database upgrade.** On first start, 5.6.0 backs up the database and adds
  two columns to radio messages (schema 5600): a label on your lines that
  react to the previous answer (a repeat request, a correction, a dispute),
  and the trace of each answer. Once upgraded, the database is refused by
  5.5.1; the backup is not. A history transfer between two devices needs
  both on 5.6.0.
- **Answer traces.** Each answer the standard radio gives is saved with its
  route, provider and model, tool calls and results, the provider's own text
  and every step that changed it, in under about 40 KB. No credentials or
  audio are stored. The session export includes the traces, and the provider
  comparison (`/api/llm/compare`) now shows each reply as the radio would
  speak it.
- **`GET /api/radio/{id}/trace`** returns one radio line with its trace.
  `/api/history` says which lines have one (`has_trace`) but leaves the
  trace out.
- **`tools/engineer_eval.py`** runs the cases in
  `docs/engineer-eval-manifest.json` through the real engineer. Offline, the
  default, a scripted provider answers and no network call is made; `--live`
  sends the cases to your configured providers and makes paid API calls.
- On the normal route, OpenAI reasoning models and the custom endpoint get
  1,600 output tokens, then 4,000 on the retry, so the model's reasoning is
  less likely to use up the answer's budget and force a second request. The
  settings are listed in `.env.example`.
- The engineer's radio stays in English.

Windows remains an explicitly unsigned direct-download installer, and Android
keeps the existing signing identity.
