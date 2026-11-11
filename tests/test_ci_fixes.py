"""GitHub Actions fixes (11 Nov): Gemini gets another try after a cooldown, Mac-only models are skipped on CI."""
import json

import pytest

from pipeline import llm


def _cfg(mins=30):
    return {"gemini": {"reopen_after_minutes": mins}}


def test_benched_gemini_reopens_after_cooldown(monkeypatch):
    llm.TRIPPED.clear()
    llm.TRIPPED.update({"gemini", "gemini:a"})
    monkeypatch.setattr(llm, "_GEMINI_LAST_TRIP", 1000.0)
    monkeypatch.setattr(llm.time, "monotonic", lambda: 1000.0 + 30 * 60)
    llm._maybe_reopen_gemini(_cfg())
    assert not llm.TRIPPED


def test_benched_gemini_stays_benched_during_cooldown(monkeypatch):
    llm.TRIPPED.clear()
    llm.TRIPPED.update({"gemini", "gemini:a"})
    monkeypatch.setattr(llm, "_GEMINI_LAST_TRIP", 1000.0)
    monkeypatch.setattr(llm.time, "monotonic", lambda: 1000.0 + 29 * 60)
    llm._maybe_reopen_gemini(_cfg())
    assert "gemini" in llm.TRIPPED
    llm.TRIPPED.clear()


def test_no_reopen_when_not_configured(monkeypatch):
    llm.TRIPPED.clear()
    llm.TRIPPED.add("gemini:a")
    monkeypatch.setattr(llm, "_GEMINI_LAST_TRIP", 0.0)
    monkeypatch.setattr(llm.time, "monotonic", lambda: 10.0 ** 6)
    llm._maybe_reopen_gemini({"gemini": {}})
    assert "gemini:a" in llm.TRIPPED
    llm.TRIPPED.clear()


def test_localhost_provider_skipped_only_on_ci(monkeypatch):
    cfg = {"fallback": {"providers": [{"name": "ollama", "base_url": "http://localhost:11434/v1", "key_env": "K",
                                       "models": ["q"]},
                                      {"name": "groq", "base_url": "https://api.groq.com", "key_env": "K",
                                       "models": ["g"]}]}}
    monkeypatch.setenv("K", "x")
    monkeypatch.setattr(llm, "usage_today", lambda: {})
    monkeypatch.delenv("CI", raising=False)
    assert [p["name"] for p, _ in llm.alt_models(cfg)] == ["ollama", "groq"]
    monkeypatch.setenv("CI", "true")
    assert [p["name"] for p, _ in llm.alt_models(cfg)] == ["groq"]


# --- 11 Nov: a remake must not draw the pictures the reviewer (or the black-frame check) just rejected ---

def _done():
    return {"scenes": [{"text": "a", "rights": {"source": "nasa", "asset_id": "nasa:PIA1"}},
                       {"text": "b", "rights": {"source": "pixabay", "asset_id": "pixabay:77"}},
                       {"text": "c", "rights": {"source": "motion", "asset_id": None}},
                       {"text": "d", "rights": {"source": "nasa", "asset_id": "nasa:PIA9"}}],
            "visual_grounding": [{"scene": 0, "decision": "MISMATCH"}, {"scene": 1, "decision": "MISMATCH"},
                                 {"scene": 3, "decision": "CONTEXTUAL"}],
            "black_scenes": [2]}


def test_remake_remembers_rejected_assets_and_sources():
    from pipeline import orchestrator
    done = _done()
    script = orchestrator._avoid_rejected(orchestrator._reusable(done), done)
    sc = script["scenes"]
    assert sc[0]["avoid"] == {"assets": ["nasa:PIA1"], "sources": []}
    assert sc[1]["avoid"] == {"assets": ["pixabay:77"], "sources": ["pexels", "pixabay"]}
    assert sc[2]["avoid"]["sources"] == ["motion"]
    assert "avoid" not in sc[3]
    again = orchestrator._avoid_rejected(orchestrator._reusable({**done, "scenes": script["scenes"]}), done)
    assert again["scenes"][0]["avoid"]["assets"] == ["nasa:PIA1"]  # kept across remakes, no duplicates


def test_avoid_does_not_change_the_verified_fingerprint():
    from pipeline import verify
    base = {"scenes": [{"text": "The station orbits Earth."}], "youtube": {"title": "T", "description": "D"}}
    marked = {"scenes": [{"text": "The station orbits Earth.", "avoid": {"assets": ["x"], "sources": ["pixabay"]}}],
              "youtube": {"title": "T", "description": "D"}}
    assert verify.fingerprint(base) == verify.fingerprint(marked)


def test_black_scenes_maps_blank_runs_to_scenes(monkeypatch):
    from pipeline import qa
    monkeypatch.setattr(qa, "_blank_runs", lambda p: [(5.2, 6.0)])
    tl = [{"start": 0, "duration": 5}, {"start": 5, "duration": 4}, {"start": 9, "duration": 3}]
    assert qa.black_scenes("base.mp4", tl) == [1]


def test_avoided_scene_skips_stock_and_gets_a_main_subject_image(monkeypatch, tmp_path):
    import yaml
    from pipeline import visuals
    cfg = yaml.safe_load(open("config.yaml"))
    cfg["video"]["sources"] = ["nasa", "pixabay", "stars"]
    monkeypatch.setattr(visuals, "USED", tmp_path / "used.json")
    asked = []

    def nasa(q, work, i, used, cfg, check=None):
        asked.append(q)
        assert "nasa:OLD" in used  # the rejected image can't be drawn again
        if q != "international space station":
            return None
        used.append("nasa:NEW")
        visuals.META[i] = {"title": "International Space Station", "page": "https://images.nasa.gov/details/NEW"}
        p = tmp_path / "s.jpg"
        p.write_bytes(b"x")
        return p

    monkeypatch.setattr(visuals, "nasa_image", nasa)
    monkeypatch.setattr(visuals, "pixabay_video", lambda *a, **k: pytest.fail("stock must be skipped"))
    scene = {"text": "It is bigger than a six-bedroom house.", "subject": "modern house", "visual": "modern house",
             "nasa": "house", "commons_subject": "international space station",
             "avoid": {"assets": ["nasa:OLD"], "sources": ["pexels", "pixabay"]}}
    tl, _ = visuals.gather([scene], cfg, tmp_path, "iss")
    assert tl[0]["rights"]["source"] == "nasa" and tl[0]["rights"]["asset_id"] == "nasa:NEW"
    assert "international space station" in asked
    saved = json.loads((tmp_path / "used.json").read_text())
    assert "nasa:NEW" in saved and "nasa:OLD" not in saved  # the ban lasts for this remake only
