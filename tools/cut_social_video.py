"""Cut a vertical social video from real gameplay and the real overlay.

    python -m tools.cut_social_video \
        --footage capture/melbourne.mp4 \
        --overlay-frames marketing/videos/overlay-frames \
        --storyboard tools/social_storyboards/reel-engineer.json \
        --out marketing/videos/reel-engineer.mp4

Two things are composited and neither is drawn for the video. The gameplay is
whatever was recorded off the console or PC. The panel under it is
``static/overlay.html`` -- the same OBS browser source a streamer points at Your
Pit Box -- captured live against a running session by
``tools.record_overlay_frames``, so the positions, wear percentages, strategy
call and radio line are the application's own output rather than a mockup.

The captions are the only authored text, and they are marketing copy: they say
what the product does, never what the engineer said. Because the gameplay and
the overlay come from two different sessions, every storyboard carries a
``disclosure`` line that is burned into the frame for the whole video. That is
the same promise the screenshots and the website already make, and a cut that
implies the overlay is reading the lap on screen would break it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]

# The product's own caption palette, lifted from tools/capture_demo_video.py so
# a social cut and the long-form demo look like the same product.
INK = (255, 255, 255, 255)
SUBTLE = (198, 206, 219, 255)
ACCENT = (255, 77, 95, 255)
PANEL = (4, 7, 10, 247)
HAIRLINE = (255, 255, 255, 26)

FONT_DIRECTORIES = (
    Path("/usr/share/fonts/opentype/inter"),
    Path("/usr/share/fonts/truetype/inter"),
    Path("C:/Windows/Fonts"),
)
FONT_FALLBACKS = {
    "display": ("InterDisplay-ExtraBold.otf", "Inter-ExtraBold.otf", "segoeuib.ttf"),
    "bold": ("Inter-Bold.otf", "segoeuib.ttf"),
    "medium": ("Inter-Medium.otf", "segoeui.ttf"),
}


def font(kind: str, size: int) -> ImageFont.FreeTypeFont:
    for name in FONT_FALLBACKS[kind]:
        for directory in FONT_DIRECTORIES:
            candidate = directory / name
            if candidate.exists():
                return ImageFont.truetype(str(candidate), size)
    # DejaVu ships with Pillow's test corpus on most systems and with every
    # Linux desktop; a plainer caption is better than no video.
    return ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size
    )


def probe_seconds(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return float(result.stdout.strip() or 0.0)


def run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr[-4000:])
        raise SystemExit(f"ffmpeg failed: {' '.join(command[:6])} ...")


def tracked_width(draw, text: str, face, tracking: float) -> int:
    return int(
        sum(draw.textlength(character, font=face) for character in text)
        + tracking * max(0, len(text) - 1)
    )


def draw_tracked(draw, xy, text: str, face, fill, tracking: float) -> None:
    """Letter-spaced text; Pillow has no tracking of its own."""
    x, y = xy
    for character in text:
        draw.text((x, y), character, font=face, fill=fill)
        x += draw.textlength(character, font=face) + tracking


def wrap(draw, text: str, face, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=face) <= width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


@dataclass(frozen=True, slots=True)
class Beat:
    start: float
    end: float
    title: str
    sub: str = ""
    eyebrow: str = ""
    speed: float = 1.0

    @property
    def source_seconds(self) -> float:
        return max(0.0, self.end - self.start)

    @property
    def timeline_seconds(self) -> float:
        return self.source_seconds / self.speed


def caption_card(width: int, beat: Beat, sizes: dict[str, int]) -> Image.Image:
    """One caption bar in the product's style, on a transparent canvas."""
    pad_x, pad_top, pad_bottom = sizes["pad_x"], sizes["pad_top"], sizes["pad_bottom"]
    eyebrow_face = font("bold", sizes["eyebrow"])
    title_face = font("display", sizes["title"])
    sub_face = font("medium", sizes["sub"])
    inner = width - 2 * pad_x

    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    title_lines = wrap(probe, beat.title, title_face, inner)
    sub_lines = wrap(probe, beat.sub, sub_face, inner) if beat.sub else []

    title_step = int(sizes["title"] * 1.16)
    sub_step = int(sizes["sub"] * 1.40)
    height = pad_top + pad_bottom
    if beat.eyebrow:
        height += int(sizes["eyebrow"] * 1.9)
    height += title_step * len(title_lines)
    if sub_lines:
        height += sizes["gap"] + sub_step * len(sub_lines)

    card = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw.rectangle([0, 0, width, height], fill=PANEL)
    draw.line([(0, 0), (width, 0)], fill=HAIRLINE, width=2)

    y = pad_top
    if beat.eyebrow:
        draw_tracked(
            draw, (pad_x, y), beat.eyebrow.upper(), eyebrow_face, ACCENT,
            tracking=sizes["eyebrow"] * 0.14,
        )
        y += int(sizes["eyebrow"] * 1.9)
    for line in title_lines:
        draw.text((pad_x, y), line, font=title_face, fill=INK)
        y += title_step
    if sub_lines:
        y += sizes["gap"]
        for line in sub_lines:
            draw.text((pad_x, y), line, font=sub_face, fill=SUBTLE)
            y += sub_step
    return card


def strip_card(width: int, text: str, size: int, colour) -> Image.Image:
    """A single centred line, used for the brand mark and the disclosure."""
    face = font("bold", size)
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    tracking = size * 0.16
    text_width = tracked_width(probe, text.upper(), face, tracking)
    card = Image.new("RGBA", (width, int(size * 2.0)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(card)
    draw_tracked(
        draw, ((width - text_width) // 2, int(size * 0.4)), text.upper(), face,
        colour, tracking,
    )
    return card


def end_card(width: int, height: int, spec: dict) -> Image.Image:
    card = Image.new("RGBA", (width, height), (6, 9, 13, 255))
    draw = ImageDraw.Draw(card)
    eyebrow_face = font("bold", int(width * 0.026))
    title_face = font("display", int(width * 0.072))
    sub_face = font("medium", int(width * 0.034))
    note_face = font("medium", int(width * 0.026))

    inner = int(width * 0.84)
    probe = ImageDraw.Draw(Image.new("RGBA", (8, 8)))
    title_lines = wrap(probe, spec.get("title", ""), title_face, inner)
    sub_lines = wrap(probe, spec.get("sub", ""), sub_face, inner)
    note_lines = wrap(probe, spec.get("note", ""), note_face, inner)

    title_step = int(width * 0.072 * 1.15)
    sub_step = int(width * 0.034 * 1.45)
    note_step = int(width * 0.026 * 1.5)
    block = (
        int(width * 0.026 * 2.4)
        + title_step * len(title_lines)
        + int(width * 0.05)
        + sub_step * len(sub_lines)
        + (int(width * 0.06) + note_step * len(note_lines) if note_lines else 0)
    )
    y = (height - block) // 2

    eyebrow = spec.get("eyebrow", "Your Pit Box")
    tracking = int(width * 0.026) * 0.18
    text_width = tracked_width(probe, eyebrow.upper(), eyebrow_face, tracking)
    draw_tracked(
        draw, ((width - text_width) // 2, y), eyebrow.upper(), eyebrow_face,
        ACCENT, tracking,
    )
    y += int(width * 0.026 * 2.4)
    for line in title_lines:
        draw.text(
            ((width - draw.textlength(line, font=title_face)) // 2, y), line,
            font=title_face, fill=INK,
        )
        y += title_step
    y += int(width * 0.05)
    for line in sub_lines:
        draw.text(
            ((width - draw.textlength(line, font=sub_face)) // 2, y), line,
            font=sub_face, fill=SUBTLE,
        )
        y += sub_step
    if note_lines:
        y += int(width * 0.06)
        for line in note_lines:
            draw.text(
                ((width - draw.textlength(line, font=note_face)) // 2, y), line,
                font=note_face, fill=(143, 164, 183, 255),
            )
            y += note_step
    return card


def build_segments(footage: Path, beats: tuple[Beat, ...], work: Path,
                   fps: int) -> Path:
    """Trim the beats out of the footage and lay them end to end.

    Each segment is re-encoded rather than stream-copied: the cuts are on
    arbitrary frames, and a copy would start every segment at the previous
    keyframe and put the wrong frames on screen.
    """
    listing = work / "segments.txt"
    entries: list[str] = []
    for index, beat in enumerate(beats):
        target = work / f"seg_{index:02d}.mp4"
        video = f"fps={fps},format=yuv420p"
        audio = "aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo"
        if beat.speed != 1.0:
            video = f"setpts={1.0 / beat.speed:.6f}*PTS,{video}"
            # atempo is only defined from 0.5 to 2.0; chain it for anything
            # outside that, which a heavy slow-motion beat needs.
            tempo, chain = beat.speed, []
            while tempo < 0.5:
                chain.append("atempo=0.5")
                tempo /= 0.5
            while tempo > 2.0:
                chain.append("atempo=2.0")
                tempo /= 2.0
            chain.append(f"atempo={tempo:.6f}")
            audio = f"{','.join(chain)},{audio}"
        # A short fade at both ends keeps the joins from clicking.
        audio += f",afade=t=in:st=0:d=0.04,afade=t=out:st={max(0.0, beat.timeline_seconds - 0.06):.3f}:d=0.06"
        run(
            [
                "ffmpeg", "-v", "error", "-y",
                "-ss", f"{beat.start:.3f}", "-to", f"{beat.end:.3f}", "-i", str(footage),
                "-filter:v", video, "-filter:a", audio,
                "-c:v", "libx264", "-preset", "medium", "-crf", "16",
                "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
                str(target),
            ]
        )
        entries.append(f"file '{target.name}'")
    listing.write_text("\n".join(entries) + "\n", encoding="utf-8")
    timeline = work / "timeline.mp4"
    run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(listing), "-c", "copy", str(timeline),
        ]
    )
    return timeline


def overlay_clip(frames: Path, work: Path, spec: dict, fps: int) -> Path | None:
    """Turn the captured overlay PNGs into one clip that keeps its alpha."""
    captured = sorted(frames.glob("ov_*.png"))
    if not captured:
        return None
    start = int(float(spec.get("start_s", 0.0)) * float(spec.get("fps", 12)))
    kept = captured[start:] or captured
    staged = work / "ov"
    staged.mkdir(exist_ok=True)
    crop = spec.get("crop") or [30, 30, 1032, 1000]
    for index, source in enumerate(kept):
        image = Image.open(source).convert("RGBA")
        image.crop(
            (crop[0], crop[1], crop[0] + crop[2], crop[1] + crop[3])
        ).save(staged / f"f_{index:05d}.png")
    clip = work / "overlay.mov"
    run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-framerate", str(spec.get("fps", 12)),
            "-i", str(staged / "f_%05d.png"),
            "-vf", f"fps={fps},scale={spec.get('width', 780)}:-1:flags=lanczos",
            "-c:v", "qtrle", str(clip),
        ]
    )
    return clip


def compose(timeline: Path, overlay: Path | None, cards: list[tuple[Path, float, float]],
            statics: list[tuple[Path, int, int]], board: dict, work: Path,
            out: Path, fps: int) -> None:
    width, height = board["width"], board["height"]
    layout = board["layout"]
    card_y, card_h = layout["video_y"], layout["video_h"]

    seconds = probe_seconds(timeline)
    inputs: list[str] = ["-i", str(timeline)]
    if overlay:
        inputs += ["-i", str(overlay)]
    for path, _, _ in statics:
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{seconds:.3f}",
                   "-i", str(path)]
    for path, _, _ in cards:
        # A caption is one PNG, so it has to be looped into a real timeline
        # before it is faded: a still frame carries timestamp zero, and a fade
        # that starts at second nine never reaches it.
        inputs += ["-loop", "1", "-framerate", str(fps), "-t", f"{seconds:.3f}",
                   "-i", str(path)]

    # A blurred, darkened copy of the same frame fills the 9:16 canvas: the
    # gameplay is 16:9 and anything else would either letterbox it or crop the
    # car out of its own video.
    # A vertical frame is taller than 16:9 gameplay, so the storyboard may crop
    # the sides in before scaling. The action in an onboard shot is centred;
    # the barriers at the edges are what a phone screen can afford to lose.
    crop = board.get("source_crop")
    if crop:
        graph_pre = [
            f"[0:v]crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]},split=2[src0][src1]"
        ]
        source_bg, source_card = "[src0]", "[src1]"
    else:
        graph_pre = ["[0:v]split=2[src0][src1]"]
        source_bg, source_card = "[src0]", "[src1]"

    graph = graph_pre + [
        (
            f"{source_bg}scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            f"gblur=sigma=48,eq=brightness=-0.30:saturation=0.45[bg]"
        ),
        f"{source_card}scale={width}:{card_h}:flags=lanczos[card]",
        f"[bg][card]overlay=0:{card_y}[base]",
    ]
    stream = "base"
    index = 1
    if overlay:
        graph.append(
            f"[{index}:v]format=rgba,colorchannelmixer=aa=1.0[ovl]"
        )
        graph.append(
            f"[{stream}][ovl]overlay={layout['overlay_x']}:{layout['overlay_y']}"
            f":eof_action=repeat[withovl]"
        )
        stream = "withovl"
        index += 1
    for path, x, y in statics:
        graph.append(f"[{index}:v]format=rgba[s{index}]")
        graph.append(f"[{stream}][s{index}]overlay={x}:{y}[st{index}]")
        stream = f"st{index}"
        index += 1
    for path, start, end in cards:
        fade = 0.32
        graph.append(
            f"[{index}:v]format=rgba,"
            f"fade=t=in:st={start:.3f}:d={fade}:alpha=1,"
            f"fade=t=out:st={max(start, end - fade):.3f}:d={fade}:alpha=1,"
            f"setpts=PTS-STARTPTS[c{index}]"
        )
        graph.append(
            f"[{stream}][c{index}]overlay={layout['caption_x']}:"
            f"{layout['caption_bottom']}-h:enable='between(t,{start:.3f},{end:.3f})'"
            f"[cc{index}]"
        )
        stream = f"cc{index}"
        index += 1
    graph.append(f"[{stream}]format=yuv420p[v]")

    run(
        [
            "ffmpeg", "-v", "error", "-y", *inputs,
            "-filter_complex", ";".join(graph),
            # Instagram plays back around -14 LUFS. Raw game capture sits far
            # below that, and a quiet reel is a scrolled-past reel.
            "-filter:a", "loudnorm=I=-14:TP=-1.5:LRA=11",
            "-map", "[v]", "-map", "0:a",
            # The captured overlay is usually longer than the cut. Without this
            # the composite runs to the overlay's length and the video ends on
            # a frozen last gameplay frame.
            "-t", f"{seconds:.3f}",
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "slow", "-crf", "19",
            "-profile:v", "high", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            str(work / "composed.mp4"),
        ]
    )
    shutil.move(str(work / "composed.mp4"), str(out))


def append_end_card(video: Path, board: dict, work: Path, fps: int) -> None:
    spec = board.get("end_card")
    if not spec:
        return
    width, height = board["width"], board["height"]
    still = work / "endcard.png"
    end_card(width, height, spec).convert("RGB").save(still)
    tail = work / "endcard.mp4"
    run(
        [
            "ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(still),
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", f"{float(spec.get('seconds', 3.0)):.2f}",
            "-r", str(fps),
            "-c:v", "libx264", "-preset", "slow", "-crf", "19",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            str(tail),
        ]
    )
    listing = work / "final.txt"
    listing.write_text(f"file '{video.resolve()}'\nfile '{tail.resolve()}'\n", encoding="utf-8")
    joined = work / "joined.mp4"
    run(
        [
            "ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0",
            "-i", str(listing), "-c:v", "libx264", "-preset", "slow", "-crf", "19",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart", str(joined),
        ]
    )
    shutil.move(str(joined), str(video))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--footage", type=Path, required=True)
    parser.add_argument("--overlay-frames", type=Path, default=None)
    parser.add_argument("--storyboard", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--fps", type=int, default=30)
    arguments = parser.parse_args()

    board = json.loads(arguments.storyboard.read_text(encoding="utf-8"))
    beats = tuple(
        Beat(
            start=float(entry["in"]), end=float(entry["out"]),
            title=str(entry.get("title", "")), sub=str(entry.get("sub", "")),
            eyebrow=str(entry.get("eyebrow", "")), speed=float(entry.get("speed", 1.0)),
        )
        for entry in board["beats"]
    )
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="pitbox-social-"))
    try:
        timeline = build_segments(arguments.footage, beats, work, arguments.fps)

        clip = None
        if arguments.overlay_frames and arguments.overlay_frames.exists():
            clip = overlay_clip(
                arguments.overlay_frames, work, board.get("overlay", {}), arguments.fps
            )

        sizes = board.get("caption_sizes") or {
            "eyebrow": 26, "title": 60, "sub": 33, "gap": 14,
            "pad_x": 46, "pad_top": 34, "pad_bottom": 40,
        }
        cards: list[tuple[Path, float, float]] = []
        cursor = 0.0
        for index, beat in enumerate(beats):
            span = beat.timeline_seconds
            if beat.title:
                path = work / f"cap_{index:02d}.png"
                caption_card(board["layout"]["caption_w"], beat, sizes).save(path)
                cards.append((path, cursor + 0.12, cursor + span - 0.06))
            cursor += span

        statics: list[tuple[Path, int, int]] = []
        if board.get("brand"):
            path = work / "brand.png"
            strip_card(board["width"], board["brand"], board.get("brand_size", 30),
                       ACCENT).save(path)
            statics.append((path, 0, board["layout"]["brand_y"]))
        if board.get("disclosure"):
            path = work / "disclosure.png"
            strip_card(board["width"], board["disclosure"],
                       board.get("disclosure_size", 20),
                       (143, 164, 183, 235)).save(path)
            statics.append((path, 0, board["layout"]["disclosure_y"]))

        compose(timeline, clip, cards, statics, board, work, arguments.out,
                arguments.fps)
        append_end_card(arguments.out, board, work, arguments.fps)
        print(f"wrote {arguments.out}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
