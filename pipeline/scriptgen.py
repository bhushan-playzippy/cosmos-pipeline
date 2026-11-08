"""Script writer: turns a topic into a scene-by-scene script + metadata."""
import json

from . import facts
from .llm import ask_json
from .performance import guidance
from .render import music_mood_rule
from .util import log

SYSTEM = """You are the head writer of a top faceless science Shorts channel.
You write tight, accurate, high-retention 40–55 second scripts.
Accuracy is non-negotiable: use only well-established facts (NASA, ESA, peer-reviewed).
Never invent statistics. Round honestly ("about", "roughly"). If unsure, choose a different fact."""


def write_script(topic, cfg):
    ch = cfg["channel"]
    lang = ch.get("language", "en")
    lang_rule = ("Write the narration in natural spoken Hindi (Devanagari script), the way a popular Hindi "
                 "science YouTuber talks; common English science words are fine."
                 if lang == "hi" else "Write the narration in simple, punchy spoken English.")

    brief = None
    if cfg.get("writer", {}).get("research"):  # 5 Nov: the space writer wrote from memory ("India reached Mars orbit")
        from .storygen import research, _notes_block  # while the article said "inserted into Mars orbit", so true
        brief = research(topic, cfg)                    # lines failed as "no document evidence": write from the notes
    notes = (_notes_block(brief) + "Every number, date and first/only/biggest claim must be one these notes state, in "
             "their words where you can: the independent checker looks for your wording in these articles.\n"
             ) if brief else ""
    prompt = f"""
Channel: "{ch['name']}" — {ch['niche']}.
Signature format: {ch['signature']}
Audience: {ch['audience']}. {ch.get('audience_rule', '')}
Topic: {topic['topic']}
Suggested comparison: {topic.get('angle', '')}
Hook idea: {topic.get('hook_idea', '')}
{notes}{guidance(cfg)}

{lang_rule}

STRUCTURE
1. Hook (scene 1): max 12 words. Creates a question the viewer NEEDS answered. No greetings, no "did you know".
   Name one familiar thing the viewer can picture at once (a car, a teaspoon, Everest) next to the space fact;
   at most ONE simple number, no jargon or mission names that need prior knowledge. (Weekly retention, 1 Nov:
   "driving to the nearest star", "ISS speed", "a volcano vs Everest" held 79–159%; "660,000 times", "8Gs",
   "red dwarf", "18 trains" held 27–37%.) Scene 2 shows the comparison, not background.
2. 5–8 more scenes, each 1–2 short sentences (max ~22 words). Escalate: each scene raises the stakes.
3. The everyday comparison is the payoff — make it vivid and visual. Compare against things with ONE published
   measurement (a regulation basketball, a school bus, the Empire State Building, the New York–LA drive), never
   human-variable ones (a blink, a finger snap, a heartbeat, a walk): the independent checker uses its own value
   for those and fails the video. Prefer "about"/"roughly" to false precision.
4. Final scene: a twist or final mind-blow that echoes the hook's key words, so the video replays smoothly into
   the start. It must be a NEW sentence: never repeat the hook (or any other line) word for word.
Total narration: 110–140 words. No "in this video", no "like and subscribe", no emojis in narration.

VISUALS — for every scene give:
- "visual": 1–3 word stock-video search query that will find real footage on Pexels (e.g. "galaxy", "sun surface", "astronaut", "earth from space", "basketball", "school bus").
- "nasa": 1–3 word NASA image library search query as backup (e.g. "Saturn rings", "Orion nebula").
- "subject": the object this line is about (e.g. "neutron star", "Saturn's rings"): its picture must show it.
- "compare" (on the 1–2 scenes where the payoff comparison is made, otherwise omit): a TO-SCALE graphic spec,
  {{"kind": "diameter" or "length", "unit": "km", "items": [{{"label": "Earth", "value": 12742}}, {{"label": "Sun",
  "value": 1391000}}]}} with 2–3 items in the SAME unit and real, well-established values. This graphic is the
  channel's signature visual: it is REQUIRED on at least one scene whenever the topic involves a size, height,
  length or distance that can be compared.

METADATA
- youtube.title: max 70 chars, curiosity-driven, honest, ends with " #Shorts"
- youtube.description: 2–3 sentences expanding the fact + a line "Sources:" listing the sources + 3 hashtags.
- youtube.tags: 10–15 tags.
- instagram.caption: 1–2 punchy lines + a question to drive comments.
- instagram.hashtags: 12–18 relevant hashtags (mix of big and niche).
- sources: list of 1–3 real sources (organisation + page/article name) that back the facts.
- ai_visuals_realistic: false (pipeline sets this).
{music_mood_rule(cfg)}

Return JSON exactly in this shape:
{{
  "topic": "...",
  "scenes": [{{"text": "...", "visual": "...", "nasa": "...", "subject": "..."}}],
  "youtube": {{"title": "...", "description": "...", "tags": ["..."]}},
  "instagram": {{"caption": "...", "hashtags": ["#..."]}},
  "sources": ["..."],
  "music_mood": "..."
}}
"""
    for attempt in range(3):
        script = ask_json(prompt, cfg, system=SYSTEM, strong=True, purpose="write")
        words = sum(len(s.get("text", "").split()) for s in script.get("scenes", []))
        if words >= 100:
            break
        log(f"script only {words} words (needs 110–140 for 40–55s), rewriting")
    script.setdefault("topic", topic["topic"])
    facts.record(script, "write")
    from .storygen import polish
    script = polish(script, cfg)       # first, so the fact-checker also checks the rewritten hook/ending
    script = fact_check(script, cfg)
    script = polish(script, cfg)       # again only if the checker made the hook long (no call otherwise)
    if brief:  # after the checks (they don't need 4,500 characters of notes): verify reads its articles from here
        script["research"] = brief
    scenes = [s for s in script.get("scenes", []) if s.get("text", "").strip()]
    if len(scenes) < 4:
        raise RuntimeError("Script came back too short, try again")
    script["scenes"] = scenes
    from .virality import drop_repeats
    if drop_repeats(script):
        log("dropped a line that repeated an earlier one word for word")
    return script


CHECKER = """You are a strict science fact-checker for a space channel. You check every number and comparison
with real arithmetic against NASA/ESA/ISRO-level values. You never let a vivid comparison survive if it is wrong."""


def fact_check(script, cfg):
    """Second pass: verify every claim (incl. the math behind comparisons) and fix anything wrong."""
    prompt = f"""
Fact-check this Shorts script. For EVERY scene, check each number, unit and comparison.
For comparisons ("heavier than X", "as far as Y"), write out the actual values and do the arithmetic.
Check the exact wording of every measurement: radius vs diameter ("wide" means diameter), mass vs weight,
km vs miles, per second vs per hour, "about" vs exact. A right number with the wrong word is WRONG.
Also check what each line IMPLIES, not just what it literally says (e.g. "a star as heavy as the Sun becomes a
neutron star" implies the Sun could become one, which is false).
If a claim is wrong, misleading, exaggerated or unverifiable, rewrite that scene so it is correct while keeping the same
punchy tone and the same length (total narration must stay 110–140 words). Also fix the same claim anywhere in youtube/instagram text.
Keep "visual", "nasa" and "subject" fields. Check every "compare" value too (same unit across items, correct numbers);
fix wrong values, or delete the "compare" field if you can't verify it. Keep the JSON shape identical.

SCRIPT:
{json.dumps(script, ensure_ascii=False)}

Return JSON: {{"checks": [{{"claim": "...", "verdict": "ok|fixed", "working": "values + arithmetic"}}],
"script": {{...the full corrected script...}}}}
"""
    from .llm import family
    writer = (script.get("provenance") or {}).get("write")  # the writer's own second pass stays in its family, so
    data = ask_json(prompt, cfg, system=CHECKER, temperature=0.2, strong=True, purpose="fact_check",  # verify keeps
                    prefer=writer, prefer_family=family(writer, cfg) if writer else None)  # 2 independent families
    fixed = data.get("script") or script
    checks = data.get("checks", [])
    for c in checks:
        if c.get("verdict") == "fixed":
            log(f"fact-check fixed: {c.get('claim', '')[:100]}")
    fixed["fact_checks"] = checks
    fixed.setdefault("topic", script["topic"])
    fixed["provenance"] = dict(script.get("provenance") or {})
    fixed["fact_trail"] = list(script.get("fact_trail") or [])
    facts.record(fixed, "fact_check", facts.snapshot(script))
    for k, v in script.items():  # the checker sometimes drops fields it didn't touch (31 Oct: "sources", so the
        if not fixed.get(k):     # video was quarantined at "sources listed")
            fixed[k] = v
    return fixed


def add_ai_disclosure(script, cfg):
    """Labels the post as AI-made in the text people actually see (YouTube flag is set at upload)."""
    if not cfg.get("disclosure", {}).get("ai_label", True):
        return
    note = cfg["disclosure"].get("text", "Made with AI: script, voice and visuals are AI-assisted.")
    yt, ig = script["youtube"], script["instagram"]
    if note not in yt["description"]:
        yt["description"] = f"{yt['description'].rstrip()}\n\n{note}"
    if note not in ig["caption"]:
        ig["caption"] = f"{ig['caption'].rstrip()}\n\n{note}"
    tags = ig.setdefault("hashtags", [])
    if "#aigenerated" not in [t.lower() for t in tags]:
        tags.append("#AIgenerated")
