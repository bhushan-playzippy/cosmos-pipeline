"""Virality agent (pre-publish): scores what decides a Short's first seconds and fixes hashtags.

Shorts are pushed or dropped on the first 1–3 seconds (swipe-away) and on whether people watch to the end and
loop. This agent can't see the future, so it checks the things that are known to matter and that we control:
  hook        scene 1 is short (≤12 words) and opens a question/stakes, no greeting
  title       ≤60 characters, concrete (a number, a name or a vivid noun), not a question-mark-only tease
  length      25–55 s (long enough to pay off, short enough to finish and loop)
  loop        the last line leads back into the first
  hashtags    #Shorts + 2–4 relevant niche tags on the description's last line (enforced, not just scored)
The score and tips go into post.txt and the gap report; only missing hashtags are fixed automatically.
"""
import re

GREETING = re.compile(r"^(hey|hi|hello|welcome|today|in this video|did you know|guys)\b", re.I)
# "In January 1925, ..." / "Back in 1912 ...": the date is the least gripping detail and costs the first second
DATE_OPEN = re.compile(r"^(in|on|back in|during|by)\s+(the\s+)?(early\s+|late\s+)?([a-z]+\s+)?(\d{1,2}(st|nd|rd|th)?,?\s+)?"
                       r"\d{3,4}s?\b", re.I)
OPEN_LOOP = re.compile(r"\?|\b(why|how|what|until|but|secret|never|only|nobody|no one|the moment|then)\b", re.I)


def same_line(a, b):
    """Equal once case, punctuation and spacing are ignored (what QA's "no repeated lines" check counts)."""
    norm = lambda s: " ".join(re.findall(r"[a-z0-9']+", s.lower()))
    return norm(a) == norm(b)


def drop_repeats(script):
    """Safety net after the writers: a scene that repeats an earlier one word for word is removed (a verbatim echo
    of the hook wastes the payoff and fails QA). Returns how many were dropped."""
    kept = []
    for s in script.get("scenes", []):
        if not any(same_line(s["text"], k["text"]) for k in kept):
            kept.append(s)
    dropped = len(script.get("scenes", [])) - len(kept)
    script["scenes"] = kept
    return dropped


def ensure_hashtags(script, cfg):
    """#Shorts first, then the script's own topic tags, then the channel's niche tags; max 5, last line."""
    yt = script["youtube"]
    found = re.findall(r"#\w+", yt["description"])
    wanted = ["#Shorts", *script.get("hashtags", []), *found,
              *cfg.get("packaging", {}).get("hashtags", [])]
    tags, seen = [], set()
    for t in wanted:
        t = "#" + re.sub(r"[^\w]", "", t.lstrip("#"))
        if len(t) > 2 and t.lower() not in seen and t.lower() != "#aigenerated":
            tags.append(t)
            seen.add(t.lower())
    tags = tags[:5]
    body = "\n".join(l for l in yt["description"].rstrip().split("\n") if not re.fullmatch(r"(\s*#\w+)+\s*", l))
    body = re.sub(r"(?<!\w)#Shorts\b", "", body).rstrip()
    yt["description"] = f"{body}\n\n{' '.join(tags)}"
    return tags


def score(script, duration):
    scenes = [s["text"] for s in script.get("scenes", [])]
    title = re.sub(r"\s*#Shorts\s*$", "", script["youtube"]["title"], flags=re.I)
    hook = scenes[0] if scenes else ""
    tips, pts = [], {}
    hw = len(hook.split())
    dated = bool(DATE_OPEN.match(hook))
    pts["hook"] = (2 if hw <= 12 and OPEN_LOOP.search(hook) and not GREETING.match(hook) and not dated else
                   1 if hw <= 16 and not dated else 0)
    if pts["hook"] < 2:
        tips.append(f"hook is {hw} words" + (" with no open question/stakes" if not OPEN_LOOP.search(hook) else "")
                    + (" and opens on a date" if dated else "")
                    + ": cut to ≤12 words that make the viewer need the answer")
    pts["title"] = 2 if len(title) <= 60 and re.search(r"\d|[A-Z][a-z]+ [A-Z]", title) else 1 if len(title) <= 70 else 0
    if pts["title"] < 2:
        tips.append("title: ≤60 chars with a concrete number or name")
    pts["length"] = 2 if 25 <= duration <= 55 else 1 if duration <= 59 else 0
    if pts["length"] < 2:
        tips.append(f"length {duration:.0f}s: aim for 25–55s")
    last = set(re.findall(r"[a-z]{4,}", scenes[-1].lower())) if scenes else set()
    first = set(re.findall(r"[a-z]{4,}", hook.lower()))
    pts["loop"] = 2 if len(last & first) >= 2 else 1 if last & first else 0
    if pts["loop"] < 2:
        tips.append("ending doesn't loop into the hook: reuse the hook's key words in the last line")
    tags = re.findall(r"#\w+", script["youtube"]["description"].split("\n")[-1])
    pts["hashtags"] = 2 if tags and tags[0].lower() == "#shorts" and 3 <= len(tags) <= 5 else 0
    if pts["hashtags"] < 2:
        tips.append("hashtags: #Shorts + 2–4 niche tags on the last line")
    total = round(100 * sum(pts.values()) / (2 * len(pts)))
    return {"score": total, "points": pts, "tips": tips}
