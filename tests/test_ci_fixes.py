"""GitHub Actions fixes (11 Nov): Gemini gets another try after a cooldown, Mac-only models are skipped on CI."""
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
