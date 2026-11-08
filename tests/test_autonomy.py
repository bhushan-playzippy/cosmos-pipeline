"""Autonomous operation (2 Nov): model routing that can always finish an independent re-check with 2 families,
lean repair requests, exact deferral causes, provider circuit breakers, release gates, health alerts, disk
retention and analytics v2. Sandbox only: no real API, upload or file outside tmp."""
import json

import pytest

from conftest import add_job, make_script
from pipeline import llm, verify


class FakeModels:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        r = self.behaviour(model)
        if isinstance(r, Exception):
            raise r
        return type("R", (), {"text": r, "usage_metadata": None})()


class FakeGroq:
    def __init__(self, behaviour):
        self.behaviour, self.calls = behaviour, []

    def __call__(self, url, json=None, timeout=None, headers=None):
        self.calls.append((json["model"], json))
        r = self.behaviour(json["model"], json)
        if isinstance(r, Exception):
            raise r
        status, text = r
        body = {"choices": [{"message": {"content": text}}], "usage": {"total_tokens": 10}}
        return type("Resp", (), {"status_code": status, "text": text, "json": lambda self: body})()


@pytest.fixture
def providers(monkeypatch):
    def install(gemini, groq):
        g = FakeModels(gemini)
        monkeypatch.setattr("google.genai.Client", lambda **k: type("C", (), {"models": g})())
        monkeypatch.setenv("GEMINI_API_KEY", "test")
        monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
        monkeypatch.setattr("time.sleep", lambda s: None)
        q = FakeGroq(groq)
        monkeypatch.setattr(llm.requests, "post", q)
        return g, q
    return install


# ---------------------------------------------------------------- R1: authoring in one family

def test_fact_check_and_repair_stay_in_the_writers_family(cfg, providers):
    g, q = providers(lambda m: '{"ok": 1}', lambda m, b: (200, '{"ok": 2}'))
    llm.ask_json("p", cfg, strong=True, purpose="fact_check", prefer="gemini-flash-latest", prefer_family="gemini")
    assert g.calls == ["gemini-flash-latest"] and q.calls == []
    llm.ask_json("p", cfg, strong=True, purpose="verify_repair", prefer="groq:openai/gpt-oss-120b",
                 prefer_family="openai")
    assert [c[0] for c in q.calls] == ["openai/gpt-oss-120b"]


def test_per_minute_limit_waits_instead_of_leaving_the_writers_family(cfg, providers, monkeypatch):
    """3 Nov cosmic: gpt-oss wrote, hit its per-minute limit ("try again in 45s") at fact_check, and the call moved
    on to nemotron. Authoring then spanned 2 families, verify had only Gemini left (tripped) → certain deferral.
    A per-minute limit is now waited out when the next model would be another family."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "or_test")
    n = {"c": 0}

    def groq(model, body):
        if model.startswith("openai/"):
            n["c"] += 1
            return (429, "Rate limit reached (TPM). Please try again in 45s") if n["c"] == 1 else (200, '{"ok": 3}')
        return 200, '{"ok": "other family"}'
    g, q = providers(lambda m: RuntimeError("503 UNAVAILABLE"), groq)
    out = llm.ask_json("p", cfg, strong=True, purpose="fact_check", prefer="groq:openai/gpt-oss-120b",
                       prefer_family="openai")
    assert out["ok"] == 3 and [c[0] for c in q.calls] == ["openai/gpt-oss-120b"] * 2
    # without a family to keep, a long per-minute wait still moves on to the next model (unchanged)
    n["c"] = 0
    q.calls.clear()
    llm.ask_json("p", cfg, strong=True, purpose="verify", avoid=("gemini-flash-latest",))
    assert len({c[0] for c in q.calls}) == 2


def test_clips_deadlock_case_now_finishes_with_two_families(cfg, providers, monkeypatch):
    """2 Nov clips: gemini wrote, openai fact-checked, qwen verified + repaired → no family left to re-check. Now
    the repair goes to the writer's family and the re-check to a family that didn't touch the text."""
    s = make_script("Apollo 13")
    s["provenance"] = {"write": "gemini-flash-latest", "fact_check": "gemini-flash-latest"}
    g, q = providers(lambda m: json.dumps({"scenes": [{"text": x["text"]} for x in s["scenes"]],
                                           "youtube": {"title": "Fixed title"}}),
                     lambda m, b: (200, '{"claims": [{"scene": 1, "claim": "c", "verdict": "supported"}]}'))
    fixed = verify._repair(s, [{"scene": 1, "claim": "c", "why": "wrong"}], cfg)
    assert fixed["provenance"]["verify_repair"].startswith("gemini")
    excluded = llm.families(verify._avoid(fixed), cfg)
    assert excluded == {"gemini"}  # openai and qwen are both still free to re-check
    verify._claims(fixed, cfg)
    assert llm.family(llm.LAST_MODEL) in ("openai", "qwen")


def test_repair_request_carries_only_the_visible_text(cfg, providers):
    s = make_script("t")
    s["fact_checks"] = [{"claim": "x" * 5000, "working": "y" * 5000}]
    s["sources"], s["provenance"] = ["S" * 3000], {"write": "groq:openai/gpt-oss-120b"}
    sent = {}

    def groq(m, body):
        sent["prompt"] = body["messages"][-1]["content"]
        return 200, json.dumps({"scenes": [{"text": x["text"]} for x in s["scenes"]]})
    providers(lambda m: '{}', groq)
    out = verify._repair(s, [{"scene": 1, "claim": "c", "why": "wrong"}], cfg)
    assert "x" * 100 not in sent["prompt"] and "S" * 100 not in sent["prompt"] and len(sent["prompt"]) < 6000
    assert out["sources"] == s["sources"] and out["fact_checks"] == s["fact_checks"]  # merged back untouched


def test_repair_that_drops_scenes_is_refused(cfg, providers):
    s = make_script("t")
    s["provenance"] = {"write": "gemini-flash-latest"}
    providers(lambda m: json.dumps({"scenes": [{"text": "only one"}]}), lambda m, b: (200, "{}"))
    with pytest.raises(ValueError, match="different number of scenes"):
        verify._repair(s, [{"scene": 1, "claim": "c", "why": "w"}], cfg)


# ---------------------------------------------------------------- R3: exact causes

def test_deferral_names_independence_when_no_family_is_left(cfg, providers):
    providers(lambda m: '{}', lambda m, b: (200, "{}"))
    with pytest.raises(llm.Deferred) as e:
        llm.ask_json("p", cfg, strong=True, purpose="verify",
                     avoid={"gemini-flash-latest", "groq:openai/gpt-oss-120b", "groq:qwen/qwen3.8-27b"})
    assert e.value.cause == "independence" and "independent model family" in str(e.value)


def test_deferral_names_overload_and_request_size(cfg, providers):
    for prov in cfg["fallback"]["providers"]:  # this test is about combining causes, not the output cap
        prov.pop("output_limits", None)
    providers(lambda m: RuntimeError("503 UNAVAILABLE high demand"),
              lambda m, b: (413, "Request too large for model: tokens per minute"))
    with pytest.raises(llm.Deferred) as e:
        llm.ask_json("p", cfg, strong=True, purpose="verify_repair", avoid={"groq:openai/gpt-oss-120b"})
    assert e.value.cause == "overload+request_size"


# ---------------------------------------------------------------- R4: circuit breaker

def test_provider_with_repeated_timeouts_is_skipped_for_the_run(cfg, providers):
    import requests
    g, q = providers(lambda m: '{"g": 1}', lambda m, b: requests.Timeout("Read timed out"))
    assert llm.ask_json("p", cfg, strong=True, purpose="verify") == {"g": 1}  # fell back to Gemini
    tripped_after = len(q.calls)
    assert "groq" in llm.TRIPPED and tripped_after == 2
    llm.ask_json("p", cfg, strong=True, purpose="verify")
    assert len(q.calls) == tripped_after  # not tried again this run


def test_gemini_breaker_defers_fast_and_the_reviewer_wait_reopens_it(cfg, providers):
    g, q = providers(lambda m: RuntimeError("503 UNAVAILABLE"), lambda m, b: (200, '{"x": 1}'))
    with pytest.raises(llm.Deferred):
        llm.ask_json("p", cfg, strong=True, purpose="write", avoid={"groq:openai/gpt-oss-120b", "groq:qwen/qwen3.8-27b"})
    assert "gemini" in llm.TRIPPED
    n = len(g.calls)
    with pytest.raises(llm.Deferred) as e:
        llm.ask_json("p", cfg, strong=True, purpose="write", avoid={"groq:openai/gpt-oss-120b", "groq:qwen/qwen3.8-27b"})
    assert len(g.calls) == n and "overload" in e.value.cause


def test_success_resets_the_failure_count(cfg, providers):
    import requests
    seq = iter([requests.Timeout("timed out"), (200, '{"a": 1}'), requests.Timeout("timed out"), (200, '{"a": 2}')])

    def groq(m, b):
        r = next(seq)
        if isinstance(r, Exception):
            raise r
        return r
    providers(lambda m: '{}', groq)
    llm.ask_json("p", cfg, strong=True, purpose="verify")
    llm.ask_json("p", cfg, strong=True, purpose="verify")
    assert "groq" not in llm.TRIPPED


def test_backoff_jitter_is_bounded():
    vals = [llm._jitter(20) for _ in range(200)]
    assert all(20 <= v <= 25 for v in vals) and len(set(vals)) > 1


# ---------------------------------------------------------------- release gates

from pipeline import control, jobs, orchestrator, release  # noqa: E402
from conftest import gate_passed  # noqa: E402


def released(cfg, folder):
    j = jobs.find(jobs.load(), folder)
    return release.check(j, jobs.script_of(j), cfg)


def test_a_video_that_passed_every_stage_is_released(cfg):
    add_job("good")
    ok, checks = released(cfg, "good")
    assert ok, release.summary(checks)


@pytest.mark.parametrize("breakage, gate", [
    (lambda s: s.update(qa={"pass": False, "checks": [{"name": "loudness", "ok": False, "detail": ""}]}), "qa"),
    (lambda s: s.pop("qa"), "qa"),                                   # a check that never ran blocks too
    (lambda s: s.update(review={"pass": False, "summary": "inaccurate"}), "review"),
    (lambda s: s.pop("visual_grounding"), "visuals"),                # e.g. made before grounding existed
    (lambda s: s["visual_grounding"][0].update(decision="MISMATCH"), "visuals"),
    (lambda s: s["scenes"][0].update(rights={"source": "commons", "license": "PD"}), "rights"),  # no asset id
    (lambda s: s["youtube"].update(description="no label"), "metadata"),
    (lambda s: s.update(claims=[{"claim": "x", "decision": "fail"}]), "claims"),
])
def test_any_failed_or_missing_gate_blocks_release(cfg, breakage, gate):
    s = gate_passed(make_script("t"))
    breakage(s)
    add_job("bad", script=s, complete=False)
    ok, checks = released(cfg, "bad")
    assert not ok and gate in [n for n, o, _ in checks if not o]


def test_edited_text_blocks_release(cfg):
    s = gate_passed(make_script("t"))
    add_job("edited", script=s)
    p = orchestrator.OUTPUT / "edited" / "script.json"
    d = json.loads(p.read_text())
    d["youtube"]["hook_text"] = "NEW CLAIM"
    p.write_text(json.dumps(d))
    ok, checks = released(cfg, "edited")
    assert not ok and "verified text" in [n for n, o, _ in checks if not o]


def test_duplicate_title_blocks_release(cfg):
    add_job("live", status="published", youtube_id="v1", title="Same Title")
    s = gate_passed(make_script("t"))
    s["youtube"]["title"] = "Same Title"
    add_job("twin", script=s)
    assert "duplicates" in [n for n, o, _ in released(cfg, "twin")[1] if not o]


def test_publish_quarantines_a_video_failing_a_gate_and_never_uploads(cfg, monkeypatch):
    s = gate_passed(make_script("t"))
    s.pop("visual_grounding")
    add_job("legacy", script=s, complete=False)
    cfg["youtube"]["enabled"] = True
    monkeypatch.setattr("pipeline.publish_youtube.upload", lambda *a, **k: pytest.fail("uploaded"))
    j = orchestrator.publish(cfg, "legacy")
    assert j["status"] == "quarantined" and "release gate" in j["reason"] and "visuals" in j["reason"]


def test_waiting_videos_get_an_automated_decision(cfg):
    cfg["review"]["human_approval"] = False
    add_job("w-good", status="awaiting_approval")
    s = gate_passed(make_script("t2"))
    s.pop("visual_grounding")
    add_job("w-legacy", status="awaiting_approval", script=s, complete=False)  # the Hunley case: no per-scene grounding
    orchestrator.release_waiting(cfg)
    st = {j["folder"]: j["status"] for j in jobs.load()}
    assert st == {"w-good": "ready", "w-legacy": "quarantined"}


def test_waiting_videos_untouched_in_a_dry_run_or_with_human_approval(cfg, monkeypatch):
    add_job("w", status="awaiting_approval")
    monkeypatch.setattr(control, "DRY_RUN", True)
    orchestrator.release_waiting(cfg)
    monkeypatch.setattr(control, "DRY_RUN", False)
    cfg["review"]["human_approval"] = True
    orchestrator.release_waiting(cfg)
    assert jobs.load()[0]["status"] == "awaiting_approval"


def test_production_records_the_release_decision(cfg, monkeypatch, fixed_now):
    from test_pipeline_units import _fake_production
    _fake_production(monkeypatch)
    from pipeline import verify as v
    monkeypatch.setattr("pipeline.verify.check", lambda s, c: {**s, "claims": [{"claim": "c", "decision": "pass"}],
                                                              "claims_for": v.fingerprint(s)})
    job = orchestrator.make_video(cfg, topic="Ok")
    rec = jobs.script_of(job)["release"]
    assert job["status"] == "ready" and rec["ok"] and {c["name"] for c in rec["checks"]} >= {"qa", "visuals", "rights"}


# ---------------------------------------------------------------- health alerts and retention

import datetime as _dt  # noqa: E402
from pipeline import health, util  # noqa: E402


def test_no_publication_and_quality_streak_alerts(cfg, fixed_now):
    for i in range(3):
        add_job(f"2026-10-2{i}_x", status="quarantined", reason="AI reviewer: bad", created=f"2026-10-2{i}T10:00:00")
    keys = {k for k, _ in health.checks(cfg)}
    assert {"no_publication", "quality"} <= keys
    add_job("2026-10-23_ok", status="scheduled", publish_at="2026-10-24T23:30:00+05:30", created="2026-10-23T10:00:00")
    assert "no_publication" not in {k for k, _ in health.checks(cfg)} and "quality" not in {k for k, _ in health.checks(cfg)}


def test_provider_upload_and_missed_run_alerts(cfg, fixed_now):
    for kind, extra in (("provider_tripped", {"provider": "groq"}), ("upload_failed", {"error": "HttpError 500"}),
                        ("missed_runs", {"runs": ["13:35"]})):
        util.event(kind, **extra)
    keys = {k for k, _ in health.checks(cfg)}
    assert {"provider", "upload", "missed_runs"} <= keys


def test_alerts_are_sent_once_a_day(cfg, fixed_now, monkeypatch):
    sent = []
    monkeypatch.setattr("pipeline.orchestrator.alert", lambda c, m: sent.append(m))
    health.notify(cfg)
    health.notify(cfg)
    assert len(sent) == len({m for m in sent}) and sent  # "no video ever published", once


def test_dry_run_sends_and_stores_no_alerts(cfg, fixed_now, monkeypatch):
    monkeypatch.setattr(control, "DRY_RUN", True)
    monkeypatch.setattr("pipeline.orchestrator.alert", lambda c, m: pytest.fail("alert sent in a dry run"))
    assert health.notify(cfg) and not (jobs.HISTORY.parent / "alerts.json").exists()


def test_stale_other_channel_is_reported(cfg, fixed_now, monkeypatch, tmp_path):
    from pipeline import channels
    other = tmp_path / "other"
    other.mkdir()
    (other / "runs.json").write_text(json.dumps({"last_ok": "2026-10-20T13:39:00+05:30"}))
    monkeypatch.setattr(channels, "available", lambda: ["cosmic", "clips"])
    real = channels.load
    monkeypatch.setattr(channels, "load", lambda cid=None: {**real(cid), "_paths": {"data": other}} if cid == "clips" else real(cid))
    assert "stale_clips" in {k for k, _ in health.checks(cfg)}


def test_retention_frees_only_videos_that_will_never_upload(cfg, fixed_now):
    old = "2026-09-01T10:00:00"
    for name, status in (("q", "quarantined"), ("p", "published"), ("r", "ready"), ("w", "awaiting_approval")):
        add_job(name, status=status, created=old)
        (orchestrator.OUTPUT / name / "post.txt").write_text("audit")
    add_job("new-q", status="quarantined", created="2026-10-23T10:00:00")
    listed, _ = health.prune(cfg, dry=True)
    assert all(not p.endswith(("script.json", "post.txt")) for p in listed)
    assert (orchestrator.OUTPUT / "q" / "video.mp4").exists()  # dry: listed, not deleted
    health.prune(cfg, dry=False)
    assert not (orchestrator.OUTPUT / "q" / "video.mp4").exists()
    assert (orchestrator.OUTPUT / "q" / "script.json").exists() and (orchestrator.OUTPUT / "q" / "post.txt").exists()
    for kept in ("p", "r", "w", "new-q"):
        assert (orchestrator.OUTPUT / kept / "video.mp4").exists()


# ---------------------------------------------------------------- analytics v2

from pipeline import analytics  # noqa: E402


class _Exec:
    def __init__(self, fn):
        self.fn = fn

    def execute(self):
        return self.fn()


class FakeAnalytics:
    def __init__(self, refuse_optional=False):
        self.refuse_optional, self.queries = refuse_optional, []

    def reports(self):
        return self

    def query(self, **kw):
        self.queries.append(kw)
        m = kw["metrics"].split(",")
        if kw.get("dimensions") == "video":
            if self.refuse_optional and "engagedViews" in m:
                return _Exec(lambda: (_ for _ in ()).throw(RuntimeError("400 Unknown identifier (engagedViews)")))
            cols = ["video"] + m
            vals = {"views": 1000, "averageViewPercentage": 61.5, "subscribersGained": 4, "engagedViews": 700}
            return _Exec(lambda: {"columnHeaders": [{"name": c} for c in cols],
                                  "rows": [["v1"] + [vals.get(x, 1) for x in m]]})
        if kw.get("dimensions") == "insightTrafficSourceType":
            return _Exec(lambda: {"rows": [["SHORTS", 900], ["YT_SEARCH", 100]]})
        return _Exec(lambda: {"rows": [[0.0, 1.1, 0.6], [0.5, 0.45, 0.5], [1.0, 0.2, 0.4]]})


class FakeData:
    def __init__(self, channel_id):
        self.cid = channel_id

    def channels(self):
        return self

    def list(self, **kw):
        return _Exec(lambda: {"items": [{"id": self.cid}]})


def analytics_env(monkeypatch, channel_id, **kw):
    from conftest import TEST_CHANNEL  # noqa: F401
    ya = FakeAnalytics(**kw)
    monkeypatch.setattr(analytics, "_clients", lambda: (ya, FakeData(channel_id)))
    add_job("pub", status="published", youtube_id="v1", publish_at="2026-10-20T05:30:00+05:30")
    return ya


def test_analytics_saves_metrics_traffic_and_retention_once_per_day(cfg, monkeypatch, fixed_now):
    from conftest import TEST_CHANNEL
    analytics_env(monkeypatch, TEST_CHANNEL)
    analytics.fetch_video_analytics(cfg)
    analytics.fetch_video_analytics(cfg)  # second pull the same day replaces, never doubles
    rows = analytics._read(analytics.ADIR / "video_analytics.csv")
    assert len(rows) == 1 and rows[0]["engagedViews"] == "700" and rows[0]["averageViewPercentage"] == "61.5"
    assert rows[0]["impressions"] == "" and rows[0]["viewedVsSwipedAway"] == ""  # unavailable = null, never 0
    assert len(analytics._read(analytics.ADIR / "traffic.csv")) == 2
    curve = json.loads((analytics.ADIR / "retention.json").read_text())["v1"]
    assert curve["below_half_at"] == 0.5 and len(curve["curve"]) == 3
    assert not analytics.analytics_due()


def test_analytics_refuses_another_channels_token(cfg, monkeypatch, fixed_now):
    analytics_env(monkeypatch, "UC_SOMEONE_ELSE")
    assert analytics.fetch_video_analytics(cfg) is None
    assert not (analytics.ADIR / "video_analytics.csv").exists()


def test_unknown_optional_metric_is_recorded_as_null(cfg, monkeypatch, fixed_now):
    from conftest import TEST_CHANNEL
    analytics_env(monkeypatch, TEST_CHANNEL, refuse_optional=True)
    analytics.fetch_video_analytics(cfg)
    row = analytics._read(analytics.ADIR / "video_analytics.csv")[0]
    assert row["engagedViews"] == "" and row["views"] == "1000"


def test_analytics_never_runs_in_a_dry_run(cfg, monkeypatch, fixed_now):
    monkeypatch.setattr(control, "DRY_RUN", True)
    monkeypatch.setattr(analytics, "_clients", lambda: pytest.fail("API called in a dry run"))
    assert analytics.fetch_video_analytics(cfg) is None


def test_snapshot_rows_are_upserted_not_appended(tmp_path):
    p = tmp_path / "c.csv"
    analytics._upsert(p, [{"date": "d1", "subscribers": 5}], ("date",))
    analytics._upsert(p, [{"date": "d1", "subscribers": 6}, {"date": "d2", "subscribers": 7}], ("date",))
    assert [(r["date"], r["subscribers"]) for r in analytics._read(p)] == [("d1", "6"), ("d2", "7")]


def test_analytics_files_follow_the_active_channel(cfg, monkeypatch):
    from pipeline import channels
    c = channels.activate(channels.load("clips"))
    assert "channels/clips" in str(analytics.ADIR)


def test_growth_uses_retention_and_subscriber_conversion(cfg):
    rows = [({"question_hook": True}, 70.0)] * 6 + [({"question_hook": False}, 40.0)] * 6
    from pipeline import growth
    fb = growth._feature_boost(rows, 55.0, 5)
    g = {"feature_boost": {}, "feature_boost_retention": fb, "feature_boost_subs": {}}
    c = growth._combined([g["feature_boost"], g["feature_boost_retention"], g["feature_boost_subs"]], "question_hook")
    assert c and c[0] > 1.0 > c[1] and c[2] == 1
    recs = growth._playbook({**g, "feature_boost": {"question_hook": {"n_with": 6, "n_without": 6}}}, 5)
    assert recs[0]["action"] == "repeat" and recs[0]["confidence"] == "low"


def test_retention_never_prunes_a_job_of_unknown_or_recent_age(cfg, fixed_now):
    """2 Nov live run: three jobs without a `created` stamp were treated as old and their videos deleted."""
    add_job("2026-10-20_1351_no-stamp", status="deleted")      # folder date: 4 days old
    add_job("no-date-at-all", status="quarantined")
    for name in ("2026-10-20_1351_no-stamp", "no-date-at-all"):
        orchestrator.jobs.mark(name, created=None)
    health.prune(cfg, dry=False)
    assert (orchestrator.OUTPUT / "2026-10-20_1351_no-stamp" / "video.mp4").exists()
    assert (orchestrator.OUTPUT / "no-date-at-all" / "video.mp4").exists()
    add_job("2026-09-01_1000_old", status="deleted")
    orchestrator.jobs.mark("2026-09-01_1000_old", created=None)
    health.prune(cfg, dry=False)
    assert not (orchestrator.OUTPUT / "2026-09-01_1000_old" / "video.mp4").exists()  # folder date says 7 weeks


def test_groq_requests_ask_only_for_a_completion_that_fits_the_token_limit(cfg, providers):
    """2 Nov: qwen refused a ~3k-token verify as "request too large" because no max_tokens was sent."""
    g, q = providers(lambda m: '{}', lambda m, b: (200, '{"ok": 1}'))
    llm.ask_json("x" * 9000, cfg, strong=True, purpose="verify")
    body = q.calls[0][1]
    assert 1024 <= body["max_tokens"] <= 8000 - 3000 and body["max_tokens"] + 3000 <= 8000
    llm.ask_json("short", cfg, strong=True, purpose="verify")
    assert q.calls[-1][1]["max_tokens"] == 4096


def test_a_rehearsal_gives_a_saved_draft_back_intact(cfg, monkeypatch, fixed_now):
    from test_pipeline_units import _fake_production
    from pipeline import ideas
    from pipeline.util import read_json, write_json
    _fake_production(monkeypatch)
    monkeypatch.setattr(control, "DRY_RUN", True)
    draft = make_script("Draft topic")
    write_json(ideas.BACKLOG, [{"topic": "Draft topic", "score": 30, "draft": draft, "draft_saved": "2026-10-24"}]
               + [{"topic": f"f{i}", "score": 1} for i in range(9)])
    orchestrator.produce(cfg, max_videos=1)
    back = next(t for t in read_json(ideas.BACKLOG, []) if t["topic"] == "Draft topic")
    assert back["draft"]["topic"] == "Draft topic" and back["draft_saved"] == "2026-10-24"


@pytest.mark.parametrize("via_produce", [True, False])
def test_a_deferred_rehearsal_gives_the_draft_back_intact(cfg, monkeypatch, fixed_now, via_produce):
    """2 Nov live: the deferral handler wrote the draft-less topic back first, so the intact copy was skipped."""
    from test_pipeline_units import _fake_production
    from pipeline import ideas
    from pipeline.util import read_json, write_json
    _fake_production(monkeypatch)
    monkeypatch.setattr(control, "DRY_RUN", True)
    monkeypatch.setattr("pipeline.verify.check", lambda s, c: (_ for _ in ()).throw(llm.Deferred("503", "overload")))
    write_json(ideas.BACKLOG, [{"topic": "Draft topic", "score": 30, "draft": make_script("Draft topic"),
                                "draft_saved": "2026-10-24"}] + [{"topic": f"f{i}", "score": 1} for i in range(9)])
    with pytest.raises(llm.Deferred):  # the daily runner catches it; produce's cleanup has run by then
        orchestrator.produce(cfg, max_videos=1) if via_produce else orchestrator.make_video(cfg)
    got = [t for t in read_json(ideas.BACKLOG, []) if t["topic"] == "Draft topic"]
    assert len(got) == 1 and got[0]["draft"]["topic"] == "Draft topic" and "_picked" not in got[0]


def test_a_model_with_a_small_output_cap_never_takes_strong_calls(cfg, providers):
    g, q = providers(lambda m: RuntimeError("503 UNAVAILABLE"), lambda m, b: (200, '{"ok": 1}'))
    with pytest.raises(llm.Deferred):  # openai wrote it; qwen can't output a ledger; Gemini is down
        llm.ask_json("p", cfg, strong=True, purpose="verify", avoid={"groq:openai/gpt-oss-120b"})
    assert q.calls == []
    assert llm.ask_json("p", cfg, purpose="polish") == {"ok": 1}  # cheap calls may still use it


# ---------------------------------------------------------------- third family (owner approved 2 Nov)

@pytest.fixture
def three_families(monkeypatch, providers):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    return providers


def test_nemotron_verifies_when_gemini_is_down_and_openai_wrote(cfg, three_families):
    g, q = three_families(lambda m: RuntimeError("503 UNAVAILABLE"), lambda m, b: (200, '{"claims": []}'))
    out = llm.ask_json("p", cfg, strong=True, purpose="verify", avoid={"groq:openai/gpt-oss-120b"})
    assert out == {"claims": []} and llm.family(llm.LAST_MODEL) == "nemotron"
    assert all(not c[0].startswith(("openai/", "qwen/")) for c in q.calls)  # qwen capped, openai avoided


def test_nemotron_is_avoided_when_it_touched_the_text(cfg, three_families):
    three_families(lambda m: RuntimeError("503 UNAVAILABLE"), lambda m, b: (200, '{"x": 1}'))
    with pytest.raises(llm.Deferred) as e:
        llm.ask_json("p", cfg, strong=True, purpose="verify",
                     avoid={"groq:openai/gpt-oss-120b", "openrouter-text:nvidia/nemotron-3-ultra-550b-a55b:free"})
    assert "overload" in e.value.cause


def test_the_free_text_allowance_is_capped(cfg, three_families):
    from pipeline.util import write_json
    write_json(llm.USAGE_FILE, {llm._quota_day(): {"openrouter-text:nvidia/nemotron-3-ultra-550b-a55b:free":
                                                   {"calls": 25, "failed": 0, "tokens": 0}}})
    assert not any(p["name"] == "openrouter-text" for p, _m in llm.alt_models(cfg))
