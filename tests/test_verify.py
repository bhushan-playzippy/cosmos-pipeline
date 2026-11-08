"""Independent claim check: the verdict on numbers is Python arithmetic, not the model's word."""
import re

import pytest

from pipeline import jobs, orchestrator, verify
from tests.conftest import make_script


def claim(**k):
    return {"scene": 1, "claim": "c", "verdict": "supported", "confidence": "high", "source": "NASA fact sheet", **k}


def plain_script(topic="t"):
    """make_script without digits: these tests are about the verdict logic, not number coverage."""
    s = make_script(topic)
    for sc in s["scenes"]:
        sc["text"] = re.sub(r"\d+", "many", sc["text"])
    return s


def test_wrong_number_fails_even_when_the_model_says_supported(cfg):
    # the real withdrawn case: Mangalyaan "400 million km", ISRO says ~666 million
    bad = verify.problems([claim(narrated=400e6, independent=666e6, unit="km")], cfg)
    assert len(bad) == 1 and "400,000,000" in bad[0]["why"]


def test_honest_rounding_passes(cfg):
    assert verify.problems([claim(narrated=74e6, independent=73e6, unit="USD"),
                            claim(narrated=None, independent=None)], cfg) == []


@pytest.mark.parametrize("verdict", ["wrong", "misleading", "disputed", "unverifiable"])
def test_non_supported_verdicts_fail(cfg, verdict):
    assert verify.problems([claim(verdict=verdict)], cfg)


def test_repair_then_recheck(cfg, monkeypatch):
    answers = iter([{"claims": [claim(narrated=400, independent=666, unit="km")]},
                    {"script": {**plain_script(), "topic": "t"}},
                    {"claims": [claim(narrated=666, independent=666, unit="km")]}])
    purposes = []
    monkeypatch.setattr(verify, "ask_json", lambda p, c, **k: purposes.append(k["purpose"]) or next(answers))
    out = verify.check(plain_script(), cfg)
    assert purposes == ["verify", "verify_repair", "verify"] and out["claims"][0]["narrated"] == 666


def test_still_wrong_after_repair_fails_the_video(cfg, monkeypatch, fixed_now):
    from pipeline import scriptgen, youtuber
    wrong = {"claims": [claim(narrated=400, independent=666, unit="km")]}
    monkeypatch.setattr(verify, "ask_json", lambda p, c, **k: {"script": make_script("t")}
                        if k["purpose"] == "verify_repair" else wrong)
    monkeypatch.setattr(scriptgen, "write_script", lambda t, c: make_script(t["topic"]))
    monkeypatch.setattr(youtuber, "package", lambda s, c: s)
    job = orchestrator.make_video(cfg, topic="Bad numbers")
    assert job["status"] == "failed" and job["stage_failed"] == "verify"
    assert "independent check still failing" in job["reason"]
    assert jobs.find(jobs.load(), job["folder"])["status"] not in jobs.LIVE


def test_verifier_calls_are_strong(cfg, monkeypatch):
    seen = []
    monkeypatch.setattr(verify, "ask_json", lambda p, c, **k: seen.append(k) or {"claims": [claim()]})
    verify.check(plain_script(), cfg)
    assert seen and all(k["strong"] for k in seen)


def test_missing_sources_come_from_the_independent_check(cfg, monkeypatch):
    script = {**plain_script(), "sources": []}
    cfg["verify"]["max_unresolved"] = 5  # this test is about the sources list, not the unresolved-claim allowance
    monkeypatch.setattr(verify, "ask_json", lambda p, c, **k: {"claims": [
        claim(source="NASA Moon Fact Sheet"), claim(source="NASA Moon Fact Sheet"), claim(source="ESA Moon page")]})
    assert verify.check(script, cfg)["sources"] == ["NASA Moon Fact Sheet", "ESA Moon page"]
    # a low-confidence claim no longer passes quietly with its source left out: it fails as UNSUPPORTED
    assert verify.classify([claim(source="Blog", confidence="low")], plain_script(), cfg)[0]["status"] == "UNSUPPORTED"
