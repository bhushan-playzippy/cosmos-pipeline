"""3 Nov evening restore: defects seen in the 21:00 cosmic run (Jupiter core pressure, quarantined twice by the
reviewer; its claims cited to the "Marine life" article). Sandbox only: no real API."""
import pytest

from pipeline import evidence, grounding, visuals

JUPITER = [{"text": "At Jupiter's core, pressure is about 40 million times Earth's sea-level pressure.",
            "visual": "jupiter", "nasa": "Jupiter interior", "subject": "Jupiter core"},
           {"text": "Imagine a regulation basketball, about 9.5 inches across, under that pressure.",
            "visual": "basketball", "nasa": "Jupiter", "subject": "basketball under pressure"}]


@pytest.fixture
def sources(cfg, monkeypatch, tmp_path):
    cfg["video"]["sources"] = ["nasa", "pixabay", "stars"]
    calls = []

    def nasa(q, work, i, used, c, check=None):
        calls.append(("nasa", q))
        info = {"title": "Jupiter Plume", "description": "Clouds on Jupiter", "tags": "", "date": "1999-03-13",
                "page": "https://images.nasa.gov/details/n1"}
        if check and check(info) == "MISMATCH":
            return None
        used.append(f"nasa:n{i}")
        visuals.META[i] = info
        return tmp_path / f"n{i}.jpg"

    def pixabay(q, work, i, used):
        calls.append(("pixabay", q))
        used.append(f"pixabay:p{i}")
        visuals.META[i] = {"title": q, "page": "https://pixabay.com/x", "tags": q}
        return tmp_path / f"p{i}.mp4"

    monkeypatch.setattr(visuals, "nasa_image", nasa)
    monkeypatch.setattr(visuals, "pixabay_video", pixabay)
    return calls


def test_everyday_comparison_scene_gets_stock_not_an_unrelated_nasa_photo(cfg, sources, tmp_path):
    tl, _ = visuals.gather([dict(s) for s in JUPITER], cfg, tmp_path, "jupiter")
    assert ("nasa", "Jupiter interior") in sources            # the space scene still gets NASA
    assert ("nasa", "Jupiter") not in sources                 # the basketball scene's "Jupiter" query is skipped
    assert ("pixabay", "basketball") in sources
    assert tl[0]["rights"]["source"] == "nasa" and tl[1]["rights"]["source"] == "pixabay"


def test_nasa_candidate_must_name_the_scene_subject():
    assert visuals.names_subject("Jupiter core", {"title": "Jupiter Plume"})
    assert not visuals.names_subject("Mariana Trench", {"title": "Clouds and Open Ocean near the Bahamas"})
    assert not visuals.nasa_fits_scene("Jupiter pressure", "school bus compression")
    assert visuals.nasa_fits_scene("Jupiter interior", "Jupiter core")
    assert visuals.nasa_fits_scene("AS11-40-5875", "Buzz Aldrin")
    assert visuals.nasa_fits_scene("S69-31741", "Apollo 11 crew")


def test_image_titled_after_another_body_is_a_mismatch():
    d, why = grounding.judge("At Jupiter's core the pressure is immense.", "Jupiter core",
                             {"source": "nasa", "id": "x", "license": "x", "title": "Europa's Mysterious Interior",
                              "description": "An ocean world orbiting Jupiter"}, (), False)
    assert d == "MISMATCH" and "europa" in why[0]


def test_unitless_number_is_not_supported_by_the_same_number_in_another_unit():
    docs = [("Marine life", "https://en.wikipedia.org/wiki/Marine_life",
             ["The Ediacara biota flourished on Earth for the last 40 million years before the Cambrian."])]
    (_, r), = evidence.assess("Jupiter's core pressure is about 40 million times Earth's pressure.", docs, 0.25)
    assert r is None or r[0] != "support"
    good = [("Jupiter", "https://en.wikipedia.org/wiki/Jupiter",
             ["The pressure at Jupiter's core is about 40 million times the pressure at Earth's surface."])]
    (_, r), = evidence.assess("Jupiter's core pressure is about 40 million times Earth's pressure.", good, 0.25)
    assert r[0] == "support"


def test_invented_source_link_is_dropped_without_touching_the_verification():
    from conftest import make_script
    from pipeline import verify
    s = make_script("Jupiter")
    s["sources"] = ["NASA – Jupiter (https://science.nasa.gov/jupiter/)", "NOAA – Trench (https://www.noaa.gov/mariana-trench)"]
    s["youtube"]["description"] = ("Jupiter's core is crushing.\n\nSources: NASA – Jupiter (https://science.nasa.gov/"
                                   "jupiter/), NOAA – Trench (https://www.noaa.gov/mariana-trench)\n\n#Shorts #Jupiter")
    s["claims_for"] = verify.fingerprint(s)
    dead = evidence.prune_sources(s, check=lambda u: "noaa" in u)
    assert dead == ["https://www.noaa.gov/mariana-trench"]
    assert s["sources"] == ["NASA – Jupiter (https://science.nasa.gov/jupiter/)"]
    assert "noaa" not in s["youtube"]["description"] and "#Shorts #Jupiter" in s["youtube"]["description"]
    assert s["claims_for"] == verify.fingerprint(s)  # the claim check still covers this text


def test_all_sources_dead_falls_back_to_fetched_citations():
    from conftest import make_script
    s = make_script("Jupiter")
    s["sources"] = ["Made up (https://example.org/nope)"]
    s["claims"] = [{"citation": {"title": "Jupiter", "url": "https://en.wikipedia.org/wiki/Jupiter"}}]
    evidence.prune_sources(s, check=lambda u: "example" in u)
    assert s["sources"] == ["Jupiter (https://en.wikipedia.org/wiki/Jupiter)"]


def test_one_overloaded_gemini_model_does_not_skip_a_healthy_one(cfg, monkeypatch):
    """3 Nov: flash-latest and 3.8-flash 503'd all day; 3.6-flash answered. The breaker used to trip all of Gemini
    after the first two failures, so the healthy model was never tried."""
    from pipeline import llm
    from test_pipeline_units import FakeModels
    cfg["gemini"]["models"] = ["gemini-flash-latest", "gemini-3.8-flash", "gemini-3.6-flash", "gemini-flash-lite-latest"]
    m = FakeModels(lambda model: '{"ok": 1}' if model == "gemini-3.6-flash" else RuntimeError("503 UNAVAILABLE"))
    monkeypatch.setattr("google.genai.Client", lambda **k: type("C", (), {"models": m})())
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setattr("time.sleep", lambda s: None)
    monkeypatch.setattr(llm, "EXHAUSTED", set())
    assert llm.ask_json("p", cfg, strong=True, purpose="verify", avoid={"groq:openai/gpt-oss-120b"}) == {"ok": 1}
    assert llm.ask_json("p", cfg, strong=True, purpose="verify", avoid={"groq:openai/gpt-oss-120b"}) == {"ok": 1}
    assert m.calls.count("gemini-flash-latest") == 1 and m.calls.count("gemini-3.8-flash") == 1  # skipped after one 503
    assert "gemini" not in llm.TRIPPED and m.calls.count("gemini-3.6-flash") == 2


def test_another_planets_moons_are_a_mismatch_under_earths_moon():
    # 4 Nov: Cassini's Saturn moons passed the metadata check as a "MATCH" under a hook about Earth's Moon
    a = {"source": "nasa", "id": "x", "license": "x", "title": "Dione and Mimas",
         "description": "Cassini looks across Saturn's moons Dione and Mimas"}
    d, why = grounding.judge("Could a road trip across America span wider than the Moon?", "Moon", a, (), False)
    assert d == "MISMATCH" and "saturn" in why[0]
    earth = {"source": "nasa", "id": "y", "license": "x", "title": "Full Moon over Earth",
             "description": "The Moon rising over Earth's limb"}
    assert grounding.judge("The Moon is 3,474 km across.", "Moon", earth, (), False)[0] != "MISMATCH"


def test_wikipedia_429_is_waited_out_not_read_as_no_articles(monkeypatch):
    # 4 Nov: a 429 with Retry-After: 2 was parsed as bad JSON and research then had no articles at all
    from pipeline import evidence
    naps = []

    class R:
        def __init__(self, code, body=None):
            self.status_code, self._b = code, body
            self.headers = {"retry-after": "2"} if code == 429 else {}
        def raise_for_status(self): pass
        def json(self): return self._b
    answers = iter([R(429), R(429), R(200, {"query": {"search": [{"title": "Apollo 11"}]}})])
    monkeypatch.setattr(evidence.requests, "get", lambda *a, **k: next(answers))
    monkeypatch.setattr("time.sleep", lambda s: naps.append(s))
    assert evidence._api({"action": "query"})["query"]["search"][0]["title"] == "Apollo 11"
    assert sum(n >= 2 for n in naps) == 2  # each 429 waited out its Retry-After


def test_wikipedia_gives_up_after_bounded_tries(monkeypatch):
    from pipeline import evidence

    class R:
        status_code, headers = 429, {}
    monkeypatch.setattr(evidence.requests, "get", lambda *a, **k: R())
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(RuntimeError):
        evidence._api({"action": "query"})


def test_nasa_image_must_name_the_subject_in_its_title_not_just_its_description():
    iss = {"title": "ISS Solar Array Wing", "description": "Astronauts can see the Moon from the station",
           "tags": "ISS, solar array"}
    assert not visuals.names_subject("Moon", iss)
    assert visuals.names_subject("Moon", {"title": "Full Moon Rises", "description": "", "tags": ""})
    assert visuals.names_subject("Moon", {"title": "Lunar view", "description": "", "tags": "Moon, Apollo"})


def test_research_keeps_the_rare_sentence_the_story_needs(cfg, monkeypatch):
    # 4 Nov: 600 generic "Apollo/Moon/landing" lines crowded out the one about the 1201/1202 program alarms
    from pipeline import storygen, evidence
    generic = [f"Apollo 11 was the Moon landing mission number {i} in this list of landing facts." for i in range(80)]
    key = "The LM guidance computer flashed unexpected 1201 and 1202 program alarms during descent."
    monkeypatch.setattr(evidence, "reference", lambda q, pages=2: [("Apollo 11", "u", generic[:40] + [key] + generic[40:])])
    b = storygen.research({"topic": "The 1969 Apollo 11 moon landing almost aborted due to 1201 computer alarm codes"},
                          cfg, limit=1200)
    assert key in b["notes"]


def test_topic_designator_is_a_name_but_not_a_different_mission_number():
    # 4 Nov: "Apollo 13" in the title of an Apollo 13 story (narration says "Odyssey") read as an unchecked 13
    from conftest import make_script
    from pipeline import verify
    s = make_script("The 1970 Apollo 13 explosion crippled by a single faulty thermostat wire")
    s["scenes"] = [{"text": "Odyssey's oxygen tank burst two days out."}, {"text": "The crew came home safely."}]
    s["youtube"] = {"title": "Apollo 13: The Wire That Nearly Killed Them"}
    assert not [g for g in verify.coverage(s, []) if g["scene"] == "title"]
    assert not verify.title_issues(s)
    s["youtube"] = {"title": "Apollo 14: The Wire That Nearly Killed Them"}
    assert verify.title_issues(s)  # the topic vouches only for its own designator


def test_verify_checks_against_the_articles_research_used_not_a_search_for_the_topic_sentence(monkeypatch):
    # 4 Nov: verify searched the whole topic and got "History of British Airways"; every Flight 9 fact was unsupported
    from pipeline import verify
    asked = []
    flight = [("British Airways Flight 009", "u", ["On 24 June 1982 the 747 flew into ash from Mount Galunggung."])]
    monkeypatch.setattr(evidence, "reference", lambda q, pages=2: asked.append(q) or
                        (flight if q == "British Airways Flight 009" else [("History of British Airways", "h", [])]))
    s = {"topic": "The 1981 British Airways Flight 9 flying through a volcanic ash cloud that stopped all four engines",
         "research": {"sources": [{"title": "British Airways Flight 009", "url": "u"}]}}
    assert verify._documents(s) == flight and asked == ["British Airways Flight 009"]
    del s["research"]
    verify._documents(s)
    assert asked[1] == "British Airways Flight 9"  # no research: the topic's names, never the whole sentence first


def test_accented_names_match_their_plain_spelling():
    # 5 Nov: "Möhne" normalised to "m hne", so the Dambusters dam claim found no document
    docs = [("Operation Chastise", "u", ["The Möhne and Edersee dams were breached."])]
    assert evidence.passage(["Mohne", "Edersee"], docs)[0] == "direct"
    assert evidence.passage(["Möhne", "breached"], docs)[0] == "direct"


def test_space_writer_writes_from_research_notes_when_enabled(cfg, monkeypatch):
    # 5 Nov: the space writer wrote from memory, so true lines failed verify as "no document evidence"
    from pipeline import scriptgen, storygen
    brief = {"sources": [{"title": "Mars Orbiter Mission", "url": "u"}],
             "notes": ["MOM has been orbiting Mars since 24 September 2014."]}
    monkeypatch.setattr(storygen, "research", lambda topic, cfg: brief)
    monkeypatch.setattr(storygen, "polish", lambda s, cfg: s)
    monkeypatch.setattr(scriptgen, "fact_check", lambda s, cfg: s)
    prompts = []
    scene = {"text": " ".join(["word"] * 20), "visual": "mars", "nasa": "mars", "subject": "Mars"}
    monkeypatch.setattr(scriptgen, "ask_json", lambda prompt, cfg, **k: prompts.append(prompt) or
                        {"topic": "t", "scenes": [dict(scene) for _ in range(6)], "youtube": {"title": "t"}})
    cfg.setdefault("writer", {})["research"] = True
    s = scriptgen.write_script({"topic": "Mangalyaan Mars Mission Budget"}, cfg)
    assert "orbiting Mars since 24 September 2014" in prompts[0] and s["research"] == brief


def test_ledger_values_written_as_text_still_count_as_checked_numbers():
    # 5 Nov: "$73 million" in the ledger read as no number, so the narration's 73 million was "not checked"
    from conftest import make_script
    from pipeline import verify
    assert verify._num("$73 million") == 73e6 and verify._num("0.280-inch") == 0.28
    assert verify._num("₹450 crore (US$73 million)") is None  # two numbers: never guessed
    s = make_script("Mangalyaan")
    s["scenes"] = [{"text": "The mission cost about 73 million US dollars."}]
    s["youtube"] = {"title": "Mars on a budget"}
    claim = {"scene": 1, "claim": "cost", "narrated": "$73 million", "independent": "US$73 million"}
    assert not [g for g in verify.coverage(s, [claim]) if g["scene"] == "1"]


def test_a_dated_ledger_entry_covers_its_year():
    from conftest import make_script
    from pipeline import verify
    s = make_script("Mangalyaan")
    s["scenes"] = [{"text": "On November 5th, 2013, India launched its first interplanetary spacecraft."}]
    s["youtube"] = {"title": "Mars on a budget"}
    claim = {"scene": 1, "claim": "launch date", "narrated": "November 5th, 2013", "independent": "5 November 2013"}
    assert not [g for g in verify.coverage(s, [claim]) if g["scene"] == "1"]


def test_a_year_the_source_states_is_checked_even_when_the_ledger_forgot_it():
    from conftest import make_script
    from pipeline import verify
    s = make_script("Mangalyaan")
    s["scenes"] = [{"text": "In November 2013, India launched the Mars Orbiter Mission."}]
    s["youtube"] = {"title": "Mars on a budget"}
    docs = [("Mars Orbiter Mission", "u", ["The Mars Orbiter Mission was launched by India in November 2013."])]
    assert [g for g in verify.coverage(s, []) if g["scene"] == "1"]              # no ledger, no source: flagged
    assert not [g for g in verify.coverage(s, [], docs) if g["scene"] == "1"]  # the article states it
