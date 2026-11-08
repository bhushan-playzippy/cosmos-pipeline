"""Story writer: true stories (history, survival, mysteries, weird-but-true) as scene-by-scene Shorts scripts.

Used instead of scriptgen.py when a profile sets writer.template: story. Same output shape, so voice, visuals,
captions, render, QA, review and publishing are shared with the original channel.

Truth rules: real events only, well documented (encyclopedias, museums, major newspapers, academic sources).
Legends and disputed details are either left out or said to be disputed. A second pass fact-checks every name,
date, number and causal claim.

Research first (3 Nov): the writer used to work from memory, and the independent check then rejected what it
couldn't support. That day Shackleton ("500 days after the hull was crushed"), the Hindenburg (a survivor
"inside the hull" the sources don't describe) and BA Flight 9 lost their topics to this. Now `research` fetches
the subject's Wikipedia articles (free, no key) and hands the writer numbered notes. The writer builds a
chronological timeline from them, uses only details the notes state, and says "unsupported" when the notes
don't carry the topic's premise. That fails the topic at stage `research`, before any fact-check or verify call.
The fact-check gets the same notes, plus a deterministic list of narrated numbers the notes never state, to
remove or correct. The independent verifier never sees the notes; it still checks blind.
"""
import json

from . import facts
from .llm import ask_json
from .performance import guidance
from .render import music_mood_rule
from .util import log, event

SYSTEM = """You are the head writer of a top faceless true-story Shorts channel. You find the single most
jaw-dropping TRUE detail of a real event and build a 40–55 second story around it. You never invent dialogue,
names, numbers or scenes. If a detail is legend or disputed, you leave it out or say so."""

CHECKER = """You are a strict history fact-checker. You verify every name, date, place, number and cause against
well-documented sources, and you remove anything that is legend, exaggeration or unverifiable."""


class Unsupported(ValueError):
    stage = "research"  # the topic's premise isn't in the sources: not a writer failure, production goes on


def research(topic, cfg, limit=None):
    """Numbered notes from the Wikipedia articles that best match the topic: the sentences that share the most
    distinctive words with it (the article's lead always comes along), in their original order, preferring ones
    with a date or number. Returns {"sources": [{title, url}], "notes": [str]}, or None if Wikipedia has nothing
    (the writer then works as before, and the independent check is unchanged)."""
    from . import evidence
    from .visuals import key_words
    limit = limit or cfg.get("writer", {}).get("research_chars", 4500)
    docs = []
    for q in dict.fromkeys(filter(None, [topic.get("subject_query"), proper_nouns(topic["topic"]),
                                         headline(topic["topic"])])):
        docs = evidence.reference(q, pages=2)  # 3 Nov: "Apollo 13 explosion crippled by..." found no article;
        if docs:                               # its proper nouns "Apollo 13" find it
            break
    import math, re

    def terms(text):  # plural-blind, numbers kept: "1201 ... program alarms" must meet "1201 computer alarm"
        nums = {n for n in re.findall(r"\b\d{3,}\b", str(text)) if not 1000 <= int(n) <= 2100 or n in topic["topic"]}
        return {w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w for w in key_words(text)} | nums

    want = terms(topic["topic"] + " " + topic.get("angle", ""))
    bags = [[terms(s) for s in sents] for _t, _u, sents in docs]
    n = sum(map(len, bags)) or 1
    # a topic word that is in hundreds of sentences ("Apollo", "Moon") says little about which one tells THIS
    # story; a rare one ("alarm", "1201") says a lot. Plain word counts kept 22 generic Apollo 11 sentences and
    # dropped the one about the 1202 alarm, so research rejected a true story (4 Nov; Titanic/Ballard too)
    idf = {w: math.log((n + 1) / (1 + sum(w in b for bag in bags for b in bag))) + 1 for w in want}
    top = max(idf.values(), default=1)
    picked = []
    for d, (_title, _url, sents) in enumerate(docs):
        for i, sent in enumerate(sents):
            hits = sum(idf[w] for w in want & bags[d][i]) / top
            lead = d == 0 and i < 6
            if hits or lead:
                picked.append((-(hits + any(c.isdigit() for c in sent) + 2 * lead), d, i, sent))
    keep, size = [], 0
    for _score, d, i, sent in sorted(picked):
        if size + len(sent) <= limit:
            keep.append((d, i, sent))
            size += len(sent)
    if not keep:
        return None
    return {"sources": [{"title": t, "url": u} for t, u, _s in docs],
            "notes": [sent for _d, _i, sent in sorted(keep)]}


def proper_nouns(topic, n=3):
    """The topic's names, numbers kept: "...Apollo 13 explosion..." → "Apollo 13"; "...King Tutankhamun's tomb"
    → "King Tutankhamun"; "...British Airways Flight 9..." → "British Airways Flight 9"."""
    import re
    words = [re.sub(r"['’]s\b", "", w) for w in re.findall(r"\b[A-Z][A-Za-z'’\-]*(?:\s+\d+\b)?", topic)]
    return " ".join([w for w in words if w.split()[0].lower() not in {"the", "a", "an", "in", "of", "on"}][:n])


def headline(topic):
    """The topic's subject as a search query: "The 1981 British Airways Flight 9 flying through a volcanic ash
    cloud..." → "British Airways Flight 9". The whole sentence found the airline's history page (3 Nov)."""
    import re
    core = re.sub(r"^(the )?(\d{4}s? )?", "", topic.strip(), flags=re.I)
    head = re.split(r"\b(who|whose|where|that|which|when|while|after|during|flying|trapped|caught|survived)\b", core)[0]
    return head.strip(" ,.-") or topic


def _notes_block(brief):
    if not brief:
        return ""
    src = "; ".join(f"{s['title']} ({s['url']})" for s in brief["sources"])
    lines = "\n".join(f"[{n + 1}] {t}" for n, t in enumerate(brief["notes"]))
    return f"""
RESEARCH NOTES: sentences from {src}. These are the ONLY facts you may state.
{lines}
"""


def unsupported_numbers(script, brief):
    """Narrated numbers (years included) that no research note states: what the fact-check must fix or drop."""
    if not brief:
        return []
    have = facts.numbers(" ".join(brief["notes"]))
    out = []
    for sc in script.get("scenes", []):
        for v in facts.numbers(sc.get("text", "")):
            if v > 3 and v not in out and not any(facts.close(v, h, 0.02) for h in have):
                out.append(v)
    return out


def write_script(topic, cfg):
    ch = cfg["channel"]
    brief = research(topic, cfg) if cfg.get("writer", {}).get("research", True) else None
    rules = """
RESEARCH RULES (the notes above are your only source)
- First build "timeline": the story's events in chronological order, each {"when": "...", "event": "...",
  "notes": [note numbers]}, using only dates the notes give.
- Every date, duration, number, person, place and cause in the narration must come from a note; give each scene
  "notes": [the note numbers it uses]. A duration is computed from two dated events in the timeline, never
  rounded up or guessed. Use only details the notes state, not ones you remember.
- No quotation unless a note contains those exact words. No "because"/motive unless a note states it.
- Leave out any detail the notes don't state rather than guessing.
- If the notes don't support the story's premise (the topic itself), return {"unsupported": "what is missing"}.
""" if brief else ""
    timeline_shape = '\n  "timeline": [{"when": "...", "event": "...", "notes": [1]}],' if brief else ""
    notes_field = ', "notes": [1]' if brief else ""
    prompt = f"""
Channel: "{ch['name']}" — {ch['niche']}.
Signature format: {ch['signature']}
Audience: {ch['audience']}. {ch.get('audience_rule', '')}
Story: {topic['topic']}
Angle: {topic.get('angle', '')}
Hook idea: {topic.get('hook_idea', '')}
{guidance(cfg)}
{_notes_block(brief)}{rules}
Write the narration in simple, gripping spoken English, like a great storyteller talking to one person.

STRUCTURE
1. Hook (scene 1): max 12 words. The most shocking TRUE detail, stated plainly, so the viewer NEEDS the rest.
   No greetings, no "did you know", no "in this video". Never open on a date ("In January 1925, ..."): the
   date goes in scene 2. Open on a person and what they did (Caesar demanding a higher ransom, Joan leaping
   from a tower), not on an institution or a statistic: those held 96–107% in the 1 Nov weekly retention, while
   statistic-led openings held 40–45%.
2. 5–8 more scenes, each 1–2 short sentences (max ~22 words): who, when, where in one line, then escalate.
   Every scene adds a new concrete detail (a number, a name, a place, a consequence). No filler.
3. A turn near the end: the twist, the cost, or the detail nobody remembers.
4. Final scene: a NEW line that lands the meaning AND echoes the hook's key words, so the video loops back into
   it. Never repeat the hook (or any other line) word for word.
Total narration: 95–120 words (about 45 seconds). No emojis, no "like and subscribe". Never invent quotes.

VISUALS
- top-level "subject_query": 2–3 words that find REAL photos of the story's main subject on Wikimedia Commons
  (e.g. "Wojtek bear", "Violet Jessop", "Molasses Flood Boston"). Used whenever a scene's own search finds nothing.
For every scene give:
- "commons": 2–5 word Wikimedia Commons search that finds a REAL historical photo, painting, map or document of
  the actual subject (e.g. "RMS Titanic 1912", "Phineas Gage", "Apollo 13 crew", "Pompeii ruins").
- "visual": 1–3 word generic stock-footage search as a fallback (e.g. "stormy ocean", "old newspaper", "candle").
- "subject": 2–4 words naming who or what THIS line is about (e.g. "Horace Hunley", "USS Housatonic").
- "shows": what the picture should show for this line (the person, ship, place or event itself; an object found
  later only if the line talks about that object).

METADATA
- youtube.title: max 60 chars, curiosity-driven and honest (no claim the story doesn't pay off), ends with " #Shorts".
- youtube.description: 2–3 sentences of context + a line "Sources:" listing the sources + hashtags line.
- youtube.tags: 10–15 tags (the event, people, place, era, "true story", "history").
- hashtags: exactly 4, no spaces, relevant: #Shorts first, then the topic (e.g. #Titanic), then 2 niche tags
  (e.g. #History #TrueStory). Put them as the last line of youtube.description too.
- instagram.caption: 1–2 punchy lines + a question that drives comments.
- instagram.hashtags: 12–18 relevant hashtags.
- sources: 2–3 real, checkable sources (encyclopedia/museum/newspaper/academic + article name).
{music_mood_rule(cfg)}

Return JSON exactly in this shape:
{{
  "topic": "...",
  "subject_query": "...",{timeline_shape}
  "scenes": [{{"text": "...", "commons": "...", "visual": "...", "subject": "...", "shows": "..."{notes_field}}}],
  "youtube": {{"title": "...", "description": "...", "tags": ["..."]}},
  "hashtags": ["#Shorts", "#...", "#...", "#..."],
  "instagram": {{"caption": "...", "hashtags": ["#..."]}},
  "sources": ["..."],
  "music_mood": "..."
}}
"""
    for attempt in range(3):
        script = ask_json(prompt, cfg, system=SYSTEM, strong=True, purpose="write")
        if brief and isinstance(script, dict) and script.get("unsupported") and not script.get("scenes"):
            event("research_unsupported", topic=topic["topic"], why=str(script["unsupported"])[:200])
            raise Unsupported(f"research: the sources don't support this story ({str(script['unsupported'])[:160]})")
        words = sum(len(s.get("text", "").split()) for s in script.get("scenes", []))
        if 90 <= words <= 135:
            break
        log(f"story is {words} words (needs 95–120 for ~45s), rewriting")
    script.setdefault("topic", topic["topic"])
    if brief:
        script["research"] = {**brief, "timeline": script.pop("timeline", None) or []}
    facts.record(script, "write")
    script = polish(script, cfg)       # first, so the fact-checker also checks the rewritten hook/ending
    script = fact_check(script, cfg)
    script = polish(script, cfg)       # again only if the checker made the hook long/dated (no call otherwise)
    scenes = [s for s in script.get("scenes", []) if s.get("text", "").strip()]
    if len(scenes) < 4:
        raise RuntimeError("Story came back too short, try again")
    script["scenes"] = scenes
    from .virality import drop_repeats
    if drop_repeats(script):
        log("dropped a line that repeated an earlier one word for word")
    return script


def polish(script, cfg):
    """One small extra call, only when needed: a hook over 12 words, an ending that doesn't loop into it, or an
    ending that just repeats the hook word for word (QA rejects repeated lines, and it wastes the payoff).
    Rewrites just the first and last lines; every fact stays as the fact-checker left it. Used by both writers."""
    from .virality import score, same_line
    scenes = script.get("scenes", [])
    if len(scenes) < 2:
        return script
    v = score({**script, "youtube": {"title": "x", "description": "#Shorts #a #b"}}, 40)["points"]
    echo = any(same_line(scenes[-1]["text"], s["text"]) for s in scenes[:-1])
    if v["hook"] == 2 and v["loop"] == 2 and not echo:
        return script
    prompt = f"""
Rewrite ONLY the first and the last line of this Short. Keep every fact exactly; add no new facts.
- First line: max 12 words, the most surprising true detail, phrased so the viewer NEEDS the rest (stakes or a
  question). No greeting, no "did you know", never a date first. Use only facts already in the story.
  Every number and size exactly as the story states it: never round up, never add a magnitude ("billions",
  "millions") or an intensifier ("lethal", "instantly", "every") the story doesn't support (1 Nov: a polished
  "billions of tons of lethal plasma" failed the independent check and killed the video).
- Last line: lands the meaning and reuses 2–3 of the first line's key words, so the video loops back into it.
  It must be a different sentence from the first line, never a word-for-word repeat.
First line now: "{scenes[0]['text']}"
Last line now: "{scenes[-1]['text']}"
Story for context: {" ".join(s['text'] for s in scenes[1:-1])}
Return JSON: {{"first": "...", "last": "..."}}
"""
    try:
        out = ask_json(prompt, cfg, temperature=0.6, purpose="polish")
        if (isinstance(out, dict) and out.get("first") and len(out["first"].split()) <= 14 and out.get("last")
                and not same_line(out["first"], out["last"])):
            # checked in Python, not trusted to the prompt: the rewrite may not state a number, magnitude or
            # intensifier the story doesn't (1 Nov: "billions of tons of lethal plasma" came from this step)
            before = facts.snapshot(script)
            story = " ".join(s["text"] for s in scenes) + " " + str(script.get("topic", ""))
            nums, words = facts.added(story, out["first"] + " " + out["last"])
            names = facts.new_names(story, out["first"] + " " + out["last"])  # no new people, ships or places either
            if nums or words or names:
                log(f"hook polish rejected: it adds {', '.join([f'{n:g}' for n in nums] + words + names)}")
                return script
            log(f"hook polished: \"{out['first']}\"")
            scenes[0]["text"], scenes[-1]["text"] = out["first"].strip(), out["last"].strip()
            facts.record(script, "polish", before)
    except Exception as e:  # polish is a bonus; never fail a fact-checked script over it
        log(f"hook polish skipped: {str(e)[:80]}")
    return script


def fact_check(script, cfg):
    brief = script.get("research")
    loose = unsupported_numbers(script, brief)
    if loose:
        log(f"research: narrated number(s) no note states: {', '.join(f'{v:g}' for v in loose[:8])}")
    extra = _notes_block(brief) + (f"""
These numbers in the narration appear in NO research note: {', '.join(f'{v:g}' for v in loose)}. Replace each with
the value a note states, or remove that detail.
""" if loose else "") + ("""Anything the notes don't state is unverifiable here: remove it rather than replace it with
another detail from memory.
""" if brief else "")
    lean = {k: v for k, v in script.items() if k != "research"}  # the notes travel once, above
    prompt = f"""
Fact-check this true-story Shorts script. For EVERY scene check each name, date, place, number and cause.
- Is it documented by reliable sources (not just popular retellings)? Famous myths count as WRONG unless the
  script says they're myths.
- Check what each line IMPLIES, not just what it literally says.
- Any invented dialogue, inner thoughts or unverifiable detail must be removed or rewritten as documented fact.
If something is wrong, exaggerated, legend or unverifiable, rewrite that scene so it is true while keeping the same
gripping tone and length (total narration must stay 95–120 words; never make it longer). Fix the same claim in title/description too.
Keep "subject_query", "commons", "visual", "subject" and "shows" fields and the JSON shape identical.

{extra}
SCRIPT:
{json.dumps(lean, ensure_ascii=False)}

Return JSON: {{"checks": [{{"claim": "...", "verdict": "ok|fixed", "working": "what the sources say"}}],
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
    for k, v in script.items():  # the checker sometimes drops fields it didn't touch
        if not fixed.get(k):
            fixed[k] = v
    return fixed
