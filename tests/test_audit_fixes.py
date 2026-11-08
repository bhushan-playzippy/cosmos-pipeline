"""Regressions for the 1 Nov full-system audit (reports/critical-issues-and-fixes-2026-11-01.md)."""
import csv
import subprocess
import types

import pytest

from pipeline import analytics, channel, control, growth, ideas, jobs, llm, orchestrator, publish_youtube, qa, voice
from pipeline.util import read_json, write_json
from tests.conftest import TEST_CHANNEL, add_job, make_script


# ---------------------------------------------------------------- P0: channel identity on every non-upload call

def _client(channel_id, calls):
    def list_(**k):
        calls.append("channels.list")
        return types.SimpleNamespace(execute=lambda: {"items": [{"id": channel_id}]})
    return types.SimpleNamespace(channels=lambda: types.SimpleNamespace(list=list_))


def test_channel_calls_refuse_a_token_for_another_channel(monkeypatch):
    monkeypatch.setattr(channel, "_VERIFIED", set())
    monkeypatch.setattr(channel, "_client", lambda: _client("UC_SOMEONE_ELSE", []))
    with pytest.raises(publish_youtube.ChannelMismatch):
        channel._yt()


def test_channel_identity_is_checked_once_per_token(monkeypatch):
    calls = []
    monkeypatch.setattr(channel, "_VERIFIED", set())
    monkeypatch.setattr(channel, "_client", lambda: _client(TEST_CHANNEL, calls))
    channel._yt()
    channel._yt()
    assert calls == ["channels.list"]


def test_sync_never_mass_deletes_when_youtube_returns_nothing(cfg, monkeypatch, fixed_now):
    add_job("a", status="scheduled", youtube_id="A", publish_at="2026-10-25T09:00:00+05:30")
    add_job("b", status="published", youtube_id="B", publish_at="2026-10-23T09:00:00+05:30")
    monkeypatch.setattr(channel, "video_status", lambda ids: [])
    orchestrator.sync_status(cfg)
    assert {j["status"] for j in jobs.load()} == {"scheduled", "published"}


# ---------------------------------------------------------------- P0: never publish unverified text

@pytest.fixture
def uploads(monkeypatch):
    calls = []

    def up(path, script, cfg, slot, on_insert=None):
        calls.append(path)
        return "VID", {"ok": True, "problems": []}
    monkeypatch.setattr(publish_youtube, "upload", up)
    return calls


def test_publish_refuses_a_script_edited_after_its_claim_check(cfg, uploads, fixed_now):
    cfg["youtube"]["playlist"] = None
    add_job("edited", script={**make_script("x"), "claims_for": "0000000000000000"})
    j = orchestrator.publish(cfg, "edited")
    assert uploads == [] and j["status"] == "quarantined" and "not verified" in j["reason"]


def test_publish_uploads_a_verified_script(cfg, uploads, fixed_now):
    cfg["youtube"]["playlist"] = None
    add_job("ok")  # add_job stamps the claim fingerprint, as a gate-passed job has
    assert orchestrator.publish(cfg, "ok")["status"] == "scheduled" and len(uploads) == 1


def test_upload_with_verification_problems_is_flagged(cfg, monkeypatch, fixed_now):
    cfg["youtube"]["playlist"] = None
    monkeypatch.setattr(publish_youtube, "upload",
                        lambda *a, **k: ("VID", {"ok": False, "problems": ["publishAt missing"]}))
    add_job("p")
    j = orchestrator.publish(cfg, "p")
    assert j["status"] == "scheduled" and "publishAt missing" in j["alert"]


# ---------------------------------------------------------------- P1: comments step isolation

def test_one_missing_script_does_not_stop_other_comments(cfg, monkeypatch, fixed_now):
    add_job("a", status="published", youtube_id="A")
    add_job("b", status="published", youtube_id="B")
    (orchestrator.OUTPUT / "a" / "script.json").unlink()
    posted = []
    monkeypatch.setattr(channel, "comment", lambda vid, text: posted.append(vid))
    orchestrator.engage(cfg)
    assert posted == ["B"]


# ---------------------------------------------------------------- P1: reviewer must score every criterion

def _review(monkeypatch, tmp_path, cfg, answer):
    from pipeline import review
    monkeypatch.setattr(review, "_frames", lambda *a: [])
    monkeypatch.setattr(review, "_audio", lambda *a: None)
    monkeypatch.setattr(review, "ask_json", lambda *a, **k: answer)
    return review.review_video("v", [], make_script("t"), cfg, tmp_path)


def test_reviewer_that_skips_visual_match_does_not_pass(cfg, monkeypatch, tmp_path):
    rv = _review(monkeypatch, tmp_path, cfg, {"pass": True, "scores": {"hook": 9, "accuracy": 10, "visual_quality": 9,
                                                                       "captions": 9, "story": 9}})
    assert not rv["pass"] and any("visual_match" in i for i in rv["issues"])


def test_reviewer_string_scores_are_read_as_numbers(cfg, monkeypatch, tmp_path):
    scores = {k: "8/10" for k in ("hook", "visual_match", "visual_quality", "captions", "story")}
    rv = _review(monkeypatch, tmp_path, cfg, {"pass": True, "scores": {**scores, "accuracy": "9"}})
    assert rv["pass"] and rv["scores"]["accuracy"] == 9


# ---------------------------------------------------------------- P1: analytics attribution

def _rows(sandbox, rows):
    p = analytics.ADIR / "videos.csv"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def test_withdrawn_video_no_longer_counts_as_public(cfg, sandbox, fixed_now):
    add_job("live", status="published", youtube_id="L", publish_at="2026-10-20T09:00:00+05:30")
    add_job("pulled", status="withdrawn", youtube_id="W", publish_at="2026-10-20T09:00:00+05:30")
    base = {"role": "discovery", "slot": "09:00", "privacy": "public", "likes": 0, "comments": 0}
    _rows(sandbox, [{"date": "2026-10-21", "video_id": "W", "folder": "pulled", "views": 900, **base},
                    {"date": "2026-10-24", "video_id": "L", "folder": "live", "views": 100, **base}])
    assert list(analytics.latest_rows()) == ["L"]
    assert growth.analyse(cfg)["videos"] == 1
    assert "| role:discovery | 1 |" in analytics.write_weekly_report(cfg).read_text()


def test_small_groups_move_the_picker_less():
    assert growth.shrink(1.6, 5, 5) == pytest.approx(1.3)
    assert growth.shrink(1.6, 50, 5) > 1.5
    assert analytics.median([1, 2, 3, 10]) == 2.5


def test_performance_reads_durations_from_the_active_channels_output(sandbox, monkeypatch):
    from pipeline import performance
    seen = []
    monkeypatch.setattr(performance, "media_duration", lambda p: seen.append(p) or 40.0)
    add_job("x", status="published")
    performance._features(jobs.load()[0])
    assert seen and str(seen[0]).startswith(str(jobs.OUTPUT))


# ---------------------------------------------------------------- P1: narration/audio completeness

def test_truncated_tts_stream_is_detected():
    text = "The submarine sank three times before it finally sank a warship in Charleston harbor."
    spoken = [{"word": w} for w in text.split()]
    assert voice.complete(text, spoken)
    assert not voice.complete(text, spoken[:6])
    assert not voice.complete(text, spoken[:-4])  # cut off near the end (the last 3 words absorb merged boundaries)
    # real Edge TTS boundaries (1 Nov): merged number phrases and a two-word final boundary are complete
    andromeda = "Why is the light hitting your eye from Andromeda 2.5 million years old?"
    merged = ["Why", "is", "the", "light", "hitting", "your", "eye", "from", "Andromeda", "2.5 million years", "old"]
    assert voice.complete(andromeda, [{"word": w} for w in merged])
    assert voice.complete("Day and night every single 24 hours.",
                          [{"word": w} for w in ["Day", "and", "night", "every", "single", "24 hours"]])


def test_qa_fails_a_video_whose_audio_stops_early(tmp_path, cfg):
    v = tmp_path / "v.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=s=1080x1920:r=30:d=25",
                    "-f", "lavfi", "-i", "sine=f=440:d=10", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac",
                    "-pix_fmt", "yuv420p", str(v)], check=True)
    checks = {n: ok for n, ok, _ in qa.technical(v, cfg)}
    assert checks["audio covers video"] is False


# ---------------------------------------------------------------- P1/P2: model-call robustness

def test_unescaped_quotes_inside_strings_are_repaired():
    broken = '{"youtube": {"title": "T", "description": "Why the "Hunley" sank, "twice" — see "Sources": NASA"}}'
    assert llm._extract_json(broken)["youtube"]["description"] == 'Why the "Hunley" sank, "twice" — see "Sources": NASA'
    assert llm._extract_json('{"a": "b"}') == {"a": "b"}


def test_google_server_errors_defer_instead_of_failing():
    assert llm._unavailable("500 INTERNAL. An internal error has occurred")
    assert llm._unavailable("502 Bad Gateway")
    assert not llm._unavailable("Expecting ',' delimiter")


# ---------------------------------------------------------------- P1: dry runs leave real state alone

def test_dry_run_returns_topics_and_uses_distinct_slots(cfg, monkeypatch, fixed_now):
    control.DRY_RUN = True
    cfg["schedule"]["buffer_days"] = 0
    write_json(ideas.BACKLOG, [{"topic": "A", "score": 5}, {"topic": "B", "score": 4}])
    monkeypatch.setattr(ideas, "refill_backlog", lambda cfg: read_json(ideas.BACKLOG, []))
    slots = []

    def once(cfg, topic, script, slot, role):
        slots.append(slot)
        return jobs.upsert({"folder": f"d{len(slots)}", "topic": topic["topic"], "status": "dry_run"})
    monkeypatch.setattr(orchestrator, "_make_once", once)
    orchestrator.produce(cfg, max_videos=2)
    assert len(slots) == 2 and slots[0] != slots[1]
    assert sorted(t["topic"] for t in read_json(ideas.BACKLOG, [])) == ["A", "B"]


def test_story_topics_without_archive_images_defer_after_six(cfg, monkeypatch, fixed_now):
    cfg["writer"] = {"template": "story"}
    write_json(ideas.BACKLOG, [{"topic": f"Obscure thing {i}", "score": 5} for i in range(12)])
    monkeypatch.setattr(ideas, "refill_backlog", lambda cfg: read_json(ideas.BACKLOG, []))
    monkeypatch.setattr("pipeline.visuals.commons_available", lambda q, need=2, **k: False)
    made = []
    monkeypatch.setattr(orchestrator, "_make_once", lambda *a: made.append(a))
    with pytest.raises(llm.Deferred, match="archive images"):
        orchestrator.make_video(cfg, slot=jobs.now())
    assert made == []


# ---------------------------------------------------------------- P1: status command

def test_status_lists_an_awaiting_video_without_a_slot(cfg, capsys, fixed_now):
    import main
    add_job("hunley", status="awaiting_approval", slot=None)
    main.status(cfg)
    assert "next free slot" in capsys.readouterr().out
