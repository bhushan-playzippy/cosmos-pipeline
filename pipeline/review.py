"""Reviewer agent: watches frames of the finished video next to the script, listens to the final mix (voice +
background music), and decides if it's good enough to post."""
from .llm import ask_json
from .util import run, log

SYSTEM = """You are a demanding senior editor at a top science Shorts channel. You review finished videos before
they go out. You are honest: you fail videos that would embarrass the channel, and you never praise by default."""


def _frames(video, timeline, workdir):
    """One frame from the middle of each scene (captions included, exactly as viewers see it)."""
    paths, t = [], 0.0
    for i, sc in enumerate(timeline):
        mid = t + sc["duration"] / 2
        t += sc["duration"]
        out = workdir / f"review_{i:02d}.jpg"
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{mid:.2f}", "-i", str(video), "-frames:v", "1",
             "-vf", "scale=360:-1", "-q:v", "4", str(out)])
        paths.append(out)
    return paths


def _audio(video, workdir):
    """The final mix as a small mono mp3, so the reviewer hears exactly what viewers hear (voice + music).
    None if it can't be extracted: the review then runs on frames alone rather than failing the video."""
    out = workdir / "review_audio.mp3"
    try:
        run(["ffmpeg", "-y", "-v", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "22050", "-b:a", "48k",
             str(out)])
    except RuntimeError as e:
        log(f"review: no audio for the reviewer ({str(e)[:80]})")
        return None
    return out


CRITERIA = ("hook", "accuracy", "visual_match", "visual_quality", "captions", "story")


def _numeric(scores):
    """Scores as numbers ("8", "8/10" → 8); anything unreadable is dropped, which then counts as unscored."""
    import re
    out = {}
    for k, v in (scores if isinstance(scores, dict) else {}).items():
        m = re.match(r"\s*(\d+(?:\.\d+)?)", str(v))
        if m:
            out[k] = float(m.group(1))
    return out


AUDIO_SCORE = """
- audio: narration clear and never drowned out; background music present, fitting the mood, audible in the pauses
  but clearly under the voice; no clicks, distortion or abrupt cuts?"""


def _scene_lines(timeline, recs):
    """Per scene: the exact line, what the image should show, and the asset's own metadata (source, title, date),
    so the reviewer judges fit against facts about the file, not only against what the frame resembles."""
    out = []
    for i, (sc, r) in enumerate(zip(timeline, recs)):
        a = r["asset"]
        meta = "; ".join(f"{k}: {str(a.get(k))[:120]}" for k in ("source", "title", "date", "description") if a.get(k))
        out.append(f"Scene {i} (frame {i + 1}): \"{sc['text']}\"\n   should show: {r['subject']} ({r['intended']})"
                   f"\n   asset: {meta or 'none recorded'}" + (" [same image as an earlier scene]" if a.get("reused_from") else ""))
    return "\n".join(out)


def review_video(video, timeline, script, cfg, workdir):
    from . import grounding
    frames = _frames(video, timeline, workdir)
    audio = _audio(video, workdir) if cfg.get("review", {}).get("listen", True) else None
    story = cfg.get("writer", {}).get("template") == "story"
    recs = grounding.records(timeline, script, story)
    scenes = _scene_lines(timeline, recs)
    total = sum(sc["duration"] for sc in timeline)
    hear = (f"\nYou also get the final AUDIO MIX (last attachment): narration over background music "
            f"({script.get('music') or 'no music track'})." if audio else "")
    prompt = f"""
You are given {len(frames)} frames, one from the middle of each scene of a {total:.0f}s vertical Short, in order.{hear}

TOPIC: {script['topic']}
NARRATION, scene by scene, with what each image should show and the image file's own metadata:
{scenes}

Score each 1–10:
- hook: does scene 0 stop the scroll in under 2 seconds?
- accuracy: anything still wrong, misleading or overclaimed in the narration? Recheck every number yourself,
  including its wording (radius vs diameter — "wide" means diameter —, mass vs weight, units, per second vs per hour).
- visual_match: does each frame fit what is being said? (a frame of Earth while talking about a neutron star = mismatch)
- visual_quality: sharp, clean, no stray text/labels/watermarks, nothing ugly or confusing?
  {cfg.get("review", {}).get("people_rule", "Any photo of a real, identifiable person (scientist, presenter, crowd) is a bad frame: score 5 or less.")}
- captions: readable, not covering the important part of the image?
- story: escalates and pays off, ending loops back to the hook?{AUDIO_SCORE if audio else ""}

Then judge EVERY scene's picture against its own line and its neighbours ("scenes" below):
- "MATCH": it shows the narrated subject or action itself (the right person, ship, place, body, event).
- "CONTEXTUAL": relevant background that doesn't show what the line says (a generic sky, a period scene, a
  related place, the subject at another moment).
- "MISMATCH": the wrong subject, era or event, or anything that would mislead. A real artifact (a watch, a wallet,
  a wreck or a museum piece, photographed later) under a line about an event is a MISMATCH: it shows an object,
  not what happened. A generic space image under a specific phenomenon is CONTEXTUAL at best.
Describe what the frame actually shows before deciding; read the metadata (a date decades after the story, or
a title saying "recovered", "museum" or "replica", tells you what the picture really is).

"pass" is true only if every score is at least 6 and accuracy is at least 8.

Return JSON:
{{"scores": {{"hook": 0, "accuracy": 0, "visual_match": 0, "visual_quality": 0, "captions": 0, "story": 0{', "audio": 0' if audio else ''}}},
  "pass": true,
  "bad_frames": [list of scene numbers with a bad or mismatched visual],
  "scenes": [{{"scene": 0, "shows": "what the frame actually shows", "decision": "MATCH|CONTEXTUAL|MISMATCH", "why": "..."}}],
  "issues": ["short, specific problems"],
  "summary": "one sentence verdict"}}
"""
    rv = ask_json(prompt, cfg, system=cfg.get("review", {}).get("system", SYSTEM), temperature=0.2,
                  strong=True, purpose="review", images=frames + ([audio] if audio else []))
    if isinstance(rv, list):  # models sometimes wrap the object in a list
        rv = rv[0] if rv and isinstance(rv[0], dict) else {}
    if not isinstance(rv, dict):
        rv = {}
    for f in frames + ([audio] if audio else []):
        f.unlink(missing_ok=True)
    heard_none = rv.pop("_no_audio", False)
    if heard_none:  # a fallback reviewer saw the frames only (QA checked the sound)
        (rv.get("scores") or {}).pop("audio", None)
        log("review: done by a fallback model on frames only (Gemini unavailable)")
    scores = rv["scores"] = _numeric(rv.get("scores"))
    # every criterion must be scored: a (fallback) model that leaves out visual_match used to pass on the rest
    missing = [k for k in CRITERIA + (("audio",) if audio and not heard_none else ()) if k not in scores]
    if missing:
        rv.setdefault("issues", []).append(f"reviewer did not score: {', '.join(missing)}")
    # the per-scene grounding: deterministic metadata rules + this reviewer's look (grounding.combine)
    rv["grounding"] = grounding.combine(recs, rv.get("scenes"))
    visual = grounding.gate(rv["grounding"], cfg)
    rv.setdefault("issues", []).extend(f"visual: {v}" for v in visual)
    rv["pass"] = (bool(rv.get("pass")) and not missing and all(v >= 6 for v in scores.values())
                  and scores.get("accuracy", 0) >= 8 and not visual)
    if visual and not rv.get("summary", "").startswith("visual"):
        rv["summary"] = f"visual grounding failed: {visual[0][:100]}. " + str(rv.get("summary", ""))
    log(f"review {'PASS' if rv['pass'] else 'FAIL'} {scores} — {rv.get('summary', '')}")
    for issue in rv.get("issues", []):
        log(f"  review: {issue}")
    return rv
