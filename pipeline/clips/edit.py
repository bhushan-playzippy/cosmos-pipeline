"""Edit agent: renders a vertical Short = narrated intro → source clip → narrated outro, with burned-in captions,
on-screen attribution and loudness normalization. Reuses the pipeline's TTS (voice.py) and captions (captions.py).

Reframing (edit.reframe):
  fit_blur      whole source frame, full width, over a blurred fill of itself. Nobody is ever cropped out, so it's
                the default when no subject tracking is available.
  center_crop   fills the screen by cropping the sides (fine for centred single-speaker footage).
"""
import os

from . import ingest, transcript
from .. import captions, voice
from ..util import ASSETS, run, media_duration, log

ENC = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
       "-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2"]


def _frame_filter(cfg, dim=False):
    W, H, fps = cfg["video"]["width"], cfg["video"]["height"], cfg["video"]["fps"]
    grade = ",eq=brightness=-0.12:saturation=0.85" if dim else ""
    if cfg["edit"].get("reframe", "fit_blur") == "center_crop":
        return f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,fps={fps}{grade}"
    lift = cfg["edit"].get("foreground_lift", 140)  # keeps the picture above the caption band
    return (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=24:2,"
            f"eq=brightness=-0.18[bg];[b]scale={W}:-2[fg];[bg][fg]overlay=(W-w)/2:(H-h)/2-{lift},setsar=1,"
            f"fps={fps}{grade}")


def _narration_part(text, still, cfg, work, name):
    """A still of the source (dimmed) under TTS narration. Returns (mp4, duration, word timings)."""
    d = work / name
    d.mkdir(exist_ok=True)
    timeline, wav = voice.synthesize([{"text": text}], cfg, d)
    dur = timeline[0]["duration"]
    out = work / f"{name}.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", str(still), "-i", str(wav),
         "-filter_complex", f"[0:v]{_frame_filter(cfg, dim=True)}[v]", "-map", "[v]", "-map", "1:a",
         "-t", f"{dur:.3f}", *ENC, str(out)])
    return out, dur, timeline[0]["words"]


def render(seg, src, narration, cfg, work, out_path):
    """→ (video path, edit record). The edit record is what compliance and post.txt report."""
    media = ingest.media_path(src)
    work.mkdir(parents=True, exist_ok=True)
    start, end = seg["start"], seg["end"]
    pad = cfg["edit"].get("pad_seconds", 0.15)  # tiny breath so the first/last syllable isn't clipped
    a, b = max(0.0, start - pad), end + pad
    for t, name in ((start + 0.3, "first.png"), (max(start, end - 0.3), "last.png")):
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.2f}", "-i", str(media), "-frames:v", "1", str(work / name)])

    intro, intro_s, intro_words = _narration_part(narration["intro"], work / "first.png", cfg, work, "intro")
    clip = work / "clip.mp4"
    run(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.3f}", "-i", str(media), "-t", f"{b - a:.3f}",
         "-filter_complex", f"[0:v]{_frame_filter(cfg)}[v]", "-map", "[v]", "-map", "0:a:0", *ENC, str(clip)])
    clip_s = media_duration(clip)
    outro, outro_s, outro_words = _narration_part(narration["outro"], work / "last.png", cfg, work, "outro")

    (work / "parts.txt").write_text("".join(f"file '{p.name}'\n" for p in (intro, clip, outro)))
    run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", "parts.txt", "-c", "copy", "joined.mp4"],
        cwd=work)

    shift = intro_s - a
    clip_words = [{**w, "start": w["start"] + shift, "end": min(w["end"], b) + shift}
                  for w in transcript.words(ingest.cues_of(src)) if a <= w["start"] < b]
    off2 = intro_s + clip_s
    timeline = [{"words": intro_words}, {"words": clip_words},
                {"words": [{**w, "start": w["start"] + off2, "end": w["end"] + off2} for w in outro_words]}]
    ass = captions.build_ass(timeline, cfg, work / "captions.ass")

    attribution = (src.get("rights") or {}).get("attribution_text") or f"Source: {src.get('creator', '')}"
    (work / "attribution.txt").write_text(attribution, encoding="utf-8")
    fonts = os.path.relpath(ASSETS / "fonts", work).replace("\\", "/")
    font = f"{fonts}/Poppins-Bold.ttf"
    vf = (f"subtitles={ass.name}:fontsdir='{fonts}',"
          f"drawtext=fontfile='{font}':textfile=attribution.txt:fontsize=38:fontcolor=white@0.92:"
          f"box=1:boxcolor=black@0.45:boxborderw=14:x=(w-text_w)/2:y={cfg['edit'].get('attribution_y', 210)}")
    run(["ffmpeg", "-y", "-v", "error", "-i", "joined.mp4", "-vf", vf,
         "-af", "loudnorm=I=-14:TP=-1.5:LRA=11", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart",
         str(out_path.resolve())], cwd=work)
    total = media_duration(out_path)
    record = {"source_id": src["id"], "source_start": start, "source_end": end, "cut": [round(a, 2), round(b, 2)],
              "intro_seconds": round(intro_s, 2), "clip_seconds": round(clip_s, 2), "outro_seconds": round(outro_s, 2),
              "total_seconds": round(total, 2), "original_share": round((intro_s + outro_s) / total, 3),
              "attribution_on_screen": attribution, "reframe": cfg["edit"].get("reframe", "fit_blur"),
              "voice": cfg["voice"][cfg["channel"].get("language", "en")],
              "resolution": f"{cfg['video']['width']}x{cfg['video']['height']}", "fps": cfg["video"]["fps"],
              "loudnorm": "I=-14:TP=-1.5:LRA=11", "caption_words": sum(len(t["words"]) for t in timeline)}
    log(f"rendered {out_path.name}: {total:.1f}s ({record['original_share']:.0%} original narration)")
    return out_path, record
