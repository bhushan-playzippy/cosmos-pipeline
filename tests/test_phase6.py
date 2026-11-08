"""Phase 6 (2 Nov): scene-level visual grounding, evidence for non-numeric claims, verification of every visible
text, growth signals in live topic selection, model-family independence and quota handling."""
import json

import pytest

from conftest import add_job, make_script
from pipeline import control, evidence, grounding, growth, ideas, jobs, llm, orchestrator, verify


def commons(title, date="", **k):
    return {"source": "commons", "id": "commons:1", "license": "Public domain", "page": "https://commons/x",
            "title": title, "date": date, **k}


# ---------------------------------------------------------------- A. visual grounding (deterministic)

def test_correct_subject_but_wrong_event_is_a_mismatch():
    d, why = grounding.judge("In 1941 the USS Arizona exploded at Pearl Harbor.", "USS Arizona",
                             commons("USS Arizona at her commissioning", "1916"), [1941])
    assert d == "MISMATCH" and "different moment" in why[0]


def test_genuine_artifact_under_an_event_line_is_a_mismatch():
    watch = commons("The pocket watch that belonged to Lt. George Dixon of the H.L. Hunley")
    assert grounding.judge("The Hunley sank during trial dives.", "H.L. Hunley", watch, [1863])[0] == "MISMATCH"
    # the same artifact under a line that isn't an event is background, not a depiction
    assert grounding.judge("The crew were volunteers from Mobile.", "H.L. Hunley", watch, [1863])[0] == "CONTEXTUAL"
    # and it fits a line about the object itself
    assert grounding.judge("Dixon's pocket watch stopped at 8:23.", "H.L. Hunley", watch, [1863])[0] != "MISMATCH"


def test_incorrect_historical_period_is_a_mismatch():
    assert grounding.judge("Charleston harbor was blockaded.", "Charleston harbor",
                           commons("Charleston harbor engraving", "1790"), [1863, 1864])[0] == "MISMATCH"


def test_later_photo_and_replica_under_event_narration_mislead():
    assert grounding.judge("It attacked the Housatonic.", "Hunley", commons("Hunley in its conservation tank", "2004"),
                           [1864])[0] == "MISMATCH"
    assert grounding.judge("It attacked the Housatonic.", "Hunley", commons("Replica of the H.L. Hunley"),
                           [1864])[0] == "MISMATCH"


def test_contextual_image_presented_as_documentary_fails():
    recs = [{"scene": 0, "narration": "This photo shows the crew minutes before the attack.", "deterministic": "UNKNOWN",
             "reasons": [], "asset": commons("Charleston 1863"), "decision": "CONTEXTUAL"}]
    out = grounding.combine(recs, [{"scene": 0, "decision": "MATCH"}])
    assert out[0]["decision"] == "MISMATCH" and "documentary" in out[0]["reasons"][-1]


def test_scientific_visual_mismatch():
    nasa = {"source": "nasa", "id": "nasa:1", "license": "NASA", "title": "Earth seen from the ISS"}
    assert grounding.judge("A neutron star spins hundreds of times a second.", "neutron star", nasa, story=False)[0] \
        == "MISMATCH"
    generic = {"source": "pexels", "id": "pexels:1", "license": "Pexels", "title": "space"}
    assert grounding.judge("A neutron star spins fast.", "neutron star", generic, story=False)[0] == "CONTEXTUAL"


def test_missing_provenance_is_unverified_and_stops_before_render(cfg):
    tl = [{"text": "The ship sank.", "rights": {"source": "commons"}, "media": "a.jpg"}]
    recs = grounding.records(tl, {"subject_query": "ship"})
    assert recs[0]["decision"] == "UNVERIFIED"
    assert grounding.gate(recs, cfg, final=False)


def test_ai_match_cannot_upgrade_unconfirmed_metadata_and_silence_is_unverified(cfg):
    recs = grounding.records([{"text": "Hunley's crew cranked.", "rights": {"source": "commons", "asset_id": "c:1",
                                                                          "license": "PD", "page": "p"},
                               "commons_file": {"title": "Charleston harbour"}}], {"subject_query": "H.L. Hunley"})
    assert grounding.combine([dict(recs[0])], [{"scene": 0, "decision": "MATCH"}])[0]["decision"] == "CONTEXTUAL"
    silent = grounding.combine(grounding.records([{"text": "x", "rights": {"source": "stars"}}]), [])
    assert silent[0]["decision"] == "UNVERIFIED" and grounding.gate(silent, cfg)


def test_reuse_cap_and_match_share_gate(cfg):
    cfg["visual"] = {"min_match_share": 0.5, "max_image_uses": 2}
    recs = [{"scene": i, "decision": "CONTEXTUAL", "deterministic": "CONTEXTUAL", "reasons": [],
             "asset": {"source": "commons", "page": "same"}} for i in range(3)]
    issues = grounding.gate(recs, cfg)
    assert any("reused too often" in i for i in issues) and any("MATCH" in i for i in issues)


def test_hunley_regression_scenes_flagged(cfg):
    """The pending Hunley video's own scenes (as recorded, before metadata was stored): the four scenes the human
    review flagged are MISMATCH, without any model."""
    watch = "https://commons.wikimedia.org/wiki/File:US_Navy_030307-N-0000X-002_The_pocket_watch_that_belonged_to_" \
            "the_commanding_officer_of_the_Civil_War-era_submarine_%22H.L._Hunley,%22_Lt._George_Dixon.jpg"
    paint = "https://commons.wikimedia.org/wiki/File:Conrad_Wise_Chapman_-_Submarine_Torpedo_Boat_H.L._Hunley,_Dec._6,_1863.jpg"
    rights = lambda page: {"source": "commons", "asset_id": "commons:1", "file_license": "Public domain", "page": page}
    tl = [{"text": "How did a hand-cranked boat sink twice?", "rights": rights(paint)},
          {"text": "In 1863, the H.L. Hunley sank during trial dives in Charleston Harbor.", "rights": rights(watch)},
          {"text": "Raised again, it attacked on the night of February 17, 1864.", "rights": rights(watch)}]
    recs = grounding.records(tl, {"subject_query": "H.L. Hunley submarine"})
    assert [r["decision"] for r in recs] == ["MATCH", "MISMATCH", "MISMATCH"]


def test_reused_image_records_the_id_of_the_image_shown(cfg, monkeypatch, tmp_path):
    from pipeline import visuals
    cfg["video"]["sources"] = ["commons"]
    cfg["writer"] = {"template": "story"}
    calls = []

    def fake(q, workdir, idx, used, subject=None, check=None):
        calls.append(idx)
        if idx == 0:
            p = workdir / "scene_00.jpg"
            p.write_bytes(b"x")
            used.append("commons:111")
            return p, {"license": "Public domain", "page": "https://c/File:A.jpg", "title": "Hunley painting"}
        used.append("commons:999")  # a later search downloaded and rejected something: must not be recorded
        return None, None
    monkeypatch.setattr(visuals, "commons_image", fake)
    tl, _ = visuals.gather([{"text": "a"}, {"text": "b"}], cfg, tmp_path, "hunley")
    assert tl[1]["reused_from"] and tl[1]["rights"]["asset_id"] == "commons:111"


# ---------------------------------------------------------------- B. non-numeric claims

DOC = [("H. L. Hunley", "https://en.wikipedia.org/wiki/H._L._Hunley",
        ["The second sinking killed Horace Lawson Hunley and seven crewmen in October 1863.",
         "Hunley was the first combat submarine to sink a warship.",
         "Farragut is said to have shouted Damn the torpedoes at Mobile Bay."])]


def nclaim(**k):
    return {"scene": 2, "claim": "c", "verdict": "supported", "confidence": "high", "source": "NPS", **k}


def story(*lines):
    s = make_script("t")
    s["scenes"] = [{"text": t} for t in lines]
    s["youtube"].update(title="A plain title", hook_text="")
    return s


def test_unsupported_identity_claim_is_unresolved_not_verified(cfg):
    s = story("Hook.", "The sinking killed the man who designed it, James McClintock.", "End.")
    c = nclaim(category="person", importance="core", key_terms=["killed", "McClintock"])
    cfg["verify"]["max_unresolved"] = 0
    bad = verify.classify([c], s, cfg, DOC)
    assert c["status"] == "UNRESOLVED" and bad and c["citation"] is None


def test_identity_claim_with_document_passage_is_cited(cfg):
    s = story("Hook.", "The second sinking killed Horace Hunley himself.", "End.")
    c = nclaim(category="person", importance="core", key_terms=["killed", "Horace Hunley"])
    assert verify.classify([c], s, cfg, DOC) == []
    assert c["status"] == "VERIFIED" and c["citation"]["kind"] == "direct" and "killed" in c["citation"]["passage"]
    assert c["citation"]["quality"] == "tertiary"  # Wikipedia is labelled as what it is


def test_key_terms_must_come_from_the_text(cfg):
    s = story("Hook.", "Hunley drowned because the hatch failed.", "End.")
    c = nclaim(category="cause", importance="core", key_terms=["killed", "Horace Hunley"])  # not in the line
    cfg["verify"]["max_unresolved"] = 0
    assert verify.classify([c], s, cfg, DOC) and c["status"] == "UNRESOLVED"


def test_incorrect_cause_and_effect_is_rejected(cfg):
    s = story("Hook.", "The torpedo exploded because a crewman lit a match.", "End.")
    c = nclaim(category="cause", verdict="wrong", key_terms=["exploded", "match"])
    assert verify.classify([c], s, cfg, DOC)[0]["status"] == "REJECTED"


def test_misattributed_quote_fails_and_a_real_one_is_cited(cfg):
    s = story("Hook.", "Lincoln shouted 'Damn the torpedoes' at Mobile Bay.", "End.")
    bad = nclaim(category="quote", quote="Full speed ahead to glory", key_terms=["Lincoln", "shouted"])
    assert verify.classify([bad], s, cfg, DOC)[0]["status"] == "UNRESOLVED"  # quotes are STRICT
    ok = nclaim(category="quote", quote="Damn the torpedoes at Mobile Bay", key_terms=["shouted"])
    assert verify.classify([ok], s, cfg, DOC) == [] and ok["citation"]["kind"] == "direct"


def test_conflicting_historical_sources_fail_unless_hedged(cfg):
    s = story("Hook.", "The attack killed 5 sailors.", "End.")
    c = nclaim(narrated=5, independent=5, unit="people", conflicting=[8], type="measurement")
    assert verify.classify([c], s, cfg)[0]["status"] == "CONFLICTING_SOURCES"
    disputed = nclaim(category="motive", verdict="disputed")
    assert verify.classify([disputed], s, cfg)[0]["status"] == "CONFLICTING_SOURCES"


def test_unverified_superlative_fails(cfg):
    s = story("Hook.", "It was the first submarine to sink a warship at night.", "End.")
    c = nclaim(category="superlative", key_terms=["first", "night"])
    assert verify.classify([c], s, cfg, DOC)[0]["status"] == "UNRESOLVED"
    s2 = story("Hook.", "Hunley was the first combat submarine to sink a warship.", "End.")
    c2 = nclaim(category="superlative", key_terms=["first", "combat submarine", "warship"])
    assert verify.classify([c2], s2, cfg, DOC) == [] and c2["status"] == "VERIFIED"


def test_model_named_source_is_not_a_citation_and_only_allowlisted_pages_are_fetched(cfg):
    s = story("Hook.", "The submarine was raised in 2000.", "End.")
    c = nclaim(category="event", importance="supporting", source="Made-up Journal", source_url="http://blog.example/x")
    verify.classify([c], s, cfg)
    assert c["citation"] is None and c["proposed_source"]["checked"].startswith("not fetched")
    assert evidence.source_quality("http://nasa.gov/x") is None  # http is never fetched
    assert evidence.source_quality("https://www.nasa.gov/x") == "primary/institutional"
    assert evidence.source_quality("https://evil.com/nasa.gov") is None


def test_rhetorical_label_cannot_hide_a_new_fact(cfg):
    s = story("It carried 40 men and sank three ships.", "It sank once.", "End.")
    c = nclaim(scene=1, category="event", importance="rhetorical")
    cfg["verify"]["max_unresolved"] = 0
    assert verify.classify([c], s, cfg) and c["status"] == "UNRESOLVED"


# ---------------------------------------------------------------- C. every visible text is covered

def verified_script():
    s = make_script("v")
    s["claims"] = []
    s["claims_for"] = verify.fingerprint(s)
    return s


def test_edited_headline_title_narration_description_each_invalidate():
    for edit in (lambda s: s["youtube"].update(hook_text="NEW HEADLINE"),
                 lambda s: s["youtube"].update(title="New title"),
                 lambda s: s["scenes"][0].update(text="Changed narration."),
                 lambda s: s["youtube"].update(description="Saturn's largest moon.\n\n#Shorts"),
                 lambda s: s["youtube"].update(pinned_comment="Is it 3 km deep?"),
                 lambda s: s["scenes"][1].update(compare={"unit": "m", "items": [{"label": "A", "value": 3}]})):
        s = verified_script()
        assert verify.verified(s)[0]
        edit(s)
        assert not verify.verified(s)[0]


def test_hashtags_and_ai_label_added_after_the_check_do_not_invalidate(cfg):
    from pipeline import scriptgen, virality
    s = verified_script()
    s["youtube"]["description"] = "Body text.\n\nSources: NASA"
    s["claims_for"] = verify.fingerprint(s)
    scriptgen.add_ai_disclosure(s, cfg)
    virality.ensure_hashtags(s, cfg)
    assert verify.verified(s)[0]


def test_description_and_comment_may_not_add_numbers_or_superlatives():
    s = story("The moon is far.", "It is cold.")
    s["youtube"]["description"] = "Titan is Saturn's largest moon, 5,150 km wide.\n\nSources: NASA 2021"
    why = " ".join(i["why"] for i in verify.visible_issues(s))
    assert "description" in why and "5150" in why and "largest" in why and "2021" not in why


def test_mission_names_are_not_extra_numbers():
    s = story("Voyager One is far away.", "Apollo 13 never landed.")
    s["youtube"].update(title="Voyager 1 and Apollo 13", description="Voyager 1 is far.\n\n#Shorts")
    assert verify.visible_issues(s) == []
    s["youtube"]["description"] = "Voyager 1 is 15 billion miles away."
    assert "1.5e+10" in verify.visible_issues(s)[0]["why"]  # a real added quantity (15 billion) is still caught


def test_graphic_numbers_need_ledger_entries():
    s = story("Kuiper belt is wide.", "Very wide.")
    s["scenes"][1]["compare"] = {"unit": "km", "items": [{"label": "Kuiper Belt", "value": 2990000000}]}
    assert any(g["scene"] == "graphic" for g in verify.coverage(s, []))
    assert not any(g["scene"] == "graphic" for g in verify.coverage(s, [{"scene": "graphic", "narrated": 2.99e9}]))


def test_rerender_reverifies_an_edited_headline(cfg, monkeypatch, fixed_now):
    from test_pipeline_units import _fake_production
    _fake_production(monkeypatch)
    seen = []
    monkeypatch.setattr("pipeline.verify.check", lambda s, c: seen.append(s["youtube"]["hook_text"]) or {
        **s, "claims": [{"claim": "c", "decision": "pass"}], "claims_for": verify.fingerprint(s)})
    s = verified_script()
    s["youtube"]["hook_text"] = "HAND EDITED"
    orchestrator.make_video(cfg, script=s)
    assert seen == ["HAND EDITED"]


def test_existing_job_migration(cfg, monkeypatch, fixed_now):
    """A v1 ledger (narration + title) stays valid while the other visible text is clean; a description that
    adds a number is caught at publish, not uploaded."""
    s = make_script("legacy")
    s["claims"] = []
    s["claims_for"] = verify.legacy_fingerprint(s)
    ok, how = verify.verified(s)
    assert ok and "legacy" in how
    s["youtube"]["description"] = "It is 12,000 km away.\n\n#Shorts"
    assert not verify.verified(s)[0]
    cfg["youtube"]["enabled"] = True
    add_job("legacy_job", script=s)
    monkeypatch.setattr("pipeline.publish_youtube.upload", lambda *a, **k: pytest.fail("uploaded unverified text"))
    assert orchestrator.publish(cfg, "legacy_job")["status"] == "quarantined"


# ---------------------------------------------------------------- D. growth → topic selection

def topics():
    return [{"topic": "Why does Saturn float?", "hook_idea": "Why would Saturn float in a bathtub?", "role": "discovery",
             "score": 20}, {"topic": "Mars dust storms", "hook_idea": "Mars storms cover the planet.",
                            "role": "discovery", "score": 20}]


def boost(question_with, question_without):
    return {"feature_boost": {"question_hook": {"with": question_with, "without": question_without, "n_with": 6,
                                                "n_without": 6, "conclusive": True}}}


def test_growth_weight_changes_the_pick_when_all_else_is_equal(cfg, tmp_path):
    from pipeline.util import write_json
    for w, expected in (((1.2, 0.9), "Why does Saturn float?"), ((0.9, 1.2), "Mars dust storms")):
        write_json(ideas.BACKLOG, topics() * 1 + [{"topic": f"filler {i}", "score": 1} for i in range(9)])
        write_json(jobs.HISTORY.parent / "growth.json", boost(*w))
        pick = ideas.next_topic(cfg, "discovery")
        assert pick["topic"] == expected and pick["selection"]["parts"]["question_hook"] == w[0 if "Why" in expected else 1]


def test_inconclusive_feature_is_ignored(cfg):
    from pipeline.util import write_json
    write_json(jobs.HISTORY.parent / "growth.json",
               {"feature_boost": {"question_hook": {"with": 2, "without": 0.1, "conclusive": False}}})
    m, parts = growth.topic_multiplier(topics()[0], cfg)
    assert "question_hook" not in parts and m == 1.0


def test_topic_selection_is_per_channel(cfg, monkeypatch, tmp_path):
    from pipeline.util import write_json
    picks = []
    for name, w in (("cosmic", (1.2, 0.9)), ("clips", (0.9, 1.2))):
        d = tmp_path / name
        d.mkdir()
        monkeypatch.setattr(jobs, "HISTORY", d / "history.json")
        monkeypatch.setattr(ideas, "HISTORY", d / "history.json")
        monkeypatch.setattr(ideas, "BACKLOG", d / "topics.json")
        write_json(ideas.BACKLOG, topics() + [{"topic": f"filler {i}", "score": 1} for i in range(9)])
        write_json(d / "growth.json", boost(*w))
        picks.append(ideas.next_topic(cfg, "discovery")["topic"])
    assert picks == ["Why does Saturn float?", "Mars dust storms"]


def test_recent_pillar_repeats_are_penalised(cfg):
    t = {"topic": "x", "pillar": "Planets", "score": 10}
    assert growth.topic_multiplier(t, cfg, ["Planets", "Planets"])[1]["repeat_pillar"] == round(0.85 ** 2, 2)


def test_growth_learns_feature_effects_with_shrinkage():
    rows = [({"question_hook": True}, 30.0)] * 6 + [({"question_hook": False}, 10.0)] * 6
    fb = growth._feature_boost(rows, 20.0, 5)["question_hook"]
    assert fb["conclusive"] and 1.0 < fb["with"] <= 1.25 and fb["without"] < 1.0
    assert not growth._feature_boost(rows[:4] + rows[-4:], 20.0, 5)["question_hook"]["conclusive"]


# ---------------------------------------------------------------- E. model independence and quota

class Fake:
    def __init__(self, ok='{"ok": 1}'):
        self.calls, self.ok = [], ok

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        return type("R", (), {"text": self.ok, "usage_metadata": None})()


@pytest.fixture
def gem(monkeypatch):
    f = Fake()
    monkeypatch.setattr("google.genai.Client", lambda **k: type("C", (), {"models": f})())
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr("time.sleep", lambda s: None)
    return f


def test_model_family_independence(cfg, gem):
    assert llm.family("gemini-flash-latest") == llm.family("gemini-3.8-flash") == "gemini"
    assert llm.family("groq:openai/gpt-oss-120b") != llm.family("groq:qwen/qwen3.8-27b")
    # written by one Gemini id: the "independent" check may not use another Gemini id
    with pytest.raises(llm.Deferred, match="family"):
        llm.ask_json("p", cfg, strong=True, purpose="verify", avoid={"gemini-flash-latest"})
    assert gem.calls == []


def test_verify_excludes_every_family_that_wrote_or_checked(cfg, monkeypatch):
    seen = {}
    monkeypatch.setattr(verify, "ask_json", lambda *a, **k: seen.update(avoid=k["avoid"]) or {"claims": [{}]})
    s = make_script("t")
    s["provenance"] = {"write": "gemini-flash-latest", "fact_check": "groq:openai/gpt-oss-120b"}
    verify._claims(s, cfg)
    assert llm.families(seen["avoid"]) == {"gemini", "openai"}


def test_quota_limit_skips_a_model_without_calling_it(cfg, gem):
    first, second = [m for m in cfg["gemini"]["models"] if m not in llm.weak_models(cfg)][:2]
    cfg["gemini"]["daily_limits"] = {first: 2}
    for _ in range(3):
        llm.ask_json("p", cfg, strong=True)
    assert gem.calls == [first, first, second]
    assert llm.usage_today()[first]["calls"] == 2


def test_quota_exhaustion_defers_strong_calls(cfg, gem):
    cfg["gemini"]["daily_limits"] = {m: 0 for m in cfg["gemini"]["models"]}  # every model already at its limit
    with pytest.raises(llm.Deferred):
        llm.ask_json("p", cfg, strong=True, purpose="write")
    assert gem.calls == []


def test_deferred_verify_keeps_the_draft_and_the_next_run_resumes_it(cfg, monkeypatch, fixed_now):
    from test_pipeline_units import _fake_production
    from pipeline import scriptgen
    from pipeline.util import read_json, write_json
    _fake_production(monkeypatch)
    writes = []
    monkeypatch.setattr(scriptgen, "write_script", lambda t, c: writes.append(1) or make_script(t["topic"]))
    monkeypatch.setattr("pipeline.verify.check", lambda s, c: (_ for _ in ()).throw(llm.Deferred("no quota")))
    write_json(ideas.BACKLOG, [{"topic": "Draft topic", "score": 30}] + [{"topic": f"f{i}", "score": 1} for i in range(9)])
    with pytest.raises(llm.Deferred):
        orchestrator.make_video(cfg)
    saved = read_json(ideas.BACKLOG, [])[0]
    assert saved["topic"] == "Draft topic" and saved["draft"]["youtube"] and jobs.load() == []
    monkeypatch.setattr("pipeline.verify.check", lambda s, c: {**s, "claims": [{"claim": "c", "decision": "pass"}],
                                                              "claims_for": verify.fingerprint(s)})
    job = orchestrator.make_video(cfg)
    assert job["status"] == "ready" and writes == [1]  # resumed at the claim check: the writer wasn't called again


def test_dry_run_never_saves_a_draft(cfg, monkeypatch, fixed_now):
    from test_pipeline_units import _fake_production
    from pipeline.util import read_json, write_json
    _fake_production(monkeypatch)
    monkeypatch.setattr(control, "DRY_RUN", True)
    monkeypatch.setattr("pipeline.verify.check", lambda s, c: (_ for _ in ()).throw(llm.Deferred("no quota")))
    write_json(ideas.BACKLOG, [{"topic": "Draft topic", "score": 30}] + [{"topic": f"f{i}", "score": 1} for i in range(9)])
    with pytest.raises(llm.Deferred):
        orchestrator.make_video(cfg)
    assert not any("draft" in t for t in read_json(ideas.BACKLOG, []))


def test_remake_after_a_visual_failure_reuses_the_verified_script(cfg, monkeypatch, fixed_now):
    from test_pipeline_units import _fake_production
    from pipeline import scriptgen
    _fake_production(monkeypatch, qa_pass=False)
    writes = []
    monkeypatch.setattr(scriptgen, "write_script", lambda t, c: writes.append(1) or make_script(t["topic"]))
    monkeypatch.setattr("pipeline.verify.check", lambda s, c: {**s, "claims": [], "claims_for": verify.fingerprint(s)})
    assert orchestrator.make_video(cfg, topic="Twice")["status"] == "quarantined"
    assert writes == [1]  # attempt 2 reused the verified script (no second write/fact-check/verify)


# ---------------------------------------------------------------- F. observability

def test_quality_report_separates_failure_classes(cfg, fixed_now):
    from pipeline import quality
    add_job("2026-10-24_a", status="failed", reason="ValueError: independent check still failing", created="2026-10-24T09:00:00")
    add_job("2026-10-24_b", status="failed", reason="interrupted: the run never finished", created="2026-10-24T09:00:00")
    add_job("2026-10-24_c", status="withdrawn", created="2026-10-24T09:00:00")
    add_job("2026-10-24_d", status="quarantined", reason="AI reviewer: visual grounding failed", created="2026-10-24T09:00:00")
    c = quality.collect(7)
    assert c["classes"] == {"content": 2, "infra": 1, "quota": 0, "human": 1}
    assert c["reviewer_rejections"] == 1
    assert any("dry run" in l for l in quality.report_lines(cfg))


def test_hook_polish_cannot_add_a_person(cfg, monkeypatch):
    from pipeline import storygen
    s = story("A thermostat wire nearly doomed Apollo 13.", "Lovell, Swigert and Haise survived.", "The wire was fixed.")
    s["topic"] = "Apollo 13 thermostat"
    monkeypatch.setattr(storygen, "ask_json", lambda *a, **k: {"first": "Frank Borman felt the blast?",
                                                               "last": "Apollo 13 came home."})
    out = storygen.polish(s, cfg)
    assert out["scenes"][0]["text"] == "A thermostat wire nearly doomed Apollo 13."
    assert facts_new_names() == ["Borman", "Frank"]


def facts_new_names():
    from pipeline import facts
    return facts.new_names("Lovell and Haise on Apollo 13", "Why did Frank Borman never fly Apollo 13?")


def test_daily_report_lists_an_awaiting_video_without_a_slot(cfg, fixed_now):
    from pipeline import analytics
    add_job("2026-10-24_hunley", status="awaiting_approval", slot=None)
    analytics.write_daily_report(cfg)
    text = next((analytics.REPORTS).rglob("daily-*.md")).read_text()
    assert "next free slot" in text


def test_a_failed_rehearsal_does_not_burn_its_topic(cfg, monkeypatch, fixed_now):
    from test_pipeline_units import _fake_production
    from pipeline import scriptgen
    _fake_production(monkeypatch)
    monkeypatch.setattr(control, "DRY_RUN", True)
    monkeypatch.setattr(scriptgen, "write_script", lambda t, c: (_ for _ in ()).throw(RuntimeError("too short")))
    job = orchestrator.make_video(cfg, topic="Saturn rings")
    assert job["status"] == "failed" and jobs.load() == [] and "Saturn rings" not in ideas.done_topics()


def test_too_long_verified_script_is_remade_with_a_faster_voice(cfg, monkeypatch, fixed_now):
    # 4 Nov: the Moon Short passed every fact check, rendered at 65.4 s twice and was quarantined both times
    from test_pipeline_units import _fake_production
    from pipeline import voice, qa, util
    _fake_production(monkeypatch)
    rates = []
    real = voice.synthesize
    monkeypatch.setattr(voice, "synthesize", lambda s, c, w: rates.append(c["voice"].get("rate")) or real(s, c, w))
    monkeypatch.setattr(util, "media_duration", lambda p: 65.4)
    gates = iter([False, True])
    monkeypatch.setattr(qa, "gate", lambda *a: (lambda ok: {"pass": ok, "checks": [
        {"name": "duration", "ok": ok, "detail": "65.4s"}]})(next(gates)))
    job = orchestrator.make_video(cfg, topic="Moon width")
    assert job["status"] == "ready"
    base = float(cfg["voice"]["rate"].rstrip("%"))
    assert rates[0] == cfg["voice"]["rate"] and float(rates[1].rstrip("%")) > base


def test_faster_voice_has_a_ceiling(cfg, monkeypatch, tmp_path):
    from pipeline import util
    monkeypatch.setattr(util, "media_duration", lambda p: 120.0)  # would need ~+120%: not natural speech
    assert orchestrator._faster_voice(cfg, tmp_path / "v.mp4") is cfg
    monkeypatch.setattr(util, "media_duration", lambda p: 30.0)   # not too long: nothing to do
    assert orchestrator._faster_voice(cfg, tmp_path / "v.mp4") is cfg
