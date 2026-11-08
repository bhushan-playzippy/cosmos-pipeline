"""Segment agent: finds self-contained moments in a cleared source's transcript and scores them.

Deterministic (no model calls): windows start and end on sentence boundaries, so a clip never starts mid-thought
or cuts a punchline, and every score comes with the reasons behind it. The scores measure fit and clarity; they
are not a prediction that anything will go viral.

Scores (0–1): hook, completeness, standalone, pacing, relevance, clarity, energy → weighted total (segments.weights).
energy = how far the window's loudest second rises above the source's typical level (laughter, shouting, hype).
Type (for the daily mix): segments.types in the profile (name → regex), first match wins; else "follow_up".
"""
import re

from . import ingest, registry, rights, transcript
from ..util import log, event

DEPENDENT_START = re.compile(r"^(and|but|so|because|which|that|this|these|those|it|its|they|he|she|also|then|"
                             r"or|plus|which|however|anyway|yeah|um|uh)\b", re.I)
NEEDS_CONTEXT = re.compile(r"\b(as i (said|mentioned)|like i said|earlier|previous(ly)?|this slide|on the screen|"
                           r"here you can see|as you can see|in this video|next (up|slide|we)|later in|"
                           r"we'll get to|that one|the other one)\b", re.I)
# channel/series framing, not content: "We asked a NASA expert", "subscribe", "welcome to"
META = re.compile(r"\b(we asked an?|subscribe|welcome (back )?to|in today's|today we|hit the|link in)\b", re.I)
# the window ends while the speaker is starting a list or a build-up: the payoff is outside the clip
UNFINISHED = re.compile(r"\b(the first (thing|reason|step|one|danger|problem)|first of all|number one|"
                        r"there are (two|three|four|several|many)|here's (why|how|the thing))\b", re.I)
HOOKY = re.compile(r"\?|\b\d[\d,.]*\b|\b(imagine|surprising|actually|never|always|most|first|only|why|how|what if|"
                   r"secret|biggest|smallest|fastest|dangerous|incredible|turns out|did you know)\b", re.I)
TYPES = {
    "surprise": re.compile(r"\b(surpris\w*|actually|turns out|never|unexpected|weird|strange|no one|myth)\b", re.I),
    "explainer": re.compile(r"\b(because|means|works|how (it|we|do)|reason|so that|which is why|process)\b", re.I),
    "story": re.compile(r"\b(i remember|when i|when we|my first|i was|we were|the day|once)\b", re.I),
    "discovery": re.compile(r"\?|\b(what|why|how|discover\w*|found|new)\b", re.I),
}


def sentences(cues):
    """Word-timed sentences from caption cues."""
    out, cur = [], []
    for w in transcript.words(cues):
        cur.append(w)
        if re.search(r"[.?!][\"')\]]*$", w["word"]):
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return [{"start": s[0]["start"], "end": s[-1]["end"], "text": " ".join(w["word"] for w in s), "words": s}
            for s in out]


def _type(text, cfg=None):
    types = (cfg or {}).get("segments", {}).get("types")
    pats = {k: re.compile(v, re.I) for k, v in types.items()} if types else TYPES
    for name, pat in pats.items():
        if pat.search(text):
            return name
    return "follow_up"


def loudness(media):
    """Per-second momentary loudness (LUFS) of the whole source, via FFmpeg's ebur128 meter."""
    import subprocess
    err = subprocess.run(["ffmpeg", "-hide_banner", "-nostats", "-i", str(media), "-vn", "-af", "ebur128", "-f", "null",
                          "-"], capture_output=True, text=True).stderr
    per = {}
    for t, m in re.findall(r"t:\s*([\d.]+)\s+TARGET:.*?M:\s*(-?[\d.]+)", err):
        sec = int(float(t))
        per[sec] = max(per.get(sec, -120.0), float(m))
    return [per.get(i, -120.0) for i in range(max(per) + 1)] if per else []


def _energy(curve, start, end):
    if not curve:
        return None
    vals = sorted(v for v in curve if v > -70)
    if not vals:
        return 0.0
    median = vals[len(vals) // 2]
    peak = max(curve[int(start):int(end) + 1] or [-120.0])
    return round(max(0.0, min(1.0, (peak - median) / 12)), 2)  # +12 LU above typical = full score


def score_window(sents, cfg, curve=None):
    text = " ".join(s["text"] for s in sents)
    first = sents[0]["text"]
    dur = sents[-1]["end"] - sents[0]["start"]
    n_words = len(text.split())
    kw = [k.lower() for k in cfg["discovery"].get("keywords", [])]
    why = []
    hook = 0.4 + 0.3 * bool(HOOKY.search(first)) + 0.3 * (len(first.split()) <= 16)
    if HOOKY.search(first):
        why.append("opens with a question/number/strong claim")
    completeness = (0.5 if DEPENDENT_START.match(first) else 1.0) * (1.0 if re.search(r"[.?!]\W*$", text) else 0.6)
    if DEPENDENT_START.match(first):
        why.append("starts mid-thought (dependent opener)")
    if UNFINISHED.search(sents[-1]["text"]) or (len(sents) > 1 and UNFINISHED.search(sents[-2]["text"])
                                                and not re.search(r"\b(second|next|also|another)\b", text[-200:], re.I)):
        completeness *= 0.4
        why.append("ends while a list/build-up is just starting (payoff outside the clip)")
    standalone = 0.4 if NEEDS_CONTEXT.search(text) else 1.0
    if standalone < 1:
        why.append("refers to something outside the clip")
    meta = sum(1 for x in sents if META.search(x["text"]))
    norm = [re.sub(r"\W+", " ", x["text"].lower()).strip() for x in sents]
    repeats = len(norm) - len(set(norm)) + sum(1 for a, b in zip(norm, norm[1:]) if a and b and (a in b or b in a))
    if meta or repeats:
        standalone *= 0.5 ** (meta + repeats)
        why.append(f"{meta} framing line(s), {repeats} repeated line(s)")
    wps = n_words / max(dur, 0.1)
    pacing = 1.0 if 2.0 <= wps <= 3.6 else 0.6 if 1.4 <= wps <= 4.2 else 0.3
    relevance = min(1.0, sum(1 for k in kw if k in text.lower()) / 2) if kw else 0.5
    avg = n_words / len(sents)
    clarity = 1.0 if 6 <= avg <= 24 else 0.6
    w = cfg["segments"]["weights"]
    s = {"hook": round(hook, 2), "completeness": round(completeness, 2), "standalone": standalone,
         "pacing": pacing, "relevance": round(relevance, 2), "clarity": clarity}
    energy = _energy(curve, sents[0]["start"], sents[-1]["end"])
    if energy is not None and w.get("energy"):
        s["energy"] = energy
        if energy >= 0.6:
            why.append("big audio peak (laughter/hype)")
    total = sum(w.get(k, 0) * v for k, v in s.items()) / sum(w.get(k, 0) for k in s)
    if s["completeness"] == 1 and s["standalone"] == 1:
        why.append("complete thought that stands alone")
    return round(total, 3), s, why


def find(cfg, source_id):
    """Scores every sentence-aligned window of the source that fits the clip length, keeps the best
    non-overlapping, non-duplicate ones as candidates. Returns the new candidate records."""
    if not rights.is_cleared(cfg, source_id):
        raise ingest.UnauthorizedSource(f"{source_id} is not rights-cleared")
    sc = cfg["segments"]
    src = registry.get(cfg, source_id)
    max_len = min(sc.get("max_seconds", 40), (src.get("rights") or {}).get("max_clip_seconds") or 1e9)
    sents = sentences(ingest.cues_of(src))
    curve = None
    if sc.get("weights", {}).get("energy"):
        curve = src.get("loudness") or loudness(ingest.media_path(src))
        registry.update(cfg, source_id, loudness=curve)
    wins = []
    for i in range(len(sents)):
        for j in range(i, len(sents)):
            dur = sents[j]["end"] - sents[i]["start"]
            if dur > max_len:
                break
            if dur >= sc.get("min_seconds", 12):
                total, scores, why = score_window(sents[i:j + 1], cfg, curve)
                wins.append((total, i, j, scores, why))
    wins.sort(key=lambda w: -w[0])
    existing = registry.segments(cfg)
    picked = []
    for total, i, j, scores, why in wins:
        if total < sc.get("min_score", 0.6) or len(picked) >= sc.get("per_source", 3):
            break
        start, end = sents[i]["start"], sents[j]["end"]
        text = " ".join(s["text"] for s in sents[i:j + 1])
        if registry.is_duplicate(cfg, source_id, start, end, text, existing + picked):
            continue
        picked.append({"id": f"{source_id}@{start:.1f}-{end:.1f}", "source_id": source_id, "start": round(start, 2),
                       "end": round(end, 2), "text": text, "score": total, "scores": scores, "why": why,
                       "type": _type(text, cfg), "status": "candidate_selected", "created": registry.today()})
    ids = {p["id"] for p in picked}  # a window re-found after being skipped replaces its old record
    registry.save_segments(cfg, [s for s in existing if s["id"] not in ids] + picked)
    registry.update(cfg, source_id, status="segmented" if picked else "exhausted",
                    segments=src.get("segments", []) + [p["id"] for p in picked])
    event("segments", source=source_id, windows=len(wins), picked=len(picked))
    log(f"{source_id}: {len(wins)} windows scored, {len(picked)} candidate(s) kept")
    return picked


def next_candidate(cfg, role=None):
    """Best unused candidate from a still-cleared source, preferring the slot's role type (a hypothesis)."""
    cands = [s for s in registry.segments(cfg) if s["status"] == "candidate_selected"
             and rights.is_cleared(cfg, s["source_id"])]
    if not cands:
        return None
    from ..growth import type_boost
    boost = type_boost(cfg)  # learned from this channel's own views/day once groups have enough videos
    cands.sort(key=lambda s: (-(s["type"] == role), -s["score"] * boost.get(s["type"], 1.0)))
    return cands[0]
