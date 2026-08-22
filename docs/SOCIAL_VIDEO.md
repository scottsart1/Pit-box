# Cutting a social video from real gameplay

`tools/cut_social_video.py` builds a vertical (1080×1920) cut for Instagram,
TikTok and YouTube Shorts out of two things, neither of which is drawn for the
video:

- **the gameplay**, recorded off the console or PC like any other capture; and
- **the panel under it**, which is `static/overlay.html` — the same OBS browser
  source a streamer points at Your Pit Box — recorded live against a running
  session by `tools/record_overlay_frames.py`.

So the position, the wear percentages, the strategy call and the radio line in
the finished video are the application's own output. The only authored text is
the captions, and they are marketing copy: they say what the product does, and
they never put words in the engineer's mouth.

## The disclosure is not optional

Gameplay footage and overlay footage almost always come from two different
sessions — the overlay cannot read a lap that was recorded last week. Every
storyboard therefore carries a `disclosure` string that is burned into the
frame for the whole video:

> Gameplay is real · the panel is the real app on a synthetic race

That is the same promise `docs/screenshots/` and the website already make. A cut
that implies the panel is reading the lap on screen breaks it, so leave the line
in and keep it accurate to whatever the overlay was actually recording.

## 1. Get a session running

A real session works. So does the synthetic one, which is what the shipped
storyboards used:

```bash
python -m tools.demo_server                       # 127.0.0.1:8010, disposable data root
python tools/replay_demo.py --circuit melbourne --driver LECLERC --laps 32 --speed 9
```

`--circuit` and `--driver` exist so the replay can be matched to the footage.
Pick the circuit the gameplay was driven on and the driver who was in the car;
a panel that says Suzuka under a lap of Albert Park is the kind of detail that
gets noticed.

## 2. Record the overlay

Let the race settle into the window you want — mid-stint, with a strategy call
standing and wear climbing, is usually the most watchable — then:

```bash
python -m tools.record_overlay_frames \
    --out marketing/videos/overlay-frames --seconds 48 --fps 12
```

The frames are transparent PNGs at three times scale, so the panel stays sharp
when it is scaled up into a 1080-wide frame.

## 3. Cut it

```bash
python -m tools.cut_social_video \
    --footage /path/to/gameplay.mp4 \
    --overlay-frames marketing/videos/overlay-frames \
    --storyboard tools/social_storyboards/reel-engineer.json \
    --out marketing/videos/reel-engineer.mp4
```

Outputs go to `marketing/videos/`, which is git-ignored: the finished files are
tens of megabytes and are rebuilt from the storyboard whenever they are needed.

## The storyboards

`tools/social_storyboards/` holds one JSON file per cut.

- `reel-engineer.json` — 30 seconds, five beats, for the feed and Reels.
- `hook-radio.json` — 16 seconds, one message, for stories and paid placements.

A beat is a slice of the footage plus the caption that sits over it:

```json
{
  "in": 21.8, "out": 28.6, "speed": 1.0,
  "eyebrow": "Calls you didn't ask for",
  "title": "And it speaks first.",
  "sub": "Rival stops as they happen. A car closing behind. Damage."
}
```

`in` and `out` are seconds into the source footage, so beats can be reordered,
repeated, or dropped without re-recording anything. `speed` below 1.0 is slow
motion, and the audio is pitched with it rather than muted.

The rest of the file is layout: where the video sits in the vertical frame,
where the overlay panel sits under it, and how big the caption type is.
`source_crop` trims the sides of 16:9 footage before it is scaled, because a
vertical frame is taller than the gameplay and the barriers at the edges are
what a phone screen can afford to lose.

Captions use the palette from `tools/capture_demo_video.py`, so a social cut and
the long-form demo look like the same product. Audio is normalised to about
−14 LUFS on the way out, which is roughly what Instagram plays back at.

## Writing the copy

Two rules, both learned from the long-form demo:

1. **Say what the product does, never what the engineer said.** If a real radio
   line belongs in the video, let the overlay's own `LAST RADIO` card show it.
2. **Keep the claims checkable.** Price, platform and "your data stays on your
   PC" all appear on the website; if one changes there, it changes here too.
