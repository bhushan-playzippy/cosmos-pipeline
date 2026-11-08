"""Scene-level visual grounding: does each picture show what its narration says? One record per scene, decided
first by deterministic rules on the asset's own metadata, then combined with the AI reviewer's per-scene look.

Why: 13 of 28 clips uploads were withdrawn, mostly for wrong or mismatched archive images, and the reviewer gave
the Hunley Short visual_match 9 while 2003 photos of a recovered pocket watch and wallet played under "sank during
trial dives" and "Housatonic sank within minutes". It saw one frame per scene and no metadata, and its prompt
told it any period image of the subject was correct. A genuine artifact is not a picture of an event.

Decisions:
  MATCH       the asset depicts the narrated subject or action (metadata names it; the reviewer agrees)
  CONTEXTUAL  relevant background (generic stock, starfield, a subject image whose fit metadata can't confirm,
              an artifact under a non-event line); fine, but the narration may not present it as documentary
  MISMATCH    wrong subject, wrong period, a different event, an artifact or later photo under event narration,
              another celestial body, or "this photo shows" over a contextual image
  UNVERIFIED  no provenance (source id / page / licence), or the reviewer gave no judgement for the scene
Rules: MISMATCH and UNVERIFIED never pass (visuals swaps the asset before render; the gate fails the video
otherwise). An AI "MATCH" can't upgrade a scene whose metadata doesn't name the subject: at most CONTEXTUAL.
"""
import re

GENERATED = {"stars", "compare", "veo", "motion"}
STOCK = {"pexels", "pixabay"}
EVENT = re.compile(
    r"\b(sank|sunk|sinks?|sinking|attack(ed|s)?|kill(ed|s|ing)?|explod(ed|es)|exploded|died|dies|rammed|crash(ed|es)?|"
    r"fought|escap(ed|es)|rescu(ed|es)|vanish(ed|es)|struck|burn(ed|t)|landed|collid(ed|es)|fired|drown(ed)?|"
    r"arrest(ed)?|execut(ed)|erupt(ed|s)?|collaps(ed|es)|flooded|stormed|captured|invaded|destroyed|shot|bombed|"
    r"blew up|went down|disappeared|never returned|lost)\b", re.I)
ARTIFACT = re.compile(r"artifact|artefact|recovered|excavat|conservat|museum|exhibit|on display|belonged to|"
                      r"wallet|pocket ?watch|\bcoins?\b|relic|remains of|\bwreck(age)?\b|replica|model of|"
                      r"reconstruct|laborator|archaeolog|reenact|re-enact", re.I)
LATER_REF = re.compile(r"\b(today|now|museum|recovered|raised in|found|discovered|wreck|remains|later|"
                       r"centuries|decades)\b", re.I)
DOCUMENTARY = re.compile(r"\b(this (photo|photograph|picture|image|footage|painting)|here you see|seen here|"
                         r"pictured|caught on (camera|film)|actual (photo|footage)|real footage|"
                         r"this is (him|her|the (actual|real)))\b", re.I)
BODIES = ["sun", "moon", "earth", "mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "pluto",
          "titan", "europa", "io", "ganymede", "enceladus", "black hole", "neutron star", "pulsar", "white dwarf",
          "milky way", "andromeda", "comet", "asteroid", "nebula", "galaxy", "supernova", "quasar",
          "callisto", "dione", "mimas", "rhea", "tethys", "iapetus", "hyperion", "triton", "phobos", "deimos",
          "charon", "miranda", "titania", "oberon"]
OTHER_PLANETS = {"mercury", "venus", "mars", "jupiter", "saturn", "uranus", "neptune", "pluto"}
YEAR = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")
ORDER = {"MATCH": 0, "CONTEXTUAL": 1, "UNKNOWN": 1, "UNVERIFIED": 2, "MISMATCH": 3}


def _words(text):
    from .visuals import key_words
    return set(key_words(str(text or "")))


def _years(text):
    return [int(y) for y in YEAR.findall(str(text or ""))]


def _bodies(text):
    t = " " + re.sub(r"[^a-z ]", " ", str(text or "").lower()) + " "
    return {b for b in BODIES if f" {b} " in t or f" {b}s " in t}


DOD_ID = re.compile(r"(?:US[ _]Navy|DoD|USAF|US[ _]Army)[ _](\d{2})(\d{2})(\d{2})-", re.I)  # US_Navy_030307 = 2003-03-07


def _page_title(page):
    from urllib.parse import unquote
    return unquote(str(page or "").rsplit("File:", 1)[-1]).replace("_", " ") if "File:" in str(page or "") else ""


def asset_of(sc):
    """The scene's asset with its provenance and descriptive metadata, from what visuals.gather recorded. Records
    made before 2 Nov kept only the Commons page: its file name stands in for the title, and a DoD photo id
    gives the date the photo was taken."""
    r, meta = sc.get("rights") or {}, sc.get("asset") or {}
    cf = sc.get("commons_file") or {}
    page = r.get("page") or cf.get("page") or meta.get("page")
    title = cf.get("title") or meta.get("title") or _page_title(page)
    date = cf.get("date") or meta.get("date", "")
    m = DOD_ID.search(title.replace(" ", "_"))
    if not date and m:
        yy = int(m.group(1))
        date = f"{2000 + yy if yy < 50 else 1900 + yy}-{m.group(2)}-{m.group(3)}"
    return {"file": str(sc.get("media", "")).rsplit("/", 1)[-1], "source": r.get("source"),
            "type": sc.get("media_kind"), "id": r.get("asset_id"), "page": page,
            "license": r.get("file_license") or r.get("license"), "title": title,
            "description": cf.get("description") or meta.get("description", ""),
            "date": date, "tags": cf.get("categories") or meta.get("tags", ""),
            "reused_from": str(sc.get("reused_from") or "").rsplit("/", 1)[-1] or None}


def judge(text, subject, asset, story_years=(), story=True):
    """Deterministic decision for one asset under one narration line: (decision, [reasons]). UNKNOWN means the
    metadata neither confirms nor contradicts the fit (the reviewer decides, capped at CONTEXTUAL)."""
    src = asset.get("source")
    if src in GENERATED:
        if src == "compare":
            return "MATCH", ["to-scale graphic built from the scene's own verified values"]
        if src == "motion":
            return "MATCH", ["original procedural animation built for this scene's named subject"]
        return "CONTEXTUAL", [f"generated illustration ({src}), not a depiction of the narrated thing"]
    missing = [k for k in ("id", "license") if not asset.get(k)] + (["page"] if src == "commons" and not asset.get("page") else [])
    if not src or missing:
        return "UNVERIFIED", [f"missing provenance: {', '.join(missing) or 'source'}"]
    if src in STOCK:
        return "CONTEXTUAL", [f"generic stock footage ({src}: '{asset.get('title') or asset.get('tags') or '?'}')"]
    hay = " ".join(str(asset.get(k) or "") for k in ("title", "description", "tags"))
    event = bool(EVENT.search(text))
    reasons = []
    said, shown = _bodies(text), _bodies(hay)
    if said and shown and not (said & shown):  # "a frame of Earth while talking about a neutron star"
        return "MISMATCH", [f"narration is about {', '.join(sorted(said))}; the image is of {', '.join(sorted(shown))}"]
    if "moon" in said and (shown & OTHER_PLANETS) - said:  # 4 Nov: Cassini's "Saturn's moons Dione and Mimas"
        return "MISMATCH", [f"narration is about the Moon; the image is of {', '.join(sorted((shown & OTHER_PLANETS) - said))}"
                            " or its moons"]  # shared the word "moon" and passed as a MATCH under Earth's Moon
    titled = _bodies(asset.get("title"))  # 3 Nov: "Europa's Mysterious Interior" under Jupiter-core narration (the
    if said and titled and not (said & titled):  # description said "Jupiter's moon", so the check above let it by)
        return "MISMATCH", [f"narration is about {', '.join(sorted(said))}; the image's title names "
                            f"{', '.join(sorted(titled))}"]
    made = _years(asset.get("date")) or ([] if src == "nasa" else _years(asset.get("title")))
    if story and made and story_years:
        y, lo, hi = min(made), min(story_years), max(story_years)
        later_ok = bool(LATER_REF.search(text)) or any(abs(v - y) <= 5 for v in _years(text))
        if y < lo - 30:
            return "MISMATCH", [f"image dated {y}, decades before the story ({lo}-{hi}): wrong period"]
        if y > hi + 25 and not later_ok:
            reasons.append(f"image dated {y}, long after the story ({lo}-{hi})")
            if event:
                return "MISMATCH", reasons + ["a later photo under event narration reads as the event itself"]
        scene_years = [v for v in _years(text) if lo <= v <= hi]
        if event and scene_years and lo - 30 <= y <= hi + 25 and all(abs(v - y) > 3 for v in scene_years):
            return "MISMATCH", [f"image dated {y} under an event in {scene_years[0]}: a different moment of the subject"]
    art = ARTIFACT.search(hay)
    if art:
        named = (_words(asset.get("title")) - _words(subject)) & _words(text)
        if not named:
            if event:
                return "MISMATCH", [f"{art.group(0)} image (artifact/later record) under event narration: it shows "
                                    "an object, not what happened"]
            reasons.append(f"{art.group(0)} image under narration that doesn't mention the object")
            return "CONTEXTUAL", reasons
    need = _words(subject)
    got = need & _words(hay)
    if need and len(got) >= min(2, len(need)):
        return ("CONTEXTUAL", reasons) if reasons else ("MATCH", [f"metadata names the subject ({', '.join(sorted(got))})"])
    return "UNKNOWN", reasons + [f"metadata doesn't name the subject ({subject or '?'})"]


def subject_of(sc, script=None):
    return (sc.get("subject") or sc.get("commons_subject") or (script or {}).get("subject_query")
            or sc.get("nasa") or sc.get("commons") or sc.get("visual") or "")


def records(timeline, script=None, story=True):
    """One grounding record per scene from the deterministic rules (the reviewer's view is added by combine)."""
    years = _years(" ".join(sc.get("text", "") for sc in timeline))
    out = []
    for i, sc in enumerate(timeline):
        asset = asset_of(sc)
        subject = subject_of(sc, script)
        decision, reasons = judge(sc.get("text", ""), subject, asset, years, story)
        out.append({"scene": i, "narration": sc.get("text", ""),
                    "intended": sc.get("shows") or sc.get("commons") or sc.get("nasa") or sc.get("visual") or "",
                    "subject": subject, "action": sorted({m.group(0).lower() for m in EVENT.finditer(sc.get("text", ""))}),
                    "period": sorted(set(_years(sc.get("text", ""))) or set(years))[:4],
                    "asset": asset, "deterministic": decision, "reasons": reasons, "reviewer": None,
                    "decision": "CONTEXTUAL" if decision == "UNKNOWN" else decision})
    return out


def combine(recs, judged):
    """Final decision per scene from the deterministic one and the reviewer's {scene, shows, decision, why}."""
    by = {}
    for j in judged if isinstance(judged, list) else []:
        try:
            by[int(j.get("scene"))] = j
        except (TypeError, ValueError, AttributeError):
            continue
    for r in recs:
        ai = by.get(r["scene"])
        det = r["deterministic"]
        verdict = str((ai or {}).get("decision", "")).upper().strip()
        r["reviewer"] = {"shows": ai.get("shows"), "decision": verdict, "why": ai.get("why")} if ai else None
        if det in ("MISMATCH", "UNVERIFIED"):
            final = det
        elif not ai or verdict not in ("MATCH", "CONTEXTUAL", "MISMATCH"):
            final, r["reasons"] = "UNVERIFIED", r["reasons"] + ["the reviewer gave no judgement for this scene"]
        elif verdict == "MISMATCH":
            final = "MISMATCH"
        elif det == "MATCH" and verdict == "MATCH":
            final = "MATCH"
        else:  # metadata can't confirm it, or the reviewer sees background only: never more than CONTEXTUAL
            final = "CONTEXTUAL"
        if final == "CONTEXTUAL" and DOCUMENTARY.search(r["narration"]):
            final = "MISMATCH"
            r["reasons"].append("narration presents a contextual image as documentary evidence")
        r["decision"] = final
    return recs


def gate(recs, cfg, final=True):
    """Issues that stop the video (empty list = pass). Before render (final=False) only MISMATCH and missing
    provenance count; after review every rule does: no MISMATCH/UNVERIFIED, enough MATCH scenes
    (visual.min_match_share), no image on more than visual.max_image_uses scenes."""
    vc = cfg.get("visual", {})
    bad = [r for r in recs if r["decision"] == "MISMATCH" or (r["decision"] == "UNVERIFIED"
                                                              and (final or r["deterministic"] == "UNVERIFIED"))]
    issues = [f"scene {r['scene']}: {r['decision']} ({'; '.join(r['reasons'])[:140]})" for r in bad]
    if final and recs:
        share = sum(r["decision"] == "MATCH" for r in recs) / len(recs)
        need = vc.get("min_match_share", 0)
        if share < need:
            issues.append(f"only {share:.0%} of scenes show the narrated subject (MATCH), need {need:.0%}")
    uses = {}
    for r in recs:
        key = r["asset"].get("page") or r["asset"].get("id") or r["asset"].get("file")  # the page names the file shown
        if r["asset"].get("source") not in GENERATED:
            uses[key] = uses.get(key, 0) + 1
    cap = vc.get("max_image_uses")
    over = {k: n for k, n in uses.items() if cap and n > cap}
    if over:
        issues.append("image reused too often: " + ", ".join(f"{k} ×{n}" for k, n in over.items()) + f" (max {cap})")
    return issues


def stats(recs):
    n = len(recs) or 1
    return {d: round(sum(r["decision"] == d for r in recs) / n, 2) for d in ("MATCH", "CONTEXTUAL", "MISMATCH", "UNVERIFIED")}
