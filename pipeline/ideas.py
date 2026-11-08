"""Topic backlog: Gemini brainstorms and scores topics; we de-duplicate against history and pick by portfolio role.

Scores are the model's estimates (no search-volume data is available on the free stack); calibrate the weights in
config.yaml → planning.score_weights against real analytics once videos have 7+ days of data.
"""
import re

from .llm import ask_json
from .util import DATA, read_json, write_json, log

BACKLOG = DATA / "topics.json"
HISTORY = DATA / "history.json"


def done_topics():
    """Topics already made or tried. A failed job whose verified script went back to the backlog as a draft
    (orchestrator._keep_verified, `resumable`) is not done: its topic must stay pickable."""
    return [h["topic"] for h in read_json(HISTORY, []) if not h.get("resumable")]


STOP = {"the", "a", "an", "of", "on", "in", "into", "is", "and", "to", "why", "how", "what", "its", "it", "s",
        "vs", "versus", "standard", "single", "entire", "our", "planet"}


def _tokens(text):
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOP}


def _subject(text):
    """The thing being explained: 'Thickness of Saturn's Rings vs. A Cricket Pitch' → 'Thickness of Saturn's Rings'."""
    return re.split(r"\s+(?:vs\.?|versus|compared to)\s+", text, maxsplit=1, flags=re.I)[0]


def _jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def too_similar(topic, others, limit=0.6, subject_limit=0.4):
    """Topic-level duplicate screen: whole-title overlap, or the same subject with a different comparison
    ('Saturn's rings thickness vs a cricket pitch' and '... vs a human hair' are the same video)."""
    a, sa = _tokens(topic), _tokens(_subject(topic))
    for o in others:
        if _jaccard(a, _tokens(o)) >= limit or _jaccard(sa, _tokens(_subject(o))) >= subject_limit:
            return o
    return None


def dedupe_backlog():
    backlog, kept = read_json(BACKLOG, []), []
    done = done_topics()
    for t in backlog:
        if not too_similar(t["topic"], done + [k["topic"] for k in kept]):
            kept.append(t)
    write_json(BACKLOG, kept)
    log(f"backlog dedupe: {len(backlog)} → {len(kept)} topics")
    return kept


def score(t, cfg):
    w = cfg.get("planning", {}).get("score_weights", {})
    s = t.get("scores", {})
    return round(sum(s.get(k, 3) * w.get(k, 1) for k in
                     ("audience_relevance", "curiosity", "evidence_quality", "visual_feasibility", "series_potential",
                      "familiarity")), 1)


def refill_backlog(cfg, n=25):
    ch, plan = cfg["channel"], cfg.get("planning", {})
    backlog = read_json(BACKLOG, [])
    avoid = done_topics() + [t["topic"] for t in backlog]
    from .performance import guidance, playbook
    learn = guidance(cfg)
    weights = playbook().get("pillar_weight", {})
    if learn:
        learn += "\nFind NEW topics with the same pull as the winners (never repeat them)."
    if weights:
        learn += "\nPillar/role performance (1.0 = typical; weight topics toward the strong ones): " + \
                 ", ".join(f"{k} {v}x" for k, v in weights.items())
    prompt = f"""
Brainstorm {n} video topics for a faceless Shorts channel about {ch['niche']}.

Channel signature (every topic must suit it): {ch['signature']}
{learn}
Audience: {ch['audience']}. {ch.get('audience_rule', '')}
Content pillars: {plan.get('pillars', [])}
Recurring series: {plan.get('series', {})}

Rules:
- {plan.get("truth_rule", 'Each topic is ONE specific, true, verifiable fact or question (NASA/ESA-level established science). If the honest answer is "scientists think", the topic must say so — no speculation sold as fact.')}
- No legends, urban myths or "Snopes: unproven" stories, and no famous "facts" historians have debunked. If the
  cause or motive is disputed, the topic says it is disputed. Every number in topic, angle and hook_idea must be one
  an encyclopedia or NASA/ESA page states (no rounding up for drama); if unsure, leave the number out.
- hook_idea: ≤12 words, opens a question or stakes (a "?" or why/how/what/but/never/nobody), never starts with a
  date or a greeting, and must not give away the payoff.
- Spread across all pillars. Strong curiosity gap, but no clickbait the facts can't pay off.
- Lead with subjects US/European viewers ALREADY know (a famous person, ship, battle, planet, mission) seen from a
  little-known angle. On our channels the famous subjects (Titanic, Joan of Arc, Caesar, Dyatlov Pass, neutron
  stars, Mars) won 900–1,400 views and obscure ones (Halifax 1917, 1904 marathon) got 0–200 in the same week.
- Do NOT repeat or closely resemble any of these already-used topics: {avoid[-200:]}
- role: "discovery" (broad hook, pulls new viewers), "authority" (deeper explanation that builds trust),
  or "retention" (part of a recurring series / follow-up that makes viewers come back). Mix them roughly evenly.
- scores 1–5 (be honest, not generous): audience_relevance, curiosity, evidence_quality (how solidly sourced),
  visual_feasibility ({plan.get("visual_question", "can NASA public-domain images genuinely show this?")}), series_potential,
  familiarity (5 = most US/EU viewers instantly recognise the subject's name, 1 = almost nobody has heard of it).
- veo_prompt: an 8-second cinematic, photoreal, VERTICAL 9:16 shot that visualises the hook. No text, faces or logos.

Return JSON: {{"topics": [{{"topic": "short topic", "pillar": "...", "series": "... or empty", "role": "discovery",
  "angle": "the everyday comparison to use", "hook_idea": "first line idea", "viewer_benefit": "what the viewer gains",
  "scores": {{"audience_relevance": 3, "curiosity": 3, "evidence_quality": 3, "visual_feasibility": 3, "series_potential": 3, "familiarity": 3}},
  "veo_prompt": "..."}}]}}
"""
    data = ask_json(prompt, cfg, purpose="ideas")
    if isinstance(data, list):
        data = {"topics": data}
    new = data.get("topics", [])
    added = []
    for t in new:
        if not t.get("topic") or too_similar(t["topic"], avoid):
            continue
        # a topic the brainstormer itself can't source well fails at verify later, after burning strong calls
        if t.get("scores", {}).get("evidence_quality", 3) < 3:
            continue
        t["score"] = score(t, cfg)
        added.append(t)
        avoid.append(t["topic"])
    backlog.extend(added)
    backlog.sort(key=lambda t: -t.get("score", 0))
    write_json(BACKLOG, backlog)
    log(f"added {len(added)} topics to backlog ({len(backlog)} total)")
    return backlog


def next_topic(cfg, role=None, drafts=True):
    """Highest-scoring unused topic, preferring the requested portfolio role. drafts=False skips topics carrying
    a saved draft (a draft deferred earlier in this run is not retried before a fresh topic)."""
    backlog = read_json(BACKLOG, [])
    if len(backlog) < cfg.get("planning", {}).get("min_backlog", 9):
        backlog = refill_backlog(cfg)
    done = done_topics()
    backlog = [t for t in backlog if not too_similar(t["topic"], done)]
    pickable = backlog if drafts else [t for t in backlog if not t.get("draft")]
    from .growth import topic_multiplier  # performance + growth agents re-rank the backlog (see topic_multiplier)
    recent = [h.get("pillar") for h in read_json(HISTORY, []) if h.get("status") not in ("failed",)]
    ranked = []
    for t in pickable:
        m, parts = topic_multiplier(t, cfg, recent)
        ranked.append((round(t.get("score", 0) * m, 2), m, parts, t))
    ranked.sort(key=lambda r: -r[0])
    pool = [r for r in ranked if role and r[3].get("role") == role] or ranked  # the slot's role keeps the series mix
    if not pool:
        raise RuntimeError("topic backlog is empty")
    final, m, parts, pick = pool[0]
    pick = dict(pick)
    pick["selection"] = {"role": role, "base_score": pick.get("score"), "multiplier": m, "parts": parts,
                         "final": final, "runner_up": [{"topic": r[3]["topic"][:60], "final": r[0]} for r in pool[1:3]]}
    log(f"topic picked: {pick['topic'][:60]} (score {pick.get('score')} × {m} = {final}; {parts})")
    from .util import event
    event("topic_selected", topic=pick["topic"], role=role, **{k: v for k, v in pick["selection"].items() if k != "role"})
    backlog.remove(pool[0][3])
    # topics filtered as done above leave the stored backlog, except one carrying a saved draft: that is work, and
    # dropping it silently is how a stray history record deleted a draft on 3 Nov
    kept = [t for t in read_json(BACKLOG, []) if t.get("draft") and t not in backlog and t is not pool[0][3]
            and t["topic"] != pick["topic"]]
    write_json(BACKLOG, backlog + kept)
    return pick


def peek(n=7):
    return read_json(BACKLOG, [])[:n]
