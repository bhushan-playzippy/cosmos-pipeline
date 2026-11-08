"""Claim-level verification after the Hunley withdrawal (1 Nov): coverage, statuses, documents, independence,
and guards on every transformation that can change a fact. No model or network calls: all faked."""
import re

from pipeline import evidence, facts, jobs, llm, orchestrator, storygen, verify
from tests.conftest import make_script

HUNLEY_6 = "They struck the Housatonic with a five‑foot spar torpedo loaded with about 80 pounds of black powder."
WIKI = [("H. L. Hunley", "https://en.wikipedia.org/wiki/H._L._Hunley", [
    "Instead, a spar torpedo—a copper cylinder containing 135 pounds (61 kilograms) of black powder—was "
    "attached to a 22-foot (6.7 m)-long wooden spar, as seen in illustrations made at this time.",
    "Hunley was designed for a crew of eight, seven to turn the hand-cranked ducted propeller and one to steer.",
    "The torpedo was detonated, sending Housatonic to the bottom in three minutes, along with five of her crewmen."])]


def claim(**k):
    return {"scene": 1, "claim": "c", "verdict": "supported", "confidence": "high", "source": "NASA fact sheet", **k}


def script_with(*lines, title="A plain title", hook=""):
    s = make_script("t")
    s["scenes"] = [{"text": t, "visual": "x"} for t in lines]
    s["youtube"].update(title=title, hook_text=hook)
    return s


# ---------------------------------------------------------------- parsing what a text asserts

def test_quantities_read_words_units_scales_and_skip_date_days():
    assert facts.quantities(HUNLEY_6) == [(5, "ft"), (80, "lb")]
    assert facts.quantities("launched on February 17, 1864") == [(1864, None)]
    assert facts.quantities("Seventeen hundred people, 4.5 billion years, 1,300 tons") == [
        (1700, "people"), (4.5e9, "yr"), (1300, "t")]
    assert facts.quantities("One of those accidents") == []          # prose "one" isn't a quantity
    assert facts.strong_words("billions of tons of lethal plasma, the first sub") >= {"billions", "lethal", "first"}


# ---------------------------------------------------------------- the Hunley regression

def test_a_number_without_its_own_ledger_entry_fails():
    s = script_with("Hook line here.", HUNLEY_6)
    gaps = verify.coverage(s, [claim(scene=2, narrated=80, independent=80, unit="lb")])
    assert [g["claim"].split(" in:")[0] for g in gaps] == ["5 ft"]   # the spar length nobody checked


def test_document_contradiction_rejects_even_when_the_model_agrees(cfg):
    s = script_with("Hook line here.", HUNLEY_6)
    bad = verify.classify([claim(scene=2, narrated=80, independent=80, unit="lb", source="NPS"),
                           claim(scene=2, narrated=5, independent=5, unit="ft", source="NPS")], s, cfg, WIKI)
    assert {c["status"] for c in bad} == {"REJECTED"} and len(bad) == 2
    assert "135" in bad[0]["why"] and "22" in bad[1]["why"] and bad[0]["conflicts"][0]["source"].startswith("https://")


def test_wrong_unit_does_not_borrow_support_from_another_quantity():
    # "five-foot" must not be "supported" by "five of her crewmen" (same number, different unit)
    (q, r), _ = evidence.assess(HUNLEY_6, WIKI, 0.25)
    assert q == (5, "ft") and r[0] == "conflict" and r[3] == [22.0]
    assert evidence.assess("The torpedo sent Housatonic to the bottom with five of her crewmen.", WIKI, 0.25)[0][1][0] == "support"


def test_document_support_is_cited_as_evidence(cfg):
    s = script_with("Hook line here.", "Seven men turned the hand-cranked propeller while one steered.")
    claims = [claim(scene=2, narrated=7, independent=7, unit="count")]
    assert verify.classify(claims, s, cfg, WIKI) == []
    assert claims[0]["status"] == "VERIFIED"


def test_full_check_fails_the_hunley_scene_after_repair(cfg, monkeypatch):
    s = script_with("How did a hand-cranked boat sink twice?", HUNLEY_6, title="Hunley's Deadly 80-Pound Bomb")
    monkeypatch.setattr(evidence, "reference", lambda q, pages=2: WIKI)
    ledger = {"claims": [claim(scene=2, narrated=80, independent=80, unit="lb"),
                         claim(scene="title", narrated=80, independent=80, unit="lb")]}
    monkeypatch.setattr(verify, "ask_json", lambda p, c, **k: {"script": s} if k["purpose"] == "verify_repair"
                        else ledger)
    try:
        verify.check(s, cfg)
        raise AssertionError("the Hunley scene must not pass")
    except ValueError as e:
        assert "still failing" in str(e)


# ---------------------------------------------------------------- statuses

def test_conflicting_sources_pass_only_hedged_and_never_in_the_title(cfg):
    c = dict(narrated=90, independent=90, unit="lb", conflicting=[135])
    hedged = script_with("Hook line here.", "Estimates of the charge run to about 90 pounds of powder.")
    flat = script_with("Hook line here.", "The charge was 90 pounds of powder.")
    assert verify.classify([claim(scene=2, **c)], hedged, cfg) == []
    assert verify.classify([claim(scene=2, **c)], flat, cfg)[0]["status"] == "CONFLICTING_SOURCES"
    assert verify.classify([claim(scene="title", **c)], script_with("x", title="The 90-Pound Bomb"), cfg)


def test_approximate_values_must_be_hedged(cfg):
    flat = script_with("Hook.", "The probe flies 115 km above the surface.")
    hedged = script_with("Hook.", "The probe flies about 115 km above the surface.")
    c = claim(scene=2, narrated=115, independent=100, unit="km")
    assert verify.classify([dict(c)], flat, cfg)[0]["why"].startswith("approximate value stated as exact")
    ok = [dict(c)]
    assert verify.classify(ok, hedged, cfg) == [] and ok[0]["status"] == "APPROXIMATE"


def test_missing_or_generic_evidence_is_unsupported(cfg):
    s = script_with("Hook.", "It sank in 3 minutes.")
    for c in (claim(scene=2, narrated=3, independent=None, unit="min"),
              claim(scene=2, narrated=3, independent=3, unit="min", source="General Historical Consensus"),
              claim(scene=2, narrated=3, independent=3, unit="min", source="")):
        assert verify.classify([c], s, cfg)[0]["status"] == "UNSUPPORTED"


def test_rhetorical_lines_and_titles_need_no_source_of_their_own(cfg):
    s = script_with("How did a hand-cranked boat sink a warship?", title="The Hand-Cranked Sub", hook="SANK A WARSHIP")
    # 2 Nov: a line is exempt only when marked rhetorical AND it adds nothing the narration doesn't state
    claims = [claim(scene=1, source="", importance="rhetorical"),
              claim(scene="title", source="General consensus", importance="rhetorical"),
              claim(scene="headline", source=None, importance="rhetorical")]
    assert verify.classify(claims, s, cfg) == [] and verify.coverage(s, claims) == []


def test_superlative_needs_a_ledger_entry_for_its_scene():
    s = script_with("Hook.", "It was the first submarine to sink a warship.")
    assert verify.coverage(s, [claim(scene=1)])[0]["why"].startswith("superlative not checked")
    assert verify.coverage(s, [claim(scene=1), claim(scene=2)]) == []


# ---------------------------------------------------------------- titles and rewrites

def test_title_may_not_claim_more_than_the_narration():
    s = script_with("A boat sank twice.", "It carried a torpedo of black powder.", title="The Deadliest 80-Pound Bomb")
    why = verify.title_issues(s)[0]["why"]
    assert "80" in why and "deadliest" in why
    assert verify.title_issues(script_with("It was lethal.", title="The Deadly Sub")) == []   # same claim, synonym


def test_hook_polish_cannot_inflate_facts(cfg, monkeypatch):
    s = script_with("What protects Earth from the Sun?", "The solar wind carries about 1 million tons an hour.",
                    "Earth's magnetic field deflects most of it.", "So what protects Earth from the Sun?")
    monkeypatch.setattr(storygen, "ask_json", lambda *a, **k: {
        "first": "What stops billions of tons of lethal plasma?", "last": "That is what stops the plasma."})
    out = storygen.polish(s, cfg)
    assert out["scenes"][0]["text"] == "What protects Earth from the Sun?"       # rewrite rejected


def test_fact_trail_records_numbers_a_rewrite_changed():
    s = script_with("A spar of 22 feet.")
    before = facts.snapshot(s)
    s["scenes"][0]["text"] = "A five-foot spar."
    facts.trail(s, "fact_check", before)
    assert s["fact_trail"] == [{"stage": "fact_check", "added": [5.0], "removed": [22.0], "words": []}]


# ---------------------------------------------------------------- independence

def test_verifier_avoids_the_models_that_wrote_and_checked_the_script(cfg, monkeypatch):
    s = script_with("Plain words only here.")
    s["provenance"] = {"write": "groq:qwen", "fact_check": "groq:gpt-oss", "polish": "gemini-flash-lite-latest"}
    seen = []
    monkeypatch.setattr(verify, "ask_json", lambda p, c, **k: seen.append(k["avoid"]) or {"claims": [claim()]})
    verify.check(s, cfg)
    assert seen == [{"groq:qwen", "groq:gpt-oss"}]


def test_avoided_models_are_never_asked(cfg, monkeypatch):
    p = {"name": "groq"}
    monkeypatch.setattr(llm, "alt_models", lambda c, vision=False: [(p, "a"), (p, "b")])
    asked = []
    monkeypatch.setattr(llm, "_ask_alt", lambda alt, *a, **k: asked.append([m for _, m in alt]) or {"ok": 1})
    cfg.setdefault("fallback", {})["prefer_for"] = ["verify"]
    assert llm.ask_json("p", cfg, strong=True, purpose="verify", avoid={"groq:a"}) == {"ok": 1}
    assert asked == [["b"]]


# ---------------------------------------------------------------- rerender and reviewer disagreement

def test_rerender_of_an_edited_script_is_verified_again(cfg, monkeypatch, fixed_now):
    from tests.test_pipeline_units import _fake_production
    _fake_production(monkeypatch)
    calls = []
    ledger = [{"claim": "c", "decision": "pass"}]  # what a passed check records (the release gate reads it)
    monkeypatch.setattr("pipeline.verify.check", lambda s, c: calls.append(1) or {**s, "claims": ledger,
                                                                                  "claims_for": verify.fingerprint(s)})
    s = make_script("Edited")
    s["claims"], s["claims_for"] = ledger, verify.fingerprint(s)
    orchestrator.make_video(cfg, script=s)
    assert calls == []                                        # untouched since its check: no model call
    s["scenes"][2]["text"] = "A hand edit with a new number 42."
    orchestrator.make_video(cfg, script=s)
    assert calls == [1]


def test_reviewer_rejection_quarantines_even_after_verify_passed(cfg, monkeypatch, fixed_now):
    from tests.test_pipeline_units import _fake_production
    _fake_production(monkeypatch, review_pass=False)
    job = orchestrator.make_video(cfg, topic="Verified but the reviewer saw a wrong caption")
    assert job["status"] == "quarantined" and job["status"] not in jobs.LIVE
    assert re.search("AI reviewer", job["reason"])


def test_a_gate_remake_reuses_the_verified_script(cfg, monkeypatch, fixed_now):
    from tests.test_pipeline_units import _fake_production
    _fake_production(monkeypatch, review_pass=False)
    checks = []

    def fake_check(s, c):
        checks.append(1)
        s = dict(s, claims=[claim()])
        s["scenes"][0] = dict(s["scenes"][0], text="Repaired line.")
        s["claims_for"] = verify.fingerprint(s)
        return s
    monkeypatch.setattr("pipeline.verify.check", fake_check)
    job = orchestrator.make_video(cfg, script=make_script("Edited"))
    assert job["status"] == "quarantined" and checks == [1]      # attempt 2 used the repaired, verified version


def test_a_question_to_the_viewer_is_not_an_unsourced_quote(cfg):
    # 5 Nov: Dunkirk's closing question failed as "quote with no document stating it"
    s = script_with("Hundreds of small boats crossed to Dunkirk in 1940.", "Would you have taken a small boat to sea?")
    ask = claim(scene=2, claim="Would you have taken a small boat to sea?", category="quote", importance="core")
    assert verify.classify([ask], s, cfg) == []
    # a question that slips in a number the narration never states is still checked
    s2 = script_with("Small boats crossed to Dunkirk.", "Why did 900 boats sail?")
    q900 = claim(scene=2, claim="Why did 900 boats sail?", category="quote", importance="core")
    assert verify.classify([q900], s2, cfg)
    # and a real quotation stays strict
    s3 = script_with("Small boats crossed to Dunkirk.", 'Churchill said "we shall fight on the beaches"?')
    quote = claim(scene=2, claim='Churchill said "we shall fight on the beaches"?', category="quote", importance="core")
    assert verify.classify([quote], s3, cfg)


def test_core_claim_with_all_terms_on_the_page_is_backed_but_a_quote_is_not(cfg):
    # 5 Nov: the Pig War failed on 12 true core lines whose terms sat in neighbouring sentences of the article
    docs = [("Pig War (1859)", "https://en.wikipedia.org/wiki/Pig_War_(1859)",
             ["On June 15, 1859, the ambiguity led to direct conflict.", "Islands were disputed.",
              "Lyman Cutlar shot a pig that was eating his potatoes."])]
    s = script_with("On June 15, 1859, Lyman Cutlar shot a pig.", "Would you have fired?")
    core = claim(scene=1, claim="Cutlar shot a pig on June 15, 1859", category="description", importance="core",
                 key_terms=["Cutlar", "June"])
    assert verify.classify([core], s, cfg, docs) == []
    said = claim(scene=1, claim="Cutlar said he shot it", category="quote", importance="core",
                 key_terms=["Cutlar", "June"], quote="I shot the pig on June 15")
    assert verify.classify([said], s, cfg, docs)


def test_unusable_key_terms_fall_back_to_the_claims_own_words(cfg):
    # 5 Nov: one model key term left "He spoke within minutes" with no document though the article states it
    docs = [("Phineas Gage", "u", ["Gage spoke within a few minutes and sat upright in an oxcart."])]
    s = script_with("Gage spoke within minutes, then rode an oxcart.", "Would you keep the rod?")
    c = claim(scene=1, claim="Gage spoke within minutes", category="description", importance="core",
              key_terms=["Gage"])
    assert verify.classify([c], s, cfg, docs) == []
    assert c["evidence_type"] == "document"
