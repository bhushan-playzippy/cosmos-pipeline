"""Deterministic quality gate: technical checks on the rendered file, editorial/metadata checks on the script,
and an originality screen against everything made before. Runs before the AI reviewer; any failure quarantines."""
import json
import re
import subprocess
from pathlib import Path

from .util import OUTPUT, media_duration


def _ffmpeg_stderr(args):
    return subprocess.run(["ffmpeg", "-hide_banner", "-nostats", *args], capture_output=True, text=True).stderr


def _blank_runs(path, min_dur=0.5, ymax=40):
    """Truly blank stretches: frames whose BRIGHTEST pixel is below `ymax` for at least `min_dur` seconds.
    Dark space footage always has bright stars, so it isn't flagged; a failed or empty render is."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vf",
                          "signalstats,metadata=print:key=lavfi.signalstats.YMAX:file=-", "-an", "-f", "null", "-"],
                         capture_output=True, text=True).stdout
    runs, start, t = [], None, 0.0
    for line in out.splitlines():
        m = re.search(r"pts_time:([\d.]+)", line)
        if m:
            t = float(m.group(1))
            continue
        m = re.search(r"YMAX=([\d.]+)", line)
        if m:
            dark = float(m.group(1)) < ymax
            if dark and start is None:
                start = t
            elif not dark and start is not None:
                if t - start >= min_dur:
                    runs.append((start, t))
                start = None
    if start is not None and t - start >= min_dur:
        runs.append((start, t))
    return runs


def _quiet_floor(path):
    """Level (dB) of the quiet moments: 5th percentile of 100 ms RMS windows, ignoring the first and last second.
    Narration alone drops to digital silence between sentences (about -90 dB); with a music bed under it the
    pauses stay around -33 dB (pass mark -45). That difference is how the gate hears whether the music made it in."""
    out = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(path), "-vn", "-af",
                          "aresample=44100,asetnsamples=n=4410:p=0,astats=metadata=1:reset=1,"
                          "ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-", "-f", "null", "-"],
                         capture_output=True, text=True).stdout
    levels = [float(x) if x not in ("-inf", "inf", "nan") else -120.0
              for x in re.findall(r"RMS_level=(\S+)", out)][10:-10]
    if not levels:
        return None
    levels.sort()
    return levels[len(levels) // 20]


def technical(video, cfg, music=None):
    """music: the track render mixed in (script["music"]); when set, the bed must be audible in the pauses."""
    q, v = cfg.get("qa", {}), cfg["video"]
    checks = []
    probe = json.loads(subprocess.run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(video)],
        capture_output=True, text=True).stdout or "{}")
    streams = probe.get("streams", [])
    vs = next((s for s in streams if s["codec_type"] == "video"), None)
    au = next((s for s in streams if s["codec_type"] == "audio"), None)
    checks.append(("video stream", bool(vs), vs and f"{vs['codec_name']} {vs['width']}x{vs['height']}"))
    checks.append(("audio stream", bool(au), au and au["codec_name"]))
    if vs:
        checks.append(("resolution", (vs["width"], vs["height"]) == (v["width"], v["height"]),
                       f"{vs['width']}x{vs['height']}"))
        num, den = map(int, vs["r_frame_rate"].split("/"))
        checks.append(("frame rate", abs(num / den - v["fps"]) < 0.5, f"{num / den:.2f}"))
    dur = media_duration(video) if vs else 0
    checks.append(("duration", q.get("min_seconds", 20) <= dur <= v["max_seconds"] + 2, f"{dur:.1f}s"))

    if au and vs:  # a truncated narration/mix leaves the end of the video silent or the audio cut short
        try:
            a_dur = float(au.get("duration") or 0)
        except ValueError:
            a_dur = 0.0
        checks.append(("audio covers video", a_dur >= dur - 0.5, f"audio {a_dur:.1f}s / video {dur:.1f}s"))
    if au:
        err = _ffmpeg_stderr(["-i", str(video), "-af", "ebur128", "-f", "null", "-"])
        m = re.findall(r"I:\s+(-?[\d.]+) LUFS", err)
        lufs = float(m[-1]) if m else None
        lo, hi = q.get("loudness_lufs", [-18, -11])
        checks.append(("loudness", lufs is not None and lo <= lufs <= hi, f"{lufs} LUFS"))
        err = _ffmpeg_stderr(["-i", str(video), "-af", f"silencedetect=noise=-40dB:d={q.get('max_silence', 1.5)}",
                              "-f", "null", "-"])
        gaps = re.findall(r"silence_duration: ([\d.]+)", err)
        checks.append(("no long silences", not gaps, f"{len(gaps)} gap(s) > {q.get('max_silence', 1.5)}s"))
        if music:
            floor = _quiet_floor(video)
            checks.append(("background music audible", floor is not None and floor > q.get("music_floor_db", -45),
                           f"pauses at {floor} dB ({music})"))
    base = Path(video).parent / "_work" / "base.mp4"  # scene footage before captions/progress bar, if still there
    if base.exists():
        black = _blank_runs(base)
        checks.append(("no black frames", not black, f"{len(black)} blank segment(s) (no pixel brighter than 40)"))
    else:
        err = _ffmpeg_stderr(["-i", str(video), "-vf", "blackdetect=d=0.5:pix_th=0.08", "-an", "-f", "null", "-"])
        black = re.findall(r"black_start:([\d.]+)", err)
        checks.append(("no black frames", not black, f"{len(black)} black segment(s)"))
    return checks


def black_scenes(base, timeline):
    """Scene numbers whose stretch of the base render is blank (see _blank_runs)."""
    try:
        runs = _blank_runs(base)
    except Exception:
        return []
    return [i for i, sc in enumerate(timeline)
            if any(a < sc["start"] + sc["duration"] and b > sc["start"] for a, b in runs)]


def editorial(script, timeline, cfg):
    q = cfg.get("qa", {})
    checks = []
    words = sum(len(s["text"].split()) for s in script["scenes"])
    lo, hi = q.get("words", [90, 165])
    checks.append(("narration length", lo <= words <= hi, f"{words} words"))
    texts = [" ".join(re.findall(r"[a-z0-9']+", s["text"].lower())) for s in script["scenes"]]
    dupes = len(texts) - len(set(texts))
    checks.append(("no repeated lines", dupes == 0, f"{dupes} repeated"))
    fallback = sum(1 for s in timeline if (s.get("rights") or {}).get("source") == "stars"
                   or (not s.get("rights") and str(s.get("media", "")).endswith(".png")))
    checks.append(("real visuals", fallback <= q.get("max_fallback_scenes", 2),
                   f"{fallback} generated-starfield scene(s)"))
    yt = script["youtube"]
    checks.append(("title length", 10 <= len(yt["title"]) <= 100, f"{len(yt['title'])} chars"))
    checks.append(("description length", len(yt["description"]) <= 4900, f"{len(yt['description'])} chars"))
    checks.append(("tags size", len(",".join(yt.get("tags", []))) <= 480, f"{len(','.join(yt.get('tags', [])))} chars"))
    checks.append(("sources listed", bool(script.get("sources")), f"{len(script.get('sources', []))} source(s)"))
    last = yt["description"].rstrip().split("\n")[-1]
    tags = re.findall(r"#\w+", last)
    checks.append(("hashtags", bool(tags) and tags[0].lower() == "#shorts" and 3 <= len(tags) <= 5,
                   " ".join(tags) or "none on the last line"))
    from .render import music_library
    if music_library(cfg):  # the channel has music: every video must carry it (viewers asked for it)
        checks.append(("background music", bool(script.get("music")), script.get("music") or "voice only"))
    if cfg.get("disclosure", {}).get("ai_label", True):
        note = cfg["disclosure"].get("text", "")
        checks.append(("AI disclosure in description", note[:30] in yt["description"], ""))
    return checks


def _shingles(text, n=5):
    w = re.findall(r"[a-z0-9]+", text.lower())
    return {" ".join(w[i:i + n]) for i in range(max(0, len(w) - n + 1))}


def originality(script, cfg, exclude_folder=None):
    """Screens for near-duplicates of earlier scripts (5-word shingle Jaccard). A screen, not legal clearance."""
    mine = _shingles(" ".join(s["text"] for s in script["scenes"]))
    worst, worst_name = 0.0, ""
    for p in OUTPUT.glob("*/script.json"):
        if p.parent.name == exclude_folder:
            continue
        try:
            other = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if other.get("topic") == script.get("topic"):
            continue  # remakes of the same topic are expected to overlap
        theirs = _shingles(" ".join(s["text"] for s in other.get("scenes", [])))
        if mine and theirs:
            j = len(mine & theirs) / len(mine | theirs)
            if j > worst:
                worst, worst_name = j, p.parent.name
    limit = cfg.get("qa", {}).get("max_similarity", 0.25)
    return [("originality vs past videos", worst <= limit, f"max overlap {worst:.2f} ({worst_name or 'none'})")]


def gate(video, script, timeline, cfg, folder_name=None):
    checks = (technical(video, cfg, script.get("music")) + editorial(script, timeline, cfg)
              + originality(script, cfg, folder_name))
    result = {"pass": all(ok for _, ok, _ in checks),
              "checks": [{"name": n, "ok": bool(ok), "detail": d or ""} for n, ok, d in checks]}
    return result
