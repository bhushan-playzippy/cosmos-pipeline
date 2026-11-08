"""Deterministic fact guards shared by the writers, packaging and verify: which numbers (with units), magnitude
words and superlatives a text asserts, so no rewrite can add or change one without a claim covering it.

Why: 1 Nov the Hunley Short went out saying "a five-foot spar torpedo loaded with about 80 pounds" (the spar was
~16-22 ft, the charge ~90-135 lb). The writer's own fact-check pass put both numbers in, packaging made "80-Pound"
the title, and the verifier's one ledger entry for that scene covered only the 80, copied from the narration. No
model call here: these are plain functions, cheap enough to run after every transformation.
"""
import re

WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
         "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
         "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40,
         "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
SCALES = {"hundred": 100, "thousand": 1e3, "million": 1e6, "billion": 1e9, "trillion": 1e12}
MAGNITUDES = {"dozens", "hundreds", "thousands", "millions", "billions", "trillions"}
UNITS = {**dict.fromkeys(("pound", "pounds", "lb", "lbs"), "lb"), **dict.fromkeys(("foot", "feet", "ft"), "ft"),
         **dict.fromkeys(("mile", "miles"), "mi"), **dict.fromkeys(("km", "kilometer", "kilometers", "kilometre",
                                                                    "kilometres"), "km"),
         **dict.fromkeys(("m", "meter", "meters", "metre", "metres"), "m"), **dict.fromkeys(("kg", "kilogram",
                                                                                         "kilograms"), "kg"),
         **dict.fromkeys(("ton", "tons", "tonne", "tonnes"), "t"), **dict.fromkeys(("year", "years"), "yr"),
         **dict.fromkeys(("day", "days"), "day"), **dict.fromkeys(("hour", "hours"), "h"),
         **dict.fromkeys(("minute", "minutes"), "min"), **dict.fromkeys(("second", "seconds"), "s"),
         **dict.fromkeys(("percent", "%"), "%"), **dict.fromkeys(("degree", "degrees"), "deg"), "mph": "mph",
         **dict.fromkeys(("men", "people", "crew", "crewmen", "lives", "soldiers", "children", "sailors",
                          "survivors", "passengers", "volunteers"), "people")}
MONTHS = {"january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
          "november", "december"}
# Superlatives and absolutes: a factual claim each time, so the scene needs a ledger entry and a title may only
# use one the narration already makes.
SUPERLATIVE = re.compile(r"\b(first|only|largest|biggest|smallest|deadliest|fastest|slowest|oldest|youngest|"
                         r"longest|tallest|hottest|coldest|heaviest|highest|deepest|record|most \w+)\b", re.I)
# Intensifiers a rewrite may not add to facts that don't carry them (hook polish, titles).
INTENSIFIER = re.compile(r"\b(deadly|deadliest|lethal|instantly|never|always|entire|every|all|whole|"
                         r"impossible|exactly)\b", re.I)
HEDGES = re.compile(r"\b(about|around|roughly|nearly|almost|approximately|some|up to|at least|more than|over|"
                    r"under|less than|an estimated|estimated|as much as|as many as|perhaps|possibly|likely|"
                    r"reportedly|believed|thought|probably)\b|[~≈]", re.I)
_TOKEN = re.compile(r"\d[\d,]*(?:\.\d+)?|[A-Za-z]+|%")


def _tokens(text):
    return _TOKEN.findall(str(text or "").replace("‑", "-").replace("–", "-"))


def quantities(text):
    """[(value, unit or None)] for every number the text states: digits, number words ("seventeen hundred",
    "five-foot"), scales ("2.5 million"). The day in a date ("February 17, 1864") isn't a quantity; the year is.
    A bare "one" is left out ("one of the crew"), "one hundred" isn't."""
    toks, out, i = _tokens(text), [], 0
    low = [t.lower() for t in toks]
    while i < len(toks):
        t, v, j = low[i], None, i
        if t[0].isdigit():
            t = t.rstrip(",")  # "February 17, 1864" tokenises as "17," then "1864"
            v = float(t.replace(",", "") if re.fullmatch(r"\d{1,3}(,\d{3})+(\.\d+)?", t) else t.split(",")[0])
            j = i + 1
            if (i and low[i - 1] in MONTHS and v <= 31) or (j < len(low) and low[j] in MONTHS and v <= 31):
                i += 1
                continue
        elif t in WORDS or (t == "a" and i + 1 < len(low) and low[i + 1] in SCALES):
            total, cur, j = 0.0, 0.0, i
            while j < len(low) and (low[j] in WORDS or low[j] in SCALES or (low[j] in ("a", "and") and j + 1 < len(low)
                                                                            and low[j + 1] in {**WORDS, **SCALES})):
                w = low[j]
                if w == "a":
                    cur = cur or 1
                elif w in WORDS:
                    cur += WORDS[w]
                elif w == "hundred":
                    cur = (cur or 1) * 100
                elif w in SCALES:
                    total, cur = total + (cur or 1) * SCALES[w], 0.0
                j += 1
            v = total + cur
            if t == "one" and j == i + 1 and not (j < len(low) and low[j] in UNITS):
                i += 1
                continue
        if v is None:
            i += 1
            continue
        while j < len(low) and low[j] in SCALES:  # "2.5 million", "4.5 billion"
            v, j = v * SCALES[low[j]], j + 1
        unit = UNITS.get(low[j]) if j < len(low) else None
        out.append((v, unit))
        i = j
    return out


def numbers(text):
    return [v for v, _ in quantities(text)]


def magnitudes(text):
    return {t.lower() for t in _tokens(text) if t.lower() in MAGNITUDES}


def strong_words(text):
    """Superlatives, intensifiers and magnitude words, lower-cased: what a rewrite may not add on its own."""
    s = str(text or "")
    return ({m.group(0).lower() for m in SUPERLATIVE.finditer(s)} | {m.group(0).lower() for m in INTENSIFIER.finditer(s)}
            | magnitudes(s))


def close(a, b, tol=0.01):
    return a == b or (a and b and abs(a - b) <= tol * max(abs(a), abs(b)))


def added(before, after):
    """What `after` asserts that `before` doesn't: numbers (by value) and strong words."""
    old = numbers(before)
    return ([v for v in numbers(after) if not any(close(v, o) for o in old)],
            sorted(strong_words(after) - strong_words(before)))


def hedged(text):
    return bool(HEDGES.search(str(text or "")))


def narration(script):
    return " ".join(s.get("text", "") for s in script.get("scenes", []))


def trail(script, stage, before):
    """Appends what a transformation changed (numbers added/removed) to script["fact_trail"], so every number in
    the final video can be traced to the stage that introduced it."""
    now = narration(script) + " | " + str(script.get("youtube", {}).get("title", ""))
    add, words = added(before, now)
    gone = [v for v in numbers(before) if not any(close(v, n) for n in numbers(now))]
    if add or gone or words:
        script.setdefault("fact_trail", []).append({"stage": stage, "added": add, "removed": gone, "words": words})
    return script


def snapshot(script):
    return narration(script) + " | " + str(script.get("youtube", {}).get("title", ""))


def record(script, stage, before=None):
    """After a model call that produced or changed the script: remember which model did `stage` (verify must
    use a different one) and, given the text before, what it changed (fact_trail)."""
    from . import llm
    if llm.LAST_MODEL:
        script.setdefault("provenance", {})[stage] = llm.LAST_MODEL
    return trail(script, stage, before) if before is not None else script


# Paragraphs the pipeline itself adds after the claim check (scriptgen.add_ai_disclosure, virality.ensure_hashtags):
# not factual claims, so they don't change what the claim check covered.
_DISCLOSURE = re.compile(r"made with ai|ai[- ](voiced|assisted|generated)|narration is ai", re.I)


def _body(text):
    """Factual body of a description or caption: before its "Sources:" list, without hashtags or the AI label."""
    text = re.split(r"\n\s*sources?\s*:", str(text or ""), maxsplit=1, flags=re.I)[0]
    paras = [p for p in re.split(r"\n\s*\n", text) if p.strip() and not _DISCLOSURE.search(p)]
    return re.sub(r"\s+", " ", re.sub(r"(?<!\w)#\w+", "", " ".join(paras))).strip()


def graphic_text(script):
    """What the to-scale graphics print on screen (scalegfx): each label with its value and unit."""
    out = []
    for sc in script.get("scenes", []):
        spec = sc.get("compare") if isinstance(sc.get("compare"), dict) else None
        for it in (spec or {}).get("items", []):
            out.append(f"{it.get('label', '')} {it.get('value', '')} {spec.get('unit', '')}".strip())
    return "; ".join(out)


def visible(script):
    """Every text a viewer sees besides the narration (the captions are the narration's own words, from the TTS
    timings): the claim check, the fingerprint and the publish gate cover all of it. 2 Nov: the headline, the
    description, the pinned comment and the graphics' numbers used to change without any re-check."""
    yt, ig = script.get("youtube") or {}, script.get("instagram") or {}
    return {"title": str(yt.get("title", "")), "headline": str(yt.get("hook_text", "")),
            "description": _body(yt.get("description")), "comment": str(yt.get("pinned_comment", "")),
            "instagram": _body(ig.get("caption")), "graphic": graphic_text(script)}


def snapshot_v2(script):
    return narration(script) + " | " + " | ".join(f"{k}: {v}" for k, v in visible(script).items())


_STARTERS = {"the", "a", "an", "how", "why", "what", "when", "where", "who", "which", "but", "and", "it", "its", "he",
             "she", "they", "this", "that", "these", "those", "in", "on", "at", "one", "his", "her", "their", "there",
             "nobody", "no", "every", "imagine", "if", "then", "yet", "so", "just", "even", "only", "now", "today", "i",
             "you", "your", "we", "our", "after", "before", "during", "with", "without", "for", "from", "to", "by"}


def new_names(before, after):
    """Capitalised words (names of people, ships, places) that `after` uses and `before` never mentions. 2 Nov dry
    run: a hook polish opened an Apollo 13 story with "Frank Borman", who wasn't on that flight; the number and
    intensifier guard couldn't see a name."""
    have = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'’.-]*", str(before or ""))}
    words = re.findall(r"\b[A-Z][a-zA-Z'’.-]{2,}", str(after or ""))
    common = _STARTERS | MONTHS | set(WORDS) | {"earth", "moon", "sun"}  # number words and everyday bodies aren't names
    return sorted({w.rstrip(".'’") for w in words if w.lower().rstrip(".'’") not in have
                   and w.lower().rstrip(".'’") not in common})
