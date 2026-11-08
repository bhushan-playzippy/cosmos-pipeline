"""Compliance agent: independent review of a rendered Short before it may enter the publishing queue.

Hard checks (any failure quarantines; no score can override them):
  rights cleared now · edit within permitted use · attribution on screen and in description · original narration
  share · technical (decodes, 9:16 size, duration, loudness, silence, black frames) · metadata limits ·
  unique title · AI reviewer (if enabled) finds no misrepresentation, unsupported claim or policy problem
Scored checks (thresholds in compliance.min_*): segment score.
"""
import re

from . import rights
from .. import qa
from ..llm import ask_json
from ..util import log, run

SPAM = re.compile(r"#(fyp|foryou|viral|trending|xyzbca)\b", re.I)


def _frames(video, n, work):
    from ..util import media_duration
    dur = media_duration(video)
    paths = []
    for i in range(n):
        out = work / f"cmp_{i}.jpg"
        run(["ffmpeg", "-y", "-v", "error", "-ss", f"{dur * (i + 0.5) / n:.2f}", "-i", str(video), "-frames:v", "1",
             "-vf", "scale=360:-1", "-q:v", "4", str(out)])
        paths.append(out)
    return paths


def ai_review(video, seg, src, narration, meta, cfg, work):
    """Model check of faithfulness and policy. Fails closed: an error is a failed review, not a pass."""
    frames = _frames(video, 6, work)
    prompt = f"""
You review a Short before publication. It is: narrated INTRO → clip from an authorized source → narrated OUTRO.
SOURCE: "{src.get('title')}" by {src.get('creator')}. Description: {src.get('description', '')[:800]}
CLIP TRANSCRIPT (speaker's exact words): "{seg['text']}"
INTRO: "{narration['intro']}"
OUTRO: "{narration['outro']}"
TITLE: "{meta['title']}"
6 frames of the finished video are attached in order.

Answer strictly:
- misrepresents: does the narration or title change or overstate what the speaker said or meant?
- unsupported: list claims in intro/outro/title not supported by the transcript or description.
- standalone: does the clip make sense to someone who hasn't seen the full video?
- policy: anything harmful, shocking, medical/financial advice, or misleading? (list)
- value: 1–10, how much the intro/outro add beyond the raw clip (explanation, context, insight).
Return JSON: {{"misrepresents": false, "unsupported": [], "standalone": true, "policy": [], "value": 0, "summary": ""}}
"""
    try:
        rv = ask_json(prompt, cfg, temperature=0.1, images=frames)
        if isinstance(rv, list):
            rv = rv[0] if rv and isinstance(rv[0], dict) else {}
    except SystemExit as e:
        rv = {"error": str(e)}
    except Exception as e:
        rv = {"error": f"{type(e).__name__}: {e}"[:200]}
    finally:
        for f in frames:
            f.unlink(missing_ok=True)
    return rv


def review(video, seg, src, narration, meta, edit, cfg, work, used_titles=()):
    c = cfg["compliance"]
    r = src.get("rights") or {}
    checks = []
    add = lambda name, ok, detail="": checks.append({"name": name, "ok": bool(ok), "detail": str(detail)})

    add("rights cleared", rights.is_cleared(cfg, src["id"]), (src.get("review") or {}).get("status"))
    lim = r.get("max_clip_seconds")
    add("within permitted clip length", not lim or edit["clip_seconds"] <= lim + 0.5,
        f"{edit['clip_seconds']}s of source (limit {lim or 'none'})")
    needed = [u for u in cfg["rights"].get("required_uses", []) if r.get("pathway") == "written_permission"
              and u not in r.get("permitted_uses", [])]
    add("uses covered by permission", not needed, f"missing: {needed}" if needed else "")
    if r.get("attribution_required"):
        add("attribution on screen", bool(edit.get("attribution_on_screen")), edit.get("attribution_on_screen"))
        add("attribution in description", r.get("attribution_text", "") in meta["description"], r.get("attribution_text"))
    add("source credited in description", str(src.get("creator", "")) in meta["description"])
    req = (r.get("requirements") or {})
    missing = ([h for h in req.get("hashtags", []) if h not in meta["description"]]
               + [l for l in req.get("description_lines", []) if l not in meta["description"]]
               + ([req["mention"]] if req.get("mention") and req["mention"] not in meta["description"] else [])
               + [t for t in req.get("tags", []) if t not in meta["tags"]])
    if req:
        add("campaign/creator requirements met", not missing, f"missing: {missing}" if missing else "all present")
    add("original narration share", edit["original_share"] >= c.get("min_original_share", 0.25),
        f"{edit['original_share']:.0%} (min {c.get('min_original_share', 0.25):.0%})")
    add("segment score", seg["score"] >= c.get("min_segment_score", 0.6), seg["score"])

    for name, ok, detail in qa.technical(video, cfg):
        add(name, ok, detail)

    add("title length", 10 <= len(meta["title"]) <= 70, f"{len(meta['title'])} chars")
    add("description length", len(meta["description"]) <= 4900, len(meta["description"]))
    add("tags size", len(",".join(meta["tags"])) <= 480, len(",".join(meta["tags"])))
    tags_in_desc = re.findall(r"#\w+", meta["description"])
    allowed = max(cfg["package"].get("max_hashtags", 3), len(req.get("hashtags", [])))
    add("hashtags sparing", len(tags_in_desc) <= allowed and not SPAM.search(
        meta["description"]), f"{len(tags_in_desc)} hashtag(s)")
    add("title unique on channel", meta["title"].lower() not in {t.lower() for t in used_titles})
    if cfg.get("disclosure", {}).get("ai_label"):
        add("AI disclosure in description", cfg["disclosure"]["text"][:30] in meta["description"])

    ai = None
    if cfg.get("review", {}).get("enabled", True):
        ai = ai_review(video, seg, src, narration, meta, cfg, work)
        add("AI review ran", "error" not in ai, ai.get("error", ""))
        if "error" not in ai:
            add("no misrepresentation", not ai.get("misrepresents"), ai.get("summary", ""))
            add("no unsupported claims", not ai.get("unsupported"), ai.get("unsupported"))
            add("stands alone", ai.get("standalone", False))
            add("no policy issues", not ai.get("policy"), ai.get("policy"))
            add("adds value", (ai.get("value") or 0) >= c.get("min_value", 6), ai.get("value"))
    result = {"pass": all(ch["ok"] for ch in checks), "checks": checks, "ai": ai}
    failed = [ch["name"] for ch in checks if not ch["ok"]]
    log(f"compliance {'PASS' if result['pass'] else 'FAIL'}" + (f": {', '.join(failed)}" if failed else ""))
    return result
