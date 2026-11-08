"""Every test runs against a temporary data/output folder and never touches the real queue or YouTube."""
import copy
import datetime as dt
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pipeline import (analytics, channels, control, evidence, finance, ideas, jobs, llm, orchestrator, runs,  # noqa: E402
                      publish_instagram, publish_youtube, qa, util, visuals)
from pipeline.util import load_config  # noqa: E402

TEST_CHANNEL = "UC_TEST_COSMIC"


@pytest.fixture
def cfg():
    c = copy.deepcopy(load_config())
    c["review"]["human_approval"] = False  # tests opt in explicitly
    c["schedule"]["slots_ist"] = ["09:00", "15:00", "20:00"]  # scheduling tests use fixed slots, not the live ones
    c["schedule"]["videos_per_day"] = 3
    c["gemini"]["api_key_env"] = "GEMINI_API_KEY"  # the fakes set this one; the live profile's variable may differ
    return c


@pytest.fixture(autouse=True)
def sandbox(tmp_path, monkeypatch):
    data, out = tmp_path / "data", tmp_path / "output"
    data.mkdir()
    out.mkdir()
    monkeypatch.delenv("NTFY_TOPIC", raising=False)  # tests never send real phone pushes
    monkeypatch.setattr(jobs, "HISTORY", data / "history.json")
    monkeypatch.setattr(jobs, "OUTPUT", out)
    monkeypatch.setattr(orchestrator, "OUTPUT", out)
    monkeypatch.setattr(qa, "OUTPUT", out)
    monkeypatch.setattr(ideas, "BACKLOG", data / "topics.json")
    monkeypatch.setattr(ideas, "HISTORY", data / "history.json")
    monkeypatch.setattr(control, "STOP_FILE", tmp_path / "STOP")
    monkeypatch.setattr(control, "USAGE", data / "usage.csv")
    monkeypatch.setattr(control, "DRY_RUN", False)
    monkeypatch.setattr(control, "_counts", {k: 0 for k in control._counts})
    monkeypatch.setattr(finance, "LEDGER", data / "finance" / "ledger.csv")
    from pipeline.business import store as business_store
    monkeypatch.setattr(business_store, "BASE", data / "business")  # owner money never touches the real store
    monkeypatch.setattr(analytics, "ADIR", data / "analytics")
    monkeypatch.setattr(analytics, "REPORTS", tmp_path / "reports")
    monkeypatch.setattr(visuals, "USED", data / "used_media.json")
    monkeypatch.setattr(visuals, "VISION_CACHE", data / "vision_cache.json")
    # everything channels.activate() rebinds, so a test that activates a channel can't leak into the next one
    monkeypatch.setattr(jobs, "TZ", jobs.IST)
    monkeypatch.setattr(control, "CHANNEL_STOP", None)
    monkeypatch.setattr(analytics, "ANALYTICS_TOKEN", tmp_path / "analytics_token.json")
    monkeypatch.setattr(publish_youtube, "TOKEN", tmp_path / "youtube_token.json")
    monkeypatch.setattr(publish_youtube, "TOKEN_ENV", "YOUTUBE_TOKEN_JSON_TEST")
    monkeypatch.setattr(publish_youtube, "CLIENT_SECRET", tmp_path / "client_secret.json")
    monkeypatch.setattr(publish_youtube, "EXPECTED_CHANNEL", TEST_CHANNEL)
    monkeypatch.setattr(publish_instagram, "USER_ENV", "IG_USER_ID_TEST")
    monkeypatch.setattr(publish_instagram, "TOKEN_ENV", "IG_ACCESS_TOKEN_TEST")
    monkeypatch.setattr(util, "LOG_TAG", "test")
    monkeypatch.setattr(evidence, "reference", lambda query, pages=2: [])  # tests never call Wikipedia
    monkeypatch.setattr(evidence, "fetch", lambda url, max_bytes=0: None)  # ...or any proposed source page
    monkeypatch.setattr(evidence, "dead_link", lambda url: False)          # ...or probe description links
    monkeypatch.setattr(llm, "USAGE_FILE", data / "llm_usage.json")
    monkeypatch.setattr(runs, "online", lambda *a, **k: True)             # ...or probe the network
    monkeypatch.setattr(util, "EVENTS", data / "events.jsonl")
    monkeypatch.setattr(channels, "ACTIVE", None)
    monkeypatch.setattr(orchestrator, "_DRY_RESERVED", [])  # per-process rehearsal state
    monkeypatch.setattr(orchestrator, "_DRY_TOPICS", [])
    from pipeline import channel
    monkeypatch.setattr(channel, "_VERIFIED", set())
    # Gemini quota memory is shared across channels (it belongs to the key), so it must never be the real file
    monkeypatch.setattr(llm, "QUOTA_FILE", data / "gemini_quota.json")
    monkeypatch.setattr(llm, "EXHAUSTED", set())
    monkeypatch.setattr(llm, "_loaded", set())
    monkeypatch.setattr(llm, "TRIPPED", set())   # circuit-breaker state is per run (process)
    monkeypatch.setattr(llm, "_FAILS", {})
    from pipeline import orchestrator as _orch
    monkeypatch.setattr(_orch, "_RUN", {"drafts_off": False})  # per-run: a draft that deferred is skipped
    for k in ("GROQ_API_KEY", "OPENROUTER_API_KEY", "OLLAMA_API_KEY", "NVIDIA_API_KEY", "NVIDIA_API_KEY_2", "NVIDIA_API_KEY_3"):  # tests never reach a real fallback provider
        monkeypatch.delenv(k, raising=False)
    return tmp_path


@pytest.fixture
def fixed_now(monkeypatch):
    """Freeze the clock at Sat 24 Oct 2026 10:00 IST."""
    t = dt.datetime(2026, 10, 24, 10, 0, tzinfo=jobs.IST)
    monkeypatch.setattr(jobs, "now", lambda: t)
    return t


def make_script(topic="Test topic", text_seed="alpha"):
    scenes = [{"text": f"{text_seed} scene number {i} says something different about {topic} and the stars {i * 7}.",
               "visual": "galaxy", "nasa": "galaxy"} for i in range(8)]
    return {"topic": topic, "scenes": scenes,
            "youtube": {"title": f"{topic} explained", "description": "Desc.\n\nMade with AI: script, voice and "
                        "visuals are AI-assisted. Facts checked against NASA/ESA sources.\n\n#Shorts #Space #Science", "tags": ["space"],
                        "pinned_comment": "What do you think?"},
            "instagram": {"caption": "c", "hashtags": ["#space"]}, "sources": ["NASA"], "music": "drift-am.m4a"}


def add_job(folder, status="ready", script=None, complete=True, **fields):
    d = orchestrator.OUTPUT / folder
    d.mkdir(parents=True, exist_ok=True)
    (d / "video.mp4").write_bytes(b"fake")
    script = script or make_script(folder)
    script = gate_passed(script) if complete else script  # complete=False: keep a deliberately broken record
    from pipeline import verify
    if "claims_for" not in script:  # a queued job passed the claim check (publish refuses unverified text)
        script = {**script, "claims_for": verify.fingerprint(script)}
    (d / "script.json").write_text(json.dumps(script))
    return jobs.upsert({"folder": folder, "topic": folder, "status": status, "created": folder, **fields})


def gate_passed(script):
    """What a job that passed every stage has recorded (the release gate reads these): a claim ledger, QA and review
    results, per-scene provenance and visual grounding. Fields a test sets itself are kept."""
    import copy
    s = copy.deepcopy(script)
    s.setdefault("claims", [{"scene": 1, "claim": "c", "status": "VERIFIED", "decision": "pass"}])
    s.setdefault("qa", {"pass": True, "checks": [{"name": "duration", "ok": True, "detail": ""}]})
    s.setdefault("review", {"pass": True, "scores": {}, "summary": "ok"})
    for sc in s.get("scenes", []):
        sc.setdefault("rights", {"source": "stars", "license": "Generated by this pipeline (original)", "asset_id": None})
    s.setdefault("visual_grounding", [{"scene": i, "narration": sc.get("text", ""), "decision": "CONTEXTUAL",
                                       "deterministic": "CONTEXTUAL", "reasons": [], "asset": {"source": "stars"}}
                                      for i, sc in enumerate(s.get("scenes", []))])
    s["youtube"].setdefault("tags", ["space"])
    return s
