"""FFmpeg assembly: scene clips → base video → voice + music + captions + progress bar."""
import os
import random

from .util import ASSETS, ROOT, run, log, media_duration


def _fit_image(src, W, H, workdir, i):
    """Cover-crops a still to exactly 2W×2H (the zoompan canvas) with Pillow first. Archive scans come in every
    shape (a 600×20000 strip once made FFmpeg's scale step fail); a fixed 9:16 input can't."""
    from PIL import Image, ImageOps
    out = workdir / f"fit_{i:02d}.jpg"
    with Image.open(src) as im:
        ImageOps.fit(im.convert("RGB"), (W * 2, H * 2), Image.LANCZOS).save(out, "JPEG", quality=92)
    return out


# Where each punch-in shot looks (x, y as a fraction of the free space): top, bottom, left, right.
ANCHORS = [(0.5, 0.25), (0.5, 0.75), (0.25, 0.5), (0.75, 0.5)]


def shot_frames(sc, i, cfg):
    """Frame counts of the shots a scene is cut into. A still held for 4–6 s with one slow zoom reads as a
    slideshow (swipe-aways; also the wording of YouTube's "inauthentic content" policy), so a long still scene
    becomes 2–3 shots of the same picture: wide, then tighter framings of different parts of it (documentary
    punch-ins), i.e. a cut every ~2.5 s with no extra image search or model call. Video clips already move, and
    to-scale graphics must stay whole (a punch-in would crop their labels)."""
    fps = cfg["video"]["fps"]
    total = max(1, int(round(sc["duration"] * fps)))
    shot = cfg["video"].get("max_shot_seconds", 0)
    if sc["media_kind"] == "video" or sc.get("compare") or (sc.get("rights") or {}).get("source") == "compare" \
            or not shot:
        return [total]
    n = min(3, max(1, -(-total // int(shot * fps))))
    return [total // n + (1 if k < total % n else 0) for k in range(n)]


def _scene_clip(sc, i, cfg, workdir):
    """Returns the scene's segment files (one per shot)."""
    W, H, fps = cfg["video"]["width"], cfg["video"]["height"], cfg["video"]["fps"]
    dur = sc["duration"]
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-an"]
    if sc["media_kind"] == "video":
        out = workdir / f"seg_{i:02d}.mp4"
        vf = (f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1,"
              f"fps={fps},eq=contrast=1.05:saturation=1.1")
        run(["ffmpeg", "-y", "-stream_loop", "-1", "-i", sc["media"], "-t", f"{dur:.3f}", "-vf", vf, *enc, str(out)])
        return [out]
    src = str(_fit_image(sc["media"], W, H, workdir, i))
    outs = []
    for k, frames in enumerate(shot_frames(sc, i, cfg)):
        out = workdir / f"seg_{i:02d}_{k}.mp4"
        if k == 0:
            zoom_in = i % 2 == 0  # alternate zoom direction so it doesn't feel templated
            z = f"min(1.0+0.12*on/{frames},1.12)" if zoom_in else f"max(1.12-0.12*on/{frames},1.0)"
            x, y = "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
        else:  # punch-in: start tighter on another part of the picture, keep drifting in
            ax, ay = ANCHORS[(i + k) % len(ANCHORS)]
            z = f"min(1.3+0.08*on/{frames},1.38)"
            x, y = f"(iw-iw/zoom)*{ax}", f"(ih-ih/zoom)*{ay}"
        vf = (f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase,crop={W * 2}:{H * 2},"
              f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s={W}x{H}:fps={fps},setsar=1")
        run(["ffmpeg", "-y", "-loop", "1", "-i", src, "-frames:v", str(frames), "-vf", vf, *enc, str(out)])
        outs.append(out)
    return outs


def music_library(cfg):
    """The channel's background tracks: video.music_dir (relative to the project), default assets/music."""
    d = ROOT / cfg["video"]["music_dir"] if cfg["video"].get("music_dir") else ASSETS / "music"
    return sorted(d.glob("*.mp3")) + sorted(d.glob("*.m4a"))


def music_mood_rule(cfg):
    """Writer-prompt line asking for the video's music mood (only for channels with video.music_moods)."""
    moods = list(cfg["video"].get("music_moods", {}))
    return (f'- "music_mood": the background-music mood that fits THIS video best, exactly one of {moods}.'
            if moods else "")


def pick_music(cfg, avoid=None, mood=None):
    """A track that suits the video's mood (video.music_moods: mood → track names), else any track of the channel;
    not the one used last if there is a choice (consecutive Shorts shouldn't sound the same)."""
    lib = music_library(cfg)
    if not lib:
        return None
    fits = set(cfg["video"].get("music_moods", {}).get(mood) or [])
    lib = [m for m in lib if m.stem in fits] or lib
    pool = [m for m in lib if m.name != avoid] or lib
    return random.choice(pool)


def _music_input(music, total):
    try:  # start somewhere inside the track so the same song doesn't always open the same way
        offset = random.uniform(0, max(0.0, media_duration(music) - total - 2))
    except Exception:
        offset = 0.0
    return ["-stream_loop", "-1", "-ss", f"{offset:.2f}", "-i", str(music)]


def _mix(voice, music, cfg, total):
    """Audio graph: the bed sits at music_volume and ducks ~8 dB under the narrator (sidechain), so it fills the
    pauses and carries the mood without fighting the words; short fade in, longer fade out at the end."""
    vol = cfg["video"].get("music_volume", 0.16)
    return (f"[{voice}]aformat=channel_layouts=stereo,asplit=2[vo][key];"
            f"[{music}]aformat=channel_layouts=stereo,volume={vol},afade=t=in:d=0.8,"
            f"afade=t=out:st={max(0.0, total - 1.8):.3f}:d=1.8[bed];"
            f"[bed][key]sidechaincompress=threshold=0.04:ratio=5:attack=15:release=350:makeup=1[mu];"
            f"[vo][mu]amix=inputs=2:duration=first:normalize=0,loudnorm=I=-14:TP=-1.5:LRA=11[aout]")


def add_music(video, music, cfg, out_path):
    """Mixes a bed under a finished voice-only video: same picture (stream copy), same narration."""
    total = media_duration(video)
    run(["ffmpeg", "-y", "-i", str(video), *_music_input(music, total), "-filter_complex", _mix("0:a", "1:a", cfg, total),
         "-map", "0:v", "-map", "[aout]", "-t", f"{total:.3f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
         "-ar", "44100", "-movflags", "+faststart", str(out_path)])
    return out_path


def render(timeline, voice_wav, ass_path, cfg, workdir, out_path, music="auto"):
    """music: a track path, None for voice only, or "auto" to pick one from the channel's library."""
    W, H = cfg["video"]["width"], cfg["video"]["height"]
    segs = [seg for i, sc in enumerate(timeline) for seg in _scene_clip(sc, i, cfg, workdir)]
    (workdir / "segs.txt").write_text("".join(f"file '{s.name}'\n" for s in segs))
    run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", "segs.txt", "-c", "copy", "base.mp4"], cwd=workdir)

    total = sum(sc["duration"] for sc in timeline)
    if music == "auto":
        music = pick_music(cfg)
    # relative + forward slashes: Windows drive letters ("C:\\") break FFmpeg filter syntax
    fonts_dir = os.path.relpath(ASSETS / "fonts", workdir).replace("\\", "/")

    inputs = ["-i", "base.mp4", "-i", voice_wav.name]
    if music:
        inputs += _music_input(music, total)

    v = f"[0:v]subtitles={ass_path.name}:fontsdir='{fonts_dir}'"
    if cfg["video"].get("progress_bar", True):
        v += (f"[sv];color=c=0xFFD400:s={W}x10:r={cfg['video']['fps']}[bar];"
              f"[sv][bar]overlay=x='-w+w*t/{total:.3f}':y=0:shortest=1")
    v += "[vout]"

    if music:
        a = _mix("1:a", "2:a", cfg, total)
    else:
        a = "[1:a]loudnorm=I=-14:TP=-1.5:LRA=11[aout]"

    run(["ffmpeg", "-y", *inputs, "-filter_complex", f"{v};{a}", "-map", "[vout]", "-map", "[aout]",
         "-t", f"{total:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "19", "-pix_fmt", "yuv420p",
         "-c:a", "aac", "-b:a", "192k", "-ar", "44100", "-movflags", "+faststart", str(out_path)], cwd=workdir)
    log(f"rendered {out_path} ({total:.1f}s, music: {music.name if music else 'none'})")
    return out_path
