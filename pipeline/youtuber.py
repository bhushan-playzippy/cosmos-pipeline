"""Packaging agent: a big-YouTuber persona writes title, description, tags, hashtags, pinned comment and release time."""
import json

from . import facts
from .llm import ask_json
from .performance import guidance
from .util import log

SYSTEM = """You are a science YouTuber with 10M+ subscribers who has packaged thousands of viral Shorts.
You know what makes people click, watch to the end and comment, but you never write clickbait the video
can't pay off, and you never add facts that are not in the narration."""


def package(script, cfg):
    ch = cfg["channel"]
    narration = " ".join(s["text"] for s in script["scenes"])
    prompt = f"""
Package this Short for YouTube and Instagram Reels.

Channel: "{ch['name']}" — {ch['niche']}. Audience: {ch['audience']}. {ch.get('audience_rule', '')}
Topic: {script['topic']}
Narration (the ONLY facts you may use): {narration}
Sources: {json.dumps(script.get('sources', []))}
{guidance(cfg)}

Write:
- youtube.title: max 60 chars, curiosity gap, honest, the key number or comparison if it has one. No " #Shorts" (added later).
  Name the specific, recognisable subject (the person, ship, planet, object) in the first few words. Never a
  vague title that hides it: on our channels "They watched the fire, not knowing what was coming" got 2 views,
  "Joan of Arc's 70-Foot Castle Tower Leap" got 1,377.
- youtube.hook_text: the on-screen headline for the first 2–3 seconds, 3–6 words, the single most extreme TRUE
  fact or number from the narration (e.g. "76 DAYS ALONE ON A RAFT", "1 TEASPOON = 4 BILLION TONS"). Not the title
  repeated, no question marks, no hashtags.
- title and hook_text claim nothing stronger than the narration: every number exactly as narrated, in the same
  unit, and no superlative ("first", "largest"), magnitude ("billions") or intensifier ("deadly") the narration
  doesn't state. The verifier checks this in code and fails the video otherwise.
- youtube.description: line 1 is a hook that makes people rewatch; then 2–3 sentences of context; then
  "Sources:" with the sources; then {cfg.get("packaging", {}).get("hashtag_rule", "3–5 hashtags on the last line (#Shorts first)")}.
- youtube.tags: 12–18 tags, mix broad (space, astronomy) and exact-match search phrases people really type.
- youtube.pinned_comment: a question that sparks debate in the comments, max 150 chars.
- description, pinned_comment and instagram.caption repeat facts from the narration only: no number,
  superlative ("first", "largest") or new fact the narration doesn't state (the claim check rejects them).
- instagram.caption: 1–2 punchy lines + a question that drives comments.
- instagram.hashtags: exactly 5 (Instagram's limit), most specific first: the exact subject, the niche, one
  broad tag. No #fyp/#viral spam.
- release: the best time to publish this Short for this audience (give it in India time, IST),
  as "hour_ist" (0–23) plus "day_hint" and a one-line "reason".

Return JSON:
{{"youtube": {{"title": "...", "hook_text": "...", "description": "...", "tags": ["..."], "pinned_comment": "..."}},
  "instagram": {{"caption": "...", "hashtags": ["#..."]}},
  "release": {{"hour_ist": 18, "day_hint": "...", "reason": "..."}}}}
"""
    pkg = ask_json(prompt, cfg, system=cfg.get("packaging", {}).get("system", SYSTEM), temperature=0.7, purpose="packaging")
    if pkg.get("youtube", {}).get("title"):
        before = facts.snapshot(script)
        script["youtube"] = pkg["youtube"]
        facts.record(script, "packaging", before)
    if pkg.get("instagram", {}).get("caption"):
        script["instagram"] = pkg["instagram"]
    rel = pkg.get("release") or {}
    try:
        rel["hour_ist"] = max(0, min(23, int(rel.get("hour_ist", cfg["youtube"].get("default_hour_ist", 18)))))
    except (TypeError, ValueError):
        rel["hour_ist"] = cfg["youtube"].get("default_hour_ist", 18)
    script["release"] = rel
    log(f"packaged: \"{script['youtube']['title']}\" — release {rel['hour_ist']}:00 IST ({rel.get('reason', '')[:80]})")
    return script
