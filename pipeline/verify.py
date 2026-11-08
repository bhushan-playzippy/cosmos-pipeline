"""Independent claim check: a blind second opinion on every material fact, with the decisions made in Python.

Why: the writer's own fact-check pass sees its draft and its reasoning, and it waved through wrong numbers that
had to be withdrawn after upload (Mangalyaan "400 million km", really ~666 million; a basketball-Earth payoff
off by ~10x). 1 Nov the Hunley Short got through this stage too: "a five-foot spar torpedo loaded with about 80
pounds" (the spar was ~16-22 ft, the charge ~90-135 lb). Three holes let it through, each closed here:
  - the ledger had one entry for the sentence and it held only the 80, so the 5 was never checked
    → coverage: every number the narration, title and hook headline state needs its own ledger entry;
  - the "independent" verifier was the same Groq model that had fact-checked (and introduced) the numbers, and
    it copied the narrated 80 as its own value → independence: models that wrote, fact-checked or repaired the
    text are excluded (llm.ask_json avoid); with no other strong model the video defers instead;
  - nothing outside the models was consulted → documents: the subject's Wikipedia articles (evidence.py) are
    searched for each narrated quantity; a contradicting sentence fails the claim, a matching one is cited.
Per claim the ledger records type, source, evidence, conflicts and a status decided here:
  VERIFIED       supported, numbers within 2% (or the document states them)
  APPROXIMATE    supported within `tolerance`; anything over 10% off must be hedged ("about") in the narration
  CONFLICTING_SOURCES  credible values disagree: passes only when hedged and not in the title; "disputed" fails
  UNSUPPORTED    unverifiable, low confidence, no specific source, or a number with no ledger entry → fails
  REJECTED       wrong/misleading, outside tolerance, contradicted by the document, or approximate-as-exact
On failure: one targeted repair of the flagged scenes, then a full re-check by a model that didn't do the
repair. Still failing → ValueError, the video fails at stage "verify" and is never published. The ledger is
stored as script.json "claims"; "claims_for" fingerprints the text it covers, so `rerender` of an edited script
re-verifies. Budget: 1 strong call per video, 3 when a repair is needed; Wikipedia is free (2-4 requests).
"""
import hashlib
import json
import math
import re

from . import evidence, facts
from .llm import ask_json
from .util import log

CHECKER = """You are an independent fact verifier for a publisher. You did not write this script and you have no
stake in it. You check claims against authoritative sources (space agencies, observatories, peer-reviewed
papers, encyclopedias, museums, national archives, academic historians). You report your OWN values; you never
copy a number from the script as confirmation. If you are not confident, say so."""
PASS = ("VERIFIED", "APPROXIMATE", "CONFLICTING_SOURCES")
GENERIC = re.compile(r"consensus|common knowledge|general knowledge|widely (known|reported)|various|multiple "
                     r"sources|^\s*(n/?a|none|unknown|general|history)\s*$", re.I)


def _prompt(script, cfg):
    narration = "\n".join(f"[{i + 1}] {s.get('text', '')}" for i, s in enumerate(script.get("scenes", [])))
    meta, vis = script.get("youtube", {}), facts.visible(script)
    return f"""
Channel: {cfg['channel'].get('niche', '')}
Verify this Shorts narration. Scenes are numbered.

{narration}

TITLE: {meta.get('title', '')}
ON-SCREEN HEADLINE: {meta.get('hook_text', '')}
DESCRIPTION: {vis['description']}
PINNED COMMENT: {vis['comment']}
ON-SCREEN GRAPHIC: {vis['graphic'] or '(none)'}

For EVERY material factual claim (each number, date, name, place, event, cause, motive, distance, size, duration,
cost, count, "first/only/largest", and any quotation or attributed statement) return one entry. ONE ENTRY PER
NUMBER: a sentence stating a length and a weight is two entries, a value with its unit conversion ("300 m
(1,000 ft)") is two entries. Fields:
- "scene": scene number, or "title" (title and headline), "description", "comment" or "graphic"
- "type": "measurement", "date", "name", "science", "causal", "specification", "quote" or "other"
- "category": "person", "location", "event", "date", "cause", "motive", "quote", "attribution", "science",
  "description", "relationship", "superlative" or "measurement"
- "importance": "core" (the story or explanation depends on it), "supporting", or "rhetorical" (a hook question
  or closing line that only restates facts stated elsewhere)
- "claim": the claim, quoted or closely paraphrased
- "key_terms": 2-4 words or names copied exactly from the text, which any source sentence stating this claim
  must contain; at least one must say what happened or what is true, not only who or what it is about
- "quote": for a quote or attributed statement, the exact quoted words; otherwise null
- "narrated": the number exactly as the narration states it, converted to a plain number (e.g. "four hundred
  million kilometers" → 400000000, "five-foot" → 5), or null if the claim has no number
- "independent": YOUR best value for the same quantity in the SAME unit, from your own knowledge of the source,
  or null if not numeric. Never echo the narrated value unless your source states it.
- "unit": the unit of both numbers ("km", "ft", "lb", "years", "USD", "count", ...), or null
- "conflicting": other values credible sources give for this quantity, same unit (e.g. older vs newer
  research); [] if they agree
- "source": organisation + page/article name that supports YOUR value. "General consensus" or "common
  knowledge" is not a source.
- "source_title", "publisher": the page and its publisher; "source_url": its URL only if you are sure it exists
  (it is fetched and searched for the claim; an invented URL counts against the claim). Prefer primary,
  institutional, museum and scholarly pages over encyclopedias.
- "confidence": "high", "medium" or "low"
- "verdict": "supported", "wrong" (the claim or its implication is false), "misleading" (true but gives a false
  impression), "disputed" (historians/scientists disagree and the script states one side as fact) or
  "unverifiable"
- "note": one short sentence (for wrong/misleading/disputed: what is actually true)
Also judge the title and headline against the narration (verdict "wrong" if they overstate it).

Return JSON: {{"claims": [{{...}}]}}
"""


def _num(x):
    """The ledger's value as a number. Checkers often write it as text ("$73 million", "0.280-inch", "2,000
    rupee"): 5 Nov those read as no number, so every such claim lost its numeric check and coverage called the
    narration's 73 million "not checked". Text is read as the narration is (facts.numbers), only when it holds
    exactly one number: "₹450 crore (US$73 million)" stays unread rather than guessed."""
    try:
        v = float(str(x).replace(",", ""))
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        pass
    if not isinstance(x, str) or not x.strip():
        return None
    found = facts.numbers(x)
    return found[0] if len(found) == 1 and math.isfinite(found[0]) else None


def problems(claims, cfg):
    """Claims that fail on the model's own answer, decided here and not by the model: wrong/misleading/disputed/
    unverifiable verdicts, and any narrated number more than `tolerance` away from the independent value."""
    tol = cfg.get("verify", {}).get("tolerance", 0.25)
    bad = []
    for c in claims:
        verdict = str(c.get("verdict", "")).lower()
        n, i = _num(c.get("narrated")), _num(c.get("independent"))
        off = None
        if n is not None and i is not None and n > 0 and i > 0:
            off = abs(math.log(n / i)) > math.log(1 + tol)
        elif n is not None and i is not None and n != i:
            off = True  # zero/negative values: must match exactly
        if verdict in ("wrong", "misleading", "disputed", "unverifiable"):
            bad.append({**c, "why": verdict})
        elif off:
            bad.append({**c, "why": f"narrated {n:,.0f} vs independent {i:,.0f} {c.get('unit') or ''}".strip()
                        if min(n, i) >= 10 else f"narrated {n:g} vs independent {i:g} {c.get('unit') or ''}".strip()})
    return bad


def _is_title(scene):
    return str(scene).lower() in ("title", "headline", "hook_text", "on-screen headline")


PLACES = ("description", "comment", "graphic", "instagram")


def _scene_text(script, scene):
    if _is_title(scene):
        yt = script.get("youtube", {})
        return f"{yt.get('title', '')} {yt.get('hook_text', '')}"
    if str(scene).lower() in PLACES:
        return facts.visible(script)[str(scene).lower()]
    try:
        return script["scenes"][int(scene) - 1].get("text", "")
    except (ValueError, TypeError, IndexError, KeyError):
        return ""


CATEGORY = {"measurement": "measurement", "specification": "measurement", "date": "date", "name": "person",
            "quote": "quote", "causal": "cause", "science": "science", "other": "description"}
# Categories that must be backed by a fetched document sentence: a model's agreement is not enough for a quote,
# an attribution, a "first/only/largest" or someone's motive (the claims a wrong video is remembered for).
STRICT = ("quote", "attribution", "superlative", "motive")


def category(c):
    cat = str(c.get("category") or "").lower().strip()
    if cat:
        return cat
    if facts.SUPERLATIVE.search(str(c.get("claim", ""))):
        return "superlative"
    return CATEGORY.get(str(c.get("type", "")).lower(), "description")


def _rhetorical(c, script):
    """A hook question or closing line may be "rhetorical" only where one is expected (first/last scene, title)
    and only when it states no number or strong word the rest of the narration doesn't: the model's label alone
    can't exempt a claim."""
    if str(c.get("importance", "")).lower() != "rhetorical" or category(c) in STRICT:
        return False
    scenes = script.get("scenes", [])
    sc = str(c.get("scene")).lower()
    if not (_is_title(sc) or sc in ("1", str(len(scenes)))):
        return False
    text = _scene_text(script, c.get("scene"))
    rest = " ".join(x.get("text", "") for x in scenes if x.get("text", "") not in text)
    nums, words = facts.added(rest, text)
    return not nums and not words


def _claim_terms(c, text):
    """Key terms when the checker's own are unusable (fewer than two, or not in the line): the claim's longest
    distinctive words that the line itself says, at most three. 5 Nov: Phineas Gage's "He spoke within minutes"
    and "Phineas Gage" found no document though the article states both, because the model gave one key term."""
    said = set(evidence._norm(text).split())
    words = [w for w in dict.fromkeys(evidence._norm(c.get("claim", "")).split())
             if len(w) >= 4 and w in said and w in evidence._words(w)]
    words = sorted(words, key=len, reverse=True)[:3]
    return words if len(words) >= 2 else []


def _viewer_question(c, script):
    """A question put to the viewer asserts nothing, whatever category the checker gives it. 5 Nov: Dunkirk's
    closing "Would you have taken a small boat to sea to save strangers?" failed as a "quote with no document
    stating it". Only a question in a scene with no quotation marks, adding no number or strong word the narration
    doesn't have: "Why did 900 boats sail?" is still checked when no scene says 900."""
    claim = str(c.get("claim", "")).strip()
    text = _scene_text(script, c.get("scene"))
    if not claim.endswith("?") or re.search(r"[\"“”]", text):
        return False
    rest = " ".join(x.get("text", "") for x in script.get("scenes", []) if x.get("text", "") not in text)
    nums, words = facts.added(rest, claim)
    return not nums and not words


def _cite(c, ev, quality=None):
    """Records evidence: only a passage found in a fetched document is a citation; the model's source is kept
    apart as a proposal with what checking it showed."""
    if ev:
        kind, quote, title, url = ev[:4]
        c["citation"] = {"title": title, "publisher": evidence.publisher(url), "url": url, "passage": quote,
                         "quality": evidence.source_quality(url) or quality or "unknown", "kind": kind}
        c["evidence"], c["evidence_source"], c["evidence_type"] = quote, url, "document"
    else:
        c["citation"] = None
        c["evidence"], c["evidence_source"], c["evidence_type"] = c.get("note") or "", "", "model"
    url = c.get("source_url")
    checked = ("confirmed" if ev and url and ev[3] == url else "fetched, claim not found" if url and evidence.fetch(url)
               else "not fetched (domain not on the allowlist)" if url and not evidence.source_quality(url)
               else "unreachable" if url else "no URL given")
    c["proposed_source"] = {"name": c.get("source"), "title": c.get("source_title"), "publisher": c.get("publisher"),
                            "url": url, "checked": checked}


def classify(claims, script, cfg, docs=()):
    """Sets status/decision/evidence on every claim; returns the failing ones (with "why").
    Numbers: the independent value and the documents decide (VERIFIED/APPROXIMATE/CONFLICTING_SOURCES/
    UNSUPPORTED/REJECTED, as before). Everything else (2 Nov): VERIFIED needs a fetched document sentence with the
    claim's key terms (or the exact quote); without one the claim is UNRESOLVED, an explicit "only the model says
    so". STRICT categories fail UNRESOLVED; other material claims may stay UNRESOLVED up to verify.max_unresolved
    per video (the rest fail and go to repair). A rhetorical restatement is RHETORICAL."""
    vc = cfg.get("verify", {})
    tol = vc.get("tolerance", 0.25)
    bad, unresolved = [], []
    for c in claims:
        text = _scene_text(script, c.get("scene"))
        title = _is_title(c.get("scene"))
        n, i = _num(c.get("narrated")), _num(c.get("independent"))
        c["category"] = category(c)
        # a number, date, measurement or quote needs a named source; a rhetorical line (hook, ending, title) is
        # judged on the verdict, and titles also by visible_issues (2 Nov: hooks and titles failed "no source")
        factual = n is not None or str(c.get("type", "")).lower() in ("date", "measurement", "specification", "quote")
        others = [v for v in map(_num, c.get("conflicting") or []) if v is not None]
        doc = next((r for (v, _u), r in evidence.assess(text, docs, tol) if n is not None and facts.close(v, n)
                    and r), None) if docs and text else None
        status, why = "VERIFIED", None
        first = (problems([c], cfg) or [{}])[0].get("why")
        ev = None
        if n is None:
            terms = [t for t in c.get("key_terms") or [] if evidence._norm(t)]
            # key terms must come from the text itself, so the model can't pick easy words to "find"
            if any(not evidence._has(t, evidence._norm(text).split()) for t in terms) or len(terms) < 2:
                terms = _claim_terms(c, text)
            ev = evidence.passage(terms, docs, quote=c.get("quote") if c["category"] in ("quote", "attribution")
                                  else None)
        if first:
            status, why = ("CONFLICTING_SOURCES" if first == "disputed" else
                           "UNSUPPORTED" if first == "unverifiable" else "REJECTED"), first
        elif str(c.get("confidence", "")).lower() == "low":  # the checker itself isn't sure: never a pass
            status, why = "UNSUPPORTED", "low confidence"
        elif n is None:
            # all key terms on a fetched source page ("indirect") back a non-strict claim, core or not: 5 Nov the Pig
            # War failed on 12 such lines ("On June 15, 1859, a pig was shot": date and shot in neighbouring
            # sentences of the article) that the independent model had also confirmed. Quotes, attributions,
            # superlatives and motives still need the sentence itself.
            if ev and (ev[0] == "direct" or c["category"] not in STRICT):
                status = "VERIFIED"
            elif _rhetorical(c, script) or _viewer_question(c, script):
                status = "RHETORICAL"
            elif c["category"] in STRICT:
                status, why = "UNRESOLVED", (f"{c['category']} with no document stating it"
                                             + (" (only indirect)" if ev else "") + "; a model's word isn't enough")
            else:
                status = "UNRESOLVED"
                unresolved.append(c)
        elif doc and doc[0] == "conflict":
            status, why = "REJECTED", (f"contradicted by {doc[2]}: \"{doc[1][:160]}\" gives "
                                       f"{', '.join(f'{v:g}' for v in doc[3])} {c.get('unit') or ''}").strip()
        elif str(c.get("confidence", "")).lower() == "low" or factual and (not c.get("source")
                                                                             or GENERIC.search(str(c["source"]))):
            status, why = "UNSUPPORTED", "no specific source" if c.get("confidence") != "low" else "low confidence"
        elif n is not None and i is None and not (doc and doc[0] == "support"):
            status, why = "UNSUPPORTED", "no independent value for a narrated number"
        elif n is not None and others and any(not facts.close(n, v, tol) for v in others):
            status = "CONFLICTING_SOURCES"
            if title or not facts.hedged(text):
                why = (f"sources disagree ({', '.join(f'{v:g}' for v in [i] + others if v is not None)} "
                       f"{c.get('unit') or ''}) but the {'title' if title else 'narration'} states {n:g} as fact")
        elif n is not None and i is not None and not facts.close(n, i, 0.02):
            status = "APPROXIMATE"
            if not facts.close(n, i, 0.10) and not facts.hedged(text):
                status, why = "REJECTED", f"approximate value stated as exact ({n:g} vs {i:g}); say \"about\""
        if n is not None:
            ev = ("direct", doc[1], doc[2].rsplit("/", 1)[-1].replace("_", " "), doc[2]) if doc and doc[0] == "support" else None
        _cite(c, ev)
        conflicts = [{"source": doc[2], "quote": doc[1], "values": doc[3]}] if doc and doc[0] == "conflict" else []
        c["conflicts"] = conflicts + [{"source": "independent checker", "values": c.get("conflicting")}
                                      ] * bool(c.get("conflicting"))
        c["status"], c["decision"] = status, "fail" if why else "pass"
        if why:
            c["why"] = why
            bad.append(c)
    allowed = vc.get("max_unresolved", 2)
    rank = {"core": 0, "supporting": 1}
    for c in sorted(unresolved, key=lambda c: rank.get(str(c.get("importance")).lower(), 1))[:max(0, len(unresolved) - allowed)]:
        c["decision"], c["why"] = "fail", (f"no document evidence ({len(unresolved)} unresolved claims; at most "
                                           f"{allowed} per video): support it, soften it or leave it out")
        bad.append(c)
    return bad


def coverage(script, claims, docs=()):
    """Numbers and superlatives the text states that no ledger entry covers (the Hunley "five-foot"). A number a
    fetched source sentence states about the same thing (evidence.assess "support") is checked too: 5 Nov the
    verifier's ledger left out Mangalyaan's launch and arrival years, which the Mars Orbiter Mission article gives."""
    # every number a ledger entry's narrated value mentions: "November 5th, 2013" covers 5 and 2013 (5 Nov: both
    # Mangalyaan dates were "not checked" though the ledger had a claim for each)
    narrated = [v for c in claims for v in ([_num(c.get("narrated"))] if _num(c.get("narrated")) is not None
                                            else facts.numbers(str(c.get("narrated") or "")))]
    scenes_with_claims = {"title" if _is_title(c.get("scene")) else str(c.get("scene")).lower() for c in claims}
    gaps = []
    known = _known(script)
    parts = [(str(k + 1), s.get("text", "")) for k, s in enumerate(script.get("scenes", []))]
    parts.append(("title", _scene_text(script, "title")))
    parts.append(("graphic", facts.graphic_text(script)))  # the to-scale graphic prints its own numbers
    for scene, text in parts:
        sourced = [vv for (vv, _u), r in evidence.assess(text, docs, 0.25) if r and r[0] == "support"] if docs else []
        for v, unit in facts.quantities(_names_only(text, *known)):
            if (v in (0, 1) and not unit) or any(facts.close(v, n) for n in narrated + sourced):
                continue
            gaps.append({"scene": scene, "claim": f"{v:g} {unit or ''}".strip() + f" in: {text[:80]}",
                         "status": "UNSUPPORTED", "why": "number not checked (no ledger entry states it)"})
        if scene != "graphic" and facts.SUPERLATIVE.search(text) and scene not in scenes_with_claims:
            gaps.append({"scene": scene, "claim": text[:100], "status": "UNSUPPORTED",
                         "why": "superlative not checked (no ledger entry for this scene)"})
    return gaps


SYNONYMS = {"lethal": "deadly"}


_DESIGNATOR = re.compile(r"\b([A-Z][A-Za-z]{2,})[ -](\d{1,3}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
                         r"twelve|thirteen)\b(?![,.]?\d)", re.I)


def _names_only(text, narr, topic=""):
    """Drops mission/vehicle designators ("Voyager 1", "Apollo 13") whose name the narration also uses: a name, not a
    quantity (2 Nov dry run: "Voyager 1" in the description was flagged against a narration saying "Voyager One").
    The video's topic vouches only for its exact designator: "Apollo 13" in the topic clears "Apollo 13", never
    "Apollo 14"."""
    have = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", narr)}
    exact = {f"{a.lower()} {b.lower()}" for a, b in _DESIGNATOR.findall(str(topic or ""))}
    return _DESIGNATOR.sub(lambda m: m.group(1) if m.group(1)[0].isupper() and (
        m.group(1).lower() in have or f"{m.group(1).lower()} {m.group(2).lower()}" in exact) else m.group(0),
        str(text or ""))


def _known(script):
    """(narration, topic) for _names_only. 4 Nov: an Apollo 13 story whose narration says "Odyssey" had "Apollo 13"
    in its title flagged as an unchecked number 13."""
    return facts.narration(script), str(script.get("topic") or "")


def title_issues(script):
    """Title and on-screen headline may not claim more than the narration: no number, superlative or magnitude
    it doesn't state (1 Nov: the Hunley title "Deadly 80-Pound Bomb" came from packaging)."""
    narr = facts.narration(script)
    head = _scene_text(script, "title")
    known = _known(script)
    nums, words = facts.added(_names_only(narr, *known), _names_only(head, *known))
    have = {SYNONYMS.get(w, w) for w in facts.strong_words(narr)}
    words = [w for w in words if SYNONYMS.get(w, w) not in have
             and (facts.SUPERLATIVE.fullmatch(w) or w in facts.MAGNITUDES or w in ("deadly", "lethal", "deadliest"))]
    return [{"scene": "title", "claim": head[:100], "status": "REJECTED",
             "why": f"title/headline states more than the narration: {', '.join([f'{n:g}' for n in nums] + words)}"}
            ] if nums or words else []


def visible_issues(script):
    """Title/headline (title_issues) plus the description, pinned comment and Instagram caption: none may state a
    number, superlative or magnitude the verified narration doesn't. They are written from the narration, so this
    keeps every visible sentence inside what the claim check covered."""
    out = title_issues(script)
    narr = facts.narration(script)
    have = {SYNONYMS.get(w, w) for w in facts.strong_words(narr)}
    for place in ("description", "comment", "instagram"):
        text = facts.visible(script)[place]
        nums, words = facts.added(_names_only(narr, *_known(script)), _names_only(text, *_known(script)))
        words = [w for w in words if SYNONYMS.get(w, w) not in have and
                 (facts.SUPERLATIVE.fullmatch(w) or w in facts.MAGNITUDES or w in ("deadly", "lethal", "deadliest"))]
        if nums or words:
            out.append({"scene": place, "claim": text[:100], "status": "REJECTED",
                        "why": f"{place} states more than the narration: "
                               f"{', '.join([f'{n:g}' for n in nums] + words)}"})
    return out


def fingerprint(script):
    """v2 (2 Nov): narration + every visible text (title, headline, description body, pinned comment, Instagram
    caption, graphic labels/values). A hand-edited headline now changes it, so rerender re-verifies."""
    return "v2:" + hashlib.sha1(facts.snapshot_v2(script).encode()).hexdigest()[:16]


def legacy_fingerprint(script):
    return hashlib.sha1(facts.snapshot(script).encode()).hexdigest()[:16]


def verified(script):
    """(ok, how). Current ledgers must match fingerprint(). A ledger made before 2 Nov fingerprinted only the
    narration + title: it stays valid (no silent invalidation of queued videos such as Hunley) only while those
    are unchanged AND the other visible texts pass the deterministic checks against it (no number/superlative
    beyond the narration, every graphic number covered by a ledger entry). Otherwise it is re-verified."""
    got = script.get("claims_for")
    if not got:
        return False, "no claim check recorded"
    if got == fingerprint(script):
        return True, "verified (v2: narration and all visible text)"
    if got.startswith("v2:"):
        return False, "visible text changed since the claim check"
    if got != legacy_fingerprint(script):
        return False, "narration/title changed since the claim check"
    extra = visible_issues(script) + [g for g in coverage(script, script.get("claims") or []) if g["scene"] == "graphic"]
    if extra:
        return False, "legacy ledger; other visible text not covered: " + "; ".join(e["why"][:80] for e in extra[:2])
    return True, "verified (legacy v1 ledger: narration + title; other visible text checked deterministically)"


def _avoid(script):
    return {m for k, m in (script.get("provenance") or {}).items() if k in ("write", "fact_check", "verify_repair")}


def _claims(script, cfg):
    data = ask_json(_prompt(script, cfg), cfg, system=CHECKER, temperature=0.1, strong=True, purpose="verify",
                    prefer=cfg.get("verify", {}).get("model"), avoid=_avoid(script))
    claims = data.get("claims", []) if isinstance(data, dict) else []
    if not claims:
        raise ValueError("independent verifier returned no claims")
    return claims


def _assess(script, claims, cfg, docs):
    docs = list(docs) + evidence.pages([c.get("source_url") for c in claims],
                                       cfg.get("verify", {}).get("max_fetches", 6))
    return classify(claims, script, cfg, docs) + coverage(script, claims, docs) + visible_issues(script)


def _documents(script):
    """The articles the claims are checked against: the ones research wrote from first, then the queries research
    uses. 4 Nov: verify searched the whole topic sentence ("The 1981 British Airways Flight 9 flying through...")
    and got "History of British Airways", so the date, the volcano and the restart were all "no document evidence"
    while the Flight 009 article states each of them."""
    from .storygen import headline, proper_nouns
    topic = script.get("topic") or ""
    titles = [s.get("title") for s in (script.get("research") or {}).get("sources") or [] if s.get("title")]
    for q in dict.fromkeys(filter(None, [*titles[:2], script.get("subject_query"), proper_nouns(topic),
                                         headline(topic), topic])):
        docs = evidence.reference(q)
        if docs:
            return docs
    return []


def check(script, cfg):
    """Returns the (possibly repaired) script with script["claims"]; raises ValueError if it can't be supported."""
    vc = cfg.get("verify", {})
    if not vc.get("enabled", True):
        return script
    docs = _documents(script) if vc.get("documents", True) else []
    claims = _claims(script, cfg)
    bad = _assess(script, claims, cfg, docs)
    if bad:
        for c in bad:
            log(f"verify: scene {c.get('scene')}: {str(c.get('claim'))[:80]} → {c['why'][:120]}")
        script = _repair(script, bad, cfg)
        from .virality import drop_repeats  # a repair can copy the hook into the ending word for word, which failed
        if drop_repeats(script):            # the "no repeated lines" gate after render (7 quarantines, 26 Oct-5 Nov)
            log("verify: dropped a repaired line that repeated an earlier one word for word")
        claims = _claims(script, cfg)
        bad = _assess(script, claims, cfg, docs)
        if bad:
            script["claims"] = claims + [c for c in bad if c not in claims]
            raise ValueError("independent check still failing after repair: " +
                             "; ".join(f"{str(c.get('claim'))[:60]} ({c['why'][:80]})" for c in bad[:3]))
        log("verify: repaired and re-checked")
    script["claims"] = claims
    script["claims_for"] = fingerprint(script)
    from . import llm
    script["independence"] = {"verify": llm.LAST_MODEL, "verify_family": llm.family(llm.LAST_MODEL, cfg),
                              "excluded_families": sorted(llm.families(_avoid(script), cfg))}
    if not script.get("sources"):  # the writer lost its list: cite what the independent check relied on
        script["sources"] = list(dict.fromkeys(str(c["source"]) for c in claims
                                               if c.get("source") and c.get("confidence") != "low"))[:3]
    cited = sum(c.get("evidence_type") == "document" for c in claims)
    open_ = sum(c.get("status") == "UNRESOLVED" for c in claims)
    log(f"verify: {len(claims)} claims passed ({cited} with a quoted document source, {open_} unresolved: model only)")
    return script


REPAIR_KEYS = ("title", "hook_text", "description", "pinned_comment")


def _repair(script, bad, cfg):
    """One targeted rewrite of the flagged scenes, by the WRITER's family (2 Nov, R1): the author fixes its own
    text and a family that neither wrote nor repaired it re-checks it, so a repair needs 2 families, not 3
    (with write=gemini, fact_check=openai, repair=qwen no family was left to re-check: clips deferred for good).
    Only the visible text travels (R2: the whole script JSON, fact-check log included, was too large for qwen's
    free per-minute limit); the result is merged back field by field."""
    issues = "\n".join(f"- scene {c.get('scene')}: \"{c.get('claim')}\" → {c.get('why')}. {c.get('note') or ''} "
                       f"(source: {c.get('source') or '?'})" for c in bad)
    yt = script.get("youtube", {})
    lean = {"scenes": [{"text": sc.get("text", "")} for sc in script.get("scenes", [])],
            "youtube": {k: yt.get(k, "") for k in REPAIR_KEYS},
            "instagram": {"caption": (script.get("instagram") or {}).get("caption", "")}}
    prompt = f"""
An independent check found these problems in a Shorts script:
{issues}

Rewrite ONLY the affected scenes so every statement is true and supported. Use the independent value where
one is given, round honestly ("about"), or remove the claim if it can't be supported. Where credible sources
disagree, either say so ("estimates range from X to Y") or leave the number out. Disputed points must be
stated as disputed. Keep each rewritten scene about the same length and tone, keep the other scenes word for
word, and keep the same number of scenes. Fix the same claim in the title, hook_text, description,
pinned_comment and instagram caption if it appears there; they may not state any number, superlative or
intensifier the narration doesn't. A claim marked "no document evidence" must be supported by what reliable
sources plainly say, softened, or left out. Never invent a quote. Apart from an independent value given above,
never add a number, date, name or quote that wasn't already in the script: when in doubt, leave the detail out
(an unsupported detail removed passes; a new one replacing it is checked again and often fails).

SCRIPT:
{json.dumps(lean, ensure_ascii=False)}

Return JSON in the same shape: {{"scenes": [{{"text": "..."}}], "youtube": {{...}}, "instagram": {{"caption": "..."}}}}
"""
    from . import llm
    before = facts.snapshot(script)
    writer = (script.get("provenance") or {}).get("write")
    got = ask_json(prompt, cfg, temperature=0.3, strong=True, purpose="verify_repair", prefer=writer,
                   prefer_family=llm.family(writer, cfg) if writer else None)
    got = got.get("script", got) if isinstance(got, dict) else {}
    new_scenes = got.get("scenes") if isinstance(got, dict) else None
    if not new_scenes or len(new_scenes) != len(script.get("scenes", [])):
        raise ValueError("verifier repair returned no script (or a different number of scenes)")
    out = json.loads(json.dumps(script))
    for sc, new in zip(out["scenes"], new_scenes):
        if isinstance(new, dict) and str(new.get("text", "")).strip():
            sc["text"] = new["text"].strip()
    for k in REPAIR_KEYS:
        v = (got.get("youtube") or {}).get(k)
        if isinstance(v, str) and v.strip():
            out.setdefault("youtube", {})[k] = v.strip()
    cap = (got.get("instagram") or {}).get("caption")
    if isinstance(cap, str) and cap.strip():
        out.setdefault("instagram", {})["caption"] = cap.strip()
    out.pop("claims", None)
    out.pop("claims_for", None)
    out["provenance"] = dict(script.get("provenance") or {})
    out["fact_trail"] = list(script.get("fact_trail") or [])
    if llm.LAST_MODEL:
        out["provenance"]["verify_repair"] = llm.LAST_MODEL
    return facts.trail(out, "verify_repair", before)
