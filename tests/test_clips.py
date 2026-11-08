"""Clip channel: rights gate, authorized ingestion, transcripts, segments, packaging, compliance, production,
revocation, and a real FFmpeg render of a synthetic source (no network, no model calls)."""
import copy
from pathlib import Path
import datetime as dt
import shutil
import subprocess

import pytest

from pipeline import channels, control, jobs, orchestrator, publish_youtube, qa
from pipeline.clips import compliance, edit, editorial, ingest, package, registry, rights, segments, transcript
from pipeline.clips import orchestrator as clips

SRT = """1
00:00:00,000 --> 00:00:04,000
What are the dangers of going to space?

2
00:00:04,500 --> 00:00:11,000
The first thing I think about is radiation, because astronauts leave the protection of Earth's magnetic field.

3
00:00:11,500 --> 00:00:18,000
On the space station, crews get about ten times the dose people get on the ground in a year.

4
00:00:18,500 --> 00:00:26,000
And as I said earlier, this slide shows the other one we talked about before.

5
00:00:26,500 --> 00:00:34,000
Bone loss is the second danger: without gravity, astronauts can lose one percent of bone mass every month.

6
00:00:34,500 --> 00:00:41,000
That is why they exercise for two hours every single day on the space station.
"""


@pytest.fixture
def ccfg(tmp_path, monkeypatch):
    """The real clips profile, pointed at a temp project."""
    monkeypatch.setattr(channels, "DATA", tmp_path / "proj" / "data")
    monkeypatch.setattr(channels, "OUTPUT", tmp_path / "proj" / "output")
    monkeypatch.setattr(channels, "ROOT", tmp_path / "proj")
    monkeypatch.setattr(ingest, "ROOT", tmp_path / "proj")
    monkeypatch.setattr(rights, "ROOT", tmp_path / "proj")
    # the clip machinery is tested against the archived creator-clips profile (channel 2 now runs true stories)
    prof = tmp_path / "profiles"
    prof.mkdir()
    (prof / "clips.yaml").write_text(sorted((Path(__file__).resolve().parent.parent / "archive").glob("clips-creator-clips-profile-*.yaml"))[-1]
                                     .read_text())
    monkeypatch.setattr(channels, "PROFILES", prof)
    c = channels.activate(channels.load("clips"))
    c["_paths"]["output"].mkdir(parents=True, exist_ok=True)
    # the machinery tests use a public-domain source and transcript-only scoring; niche settings are tested below
    c["rights"]["allowed_pathways"].append("public_domain")
    c["segments"]["weights"]["energy"] = 0
    c["schedule"]["slots"] = ["08:00", "11:30", "14:30", "18:00", "21:00"]
    publish_youtube.EXPECTED_CHANNEL = "UC_TEST_CLIPS"
    c["channel"]["youtube_channel_id"] = "UC_TEST_CLIPS"
    return c


def nasa_src(cfg, sid="nasa:test1", **rights_over):
    rec = {"id": sid, "platform": "nasa", "nasa_id": sid.split(":")[1], "title": "What Are the Dangers of Going to Space",
           "creator": "NASA LaRC", "description": "A NASA expert explains radiation and bone loss.", "keywords": ["space"],
           "url": "https://images.nasa.gov/details/x", "published": "2025-04-01",
           "rights": {"pathway": "public_domain", "holder": "NASA", "licence": "public-domain",
                      "evidence": "https://www.nasa.gov/nasa-brand-center/images-and-media/", "commercial_use": True,
                      "attribution_required": True, "attribution_text": "Video: NASA",
                      "permitted_uses": ["edit", "publish_youtube", "monetize"], **rights_over}}
    registry.upsert(cfg, rec)
    return sid


def approve(cfg, sid):
    return rights.approve(cfg, sid, reviewer="tester", note="checked: no third-party music")


# ---------------------------------------------------------------- rights gate

def test_popular_video_without_rights_record_goes_to_manual_review(ccfg):
    registry.upsert(ccfg, {"id": "yt:abc", "platform": "youtube", "title": "Viral", "views": 9_000_000})
    d = rights.review(ccfg, "yt:abc")
    assert d["status"] == "manual_review" and "not permission" in d["soft"][0]
    assert not rights.is_cleared(ccfg, "yt:abc")


@pytest.mark.parametrize("licence,expect", [("CC-BY-NC-4.0", "commercial"), ("CC-BY-ND-4.0", "derivatives")])
def test_licences_that_forbid_the_use_are_rejected(ccfg, licence, expect):
    nasa_src(ccfg, pathway="licence", licence=licence)
    d = rights.review(ccfg, "nasa:test1")
    assert d["status"] == "rejected" and any(expect in h for h in d["hard"])


def test_expired_permission_blocks(ccfg):
    nasa_src(ccfg, expires=(dt.date.today() - dt.timedelta(days=1)).isoformat())
    approve(ccfg, "nasa:test1")
    assert rights.evaluate(registry.get(ccfg, "nasa:test1"), ccfg)["status"] == "rejected"
    assert not rights.is_cleared(ccfg, "nasa:test1")


def test_missing_permission_evidence_blocks(ccfg):
    nasa_src(ccfg, pathway="written_permission", evidence="docs/permissions/missing.pdf")
    approve(ccfg, "nasa:test1")
    d = rights.evaluate(registry.get(ccfg, "nasa:test1"), ccfg)
    assert d["status"] == "rejected" and any("evidence missing" in h for h in d["hard"])


def test_written_permission_must_cover_every_intended_use(ccfg):
    ev = rights.ROOT / "permission.pdf"
    ev.write_text("signed")
    nasa_src(ccfg, pathway="written_permission", evidence="permission.pdf", permitted_uses=["edit"])
    approve(ccfg, "nasa:test1")
    d = rights.evaluate(registry.get(ccfg, "nasa:test1"), ccfg)
    assert d["status"] == "rejected" and any("publish_youtube" in h for h in d["hard"])


def test_public_domain_needs_a_human_then_clears(ccfg):
    nasa_src(ccfg)
    assert rights.review(ccfg, "nasa:test1")["status"] == "manual_review"
    assert approve(ccfg, "nasa:test1")["status"] == "approved"
    assert rights.is_cleared(ccfg, "nasa:test1") and registry.get(ccfg, "nasa:test1")["status"] == "eligible"


def test_human_approval_cannot_override_a_hard_failure(ccfg):
    nasa_src(ccfg, commercial_use=False)
    assert approve(ccfg, "nasa:test1")["status"] == "rejected"


def test_remix_pathway_is_not_automatable(ccfg):
    nasa_src(ccfg, pathway="platform_remix")
    ccfg["rights"]["allowed_pathways"].append("platform_remix")
    d = rights.evaluate(registry.get(ccfg, "nasa:test1"), ccfg)
    assert d["status"] == "rejected" and any("Data API can't create a remix" in h for h in d["hard"])


def test_third_party_markers_force_review(ccfg):
    sid = nasa_src(ccfg)
    registry.update(ccfg, sid, description="Music licensed from Example Records. © 2025")
    assert any("third-party" in s for s in rights.evaluate(registry.get(ccfg, sid), ccfg)["soft"])


# ---------------------------------------------------------------- ingestion

@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=x", "https://youtu.be/x",
                                 "https://www.twitch.tv/videos/1", "https://kick.com/x/clips/1",
                                 "https://www.tiktok.com/@a/video/1", "https://example.com/a.mp4",
                                 "http://images-assets.nasa.gov/video/a.mp4"])
def test_unauthorized_hosts_are_never_downloaded(ccfg, url):
    with pytest.raises(ingest.UnauthorizedSource):
        ingest.check_url(url, ccfg)


def test_allowlisted_host_passes(ccfg):
    ccfg["ingest"]["allowed_hosts"] = ["images-assets.nasa.gov"]
    ingest.check_url("https://images-assets.nasa.gov/video/x/x~large.mp4", ccfg)


def test_uncleared_source_is_never_ingested(ccfg, monkeypatch):
    nasa_src(ccfg)
    monkeypatch.setattr("requests.get", lambda *a, **k: pytest.fail("network used for an uncleared source"))
    with pytest.raises(ingest.UnauthorizedSource, match="not rights-cleared"):
        ingest.ingest(ccfg, "nasa:test1")


def test_supplied_files_must_live_in_the_channel_source_folder(ccfg, tmp_path):
    outside = tmp_path / "elsewhere.mp4"
    outside.write_bytes(b"x")
    registry.upsert(ccfg, {"id": "file:x", "platform": "file", "title": "t", "creator": "c",
                           "media": {"kind": "file", "location": str(outside)},
                           "rights": {"pathway": "own", "evidence": str(outside), "commercial_use": True}})
    rights.approve(ccfg, "file:x", "tester")
    with pytest.raises(ingest.UnauthorizedSource, match="must live under"):
        ingest.ingest(ccfg, "file:x")


# ---------------------------------------------------------------- transcripts and segments

def test_srt_and_vtt_parse_to_timed_cues():
    cues = transcript.parse(SRT)
    assert len(cues) == 6 and cues[1]["start"] == 4.5 and cues[1]["end"] == 11.0
    vtt = "WEBVTT\n\n00:01.000 --> 00:03.500 align:start\n<v Speaker>Hello <b>there</b>.\n"
    assert transcript.parse(vtt) == [{"start": 1.0, "end": 3.5, "text": "Hello there."}]
    words = transcript.words(cues[:1])
    assert words[0]["start"] == 0.0 and abs(words[-1]["end"] - 4.0) < 1e-6


def _ingested(cfg, sid="nasa:test1"):
    nasa_src(cfg, sid)
    approve(cfg, sid)
    d = cfg["_paths"]["data"] / "media"
    d.mkdir(parents=True, exist_ok=True)
    (d / "t.srt").write_text(SRT)
    registry.update(cfg, sid, status="ingested", media={"local": str(d / "src.mp4")},
                    transcript={"kind": "srt", "local": str(d / "t.srt")})
    return sid


def test_segments_have_valid_sentence_aligned_timestamps(ccfg):
    ccfg["segments"].update(min_seconds=10, max_seconds=25, min_score=0.0, per_source=3)
    picked = segments.find(ccfg, _ingested(ccfg))
    assert picked
    starts = {round(c["start"], 2) for c in transcript.parse(SRT)}
    for s in picked:
        assert 0 <= s["start"] < s["end"] <= 41 and 10 <= s["end"] - s["start"] <= 25
        assert round(s["start"], 2) in starts  # starts on a sentence (cue) boundary
        assert s["text"].rstrip()[-1] in ".?!"  # ends on a full stop, not mid-sentence
        assert s["why"] and set(s["scores"]) == {"hook", "completeness", "standalone", "pacing", "relevance", "clarity"}
    assert not any("as I said" in s["text"] for s in picked[:1])  # context-dependent window ranks last


def test_segments_are_not_duplicated_across_runs(ccfg):
    ccfg["segments"].update(min_seconds=10, max_seconds=25, min_score=0.0, per_source=5)
    sid = _ingested(ccfg)
    first = segments.find(ccfg, sid)
    second = segments.find(ccfg, sid)
    assert first and second == []
    for i, a in enumerate(first):
        for b in first[i + 1:]:
            assert registry.overlap((a["start"], a["end"]), (b["start"], b["end"])) <= 0.3


def test_same_quote_from_another_source_is_a_duplicate(ccfg):
    ccfg["segments"].update(min_seconds=10, max_seconds=25, min_score=0.0)
    first = segments.find(ccfg, _ingested(ccfg, "nasa:a"))
    reason = registry.is_duplicate(ccfg, "nasa:b", 0, 20, first[0]["text"])
    assert reason and "word overlap" in reason


def test_uncleared_source_is_never_segmented(ccfg):
    sid = _ingested(ccfg)
    rights.revoke(ccfg, sid, "creator withdrew permission")
    with pytest.raises(ingest.UnauthorizedSource):
        segments.find(ccfg, sid)


# ---------------------------------------------------------------- packaging and compliance

NARR = {"intro": "This NASA expert names the two biggest threats astronauts face, and the first one is invisible.",
        "outro": "Radiation and bone loss are why long missions need shielding and daily exercise. Mars crews would "
                 "face both for years, so this is a problem NASA has to solve first.",
        "title_options": ["The Invisible Danger Astronauts Face", "Why Astronauts Exercise 2 Hours a Day"],
        "pinned_comment": "Which danger would worry you more?", "claims": [], "feasible": True}


def seg_rec(sid="nasa:test1"):
    return {"id": f"{sid}@4.5-18.0", "source_id": sid, "start": 4.5, "end": 18.0, "score": 0.8, "type": "explainer",
            "text": "The first thing I think about is radiation.", "why": ["x"], "scores": {}}


def test_description_carries_attribution_and_sparing_hashtags(ccfg):
    sid = nasa_src(ccfg)
    meta = package.package(seg_rec(sid), registry.get(ccfg, sid), NARR, ccfg)
    assert "Video: NASA" in meta["description"] and "NASA LaRC" in meta["description"]
    assert "does not endorse" in meta["description"]
    assert len([w for w in meta["description"].split() if w.startswith("#")]) <= 3


def test_titles_stay_unique_across_the_queue(ccfg):
    sid = nasa_src(ccfg)
    jobs.upsert({"folder": "x", "topic": "t", "status": "scheduled", "title": "The Invisible Danger Astronauts Face"})
    assert package.package(seg_rec(sid), registry.get(ccfg, sid), NARR, ccfg)["title"].startswith("Why Astronauts")
    jobs.upsert({"folder": "y", "topic": "t", "status": "ready", "title": "Why Astronauts Exercise 2 Hours a Day"})
    with pytest.raises(ValueError, match="no unique title"):
        package.package(seg_rec(sid), registry.get(ccfg, sid), NARR, ccfg)


def _compliance(ccfg, monkeypatch, share=0.35, **src_over):
    sid = nasa_src(ccfg, **src_over)
    approve(ccfg, sid)
    ccfg["review"]["enabled"] = False
    monkeypatch.setattr(qa, "technical", lambda v, c: [("video stream", True, "h264 1080x1920")])
    src = registry.get(ccfg, sid)
    meta = package.package(seg_rec(sid), src, NARR, ccfg)
    ed = {"clip_seconds": 13.5, "original_share": share, "attribution_on_screen": "Video: NASA"}
    return compliance.review("v.mp4", seg_rec(sid), src, NARR, meta, ed, ccfg, None), sid


def test_compliance_passes_a_clean_short(ccfg, monkeypatch):
    res, _ = _compliance(ccfg, monkeypatch)
    assert res["pass"], [c for c in res["checks"] if not c["ok"]]


def test_too_little_original_contribution_fails(ccfg, monkeypatch):
    res, _ = _compliance(ccfg, monkeypatch, share=0.1)
    assert not res["pass"] and any(c["name"] == "original narration share" and not c["ok"] for c in res["checks"])


def test_permitted_clip_length_is_enforced(ccfg, monkeypatch):
    res, _ = _compliance(ccfg, monkeypatch, max_clip_seconds=10)
    assert not res["pass"] and any(c["name"] == "within permitted clip length" and not c["ok"] for c in res["checks"])


def test_ai_reviewer_error_fails_closed(ccfg, monkeypatch):
    sid = nasa_src(ccfg)
    approve(ccfg, sid)
    monkeypatch.setattr(qa, "technical", lambda v, c: [])
    monkeypatch.setattr(compliance, "_frames", lambda v, n, w: [])
    monkeypatch.setattr(compliance, "ask_json", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("quota")))
    src = registry.get(ccfg, sid)
    meta = package.package(seg_rec(sid), src, NARR, ccfg)
    res = compliance.review("v.mp4", seg_rec(sid), src, NARR, meta,
                            {"clip_seconds": 13, "original_share": 0.4, "attribution_on_screen": "Video: NASA"}, ccfg, None)
    assert not res["pass"] and any(c["name"] == "AI review ran" and not c["ok"] for c in res["checks"])


# ---------------------------------------------------------------- production, gates, revocation

@pytest.fixture
def fake_production(ccfg, monkeypatch):
    """Everything except the model and FFmpeg is real."""
    monkeypatch.setattr(editorial, "write", lambda seg, src, cfg: copy.deepcopy(NARR))

    def fake_render(seg, src, narration, cfg, work, out):
        out.write_bytes(b"video")
        return out, {"clip_seconds": seg["end"] - seg["start"], "original_share": 0.35,
                     "attribution_on_screen": "Video: NASA"}
    monkeypatch.setattr(edit, "render", fake_render)
    monkeypatch.setattr(qa, "technical", lambda v, c: [("video stream", True, "")])
    ccfg["review"]["enabled"] = False
    ccfg["segments"].update(min_seconds=10, max_seconds=25, min_score=0.0)
    return ccfg


def test_a_cleared_segment_becomes_a_ready_short(fake_production):
    cfg = fake_production
    segments.find(cfg, _ingested(cfg))
    job = clips.make_short(cfg)
    assert job["status"] == "ready" and job["source_id"] == "nasa:test1"  # approval off: the gates approve
    s = jobs.script_of(job)
    assert s["edit"]["original_share"] >= 0.25 and "Video: NASA" in s["youtube"]["description"]
    assert (cfg["_paths"]["output"] / job["folder"] / "post.txt").exists()
    assert next(x for x in registry.segments(cfg) if x["id"] == job["segment_id"])["status"] == "used"


def test_compliance_failure_never_reaches_the_queue(fake_production, monkeypatch):
    cfg = fake_production
    segments.find(cfg, _ingested(cfg))
    monkeypatch.setattr(qa, "technical", lambda v, c: [("resolution", False, "1920x1080")])
    job = clips.make_short(cfg)
    assert job["status"] == "quarantined" and "resolution" in job["reason"]
    uploads = []
    monkeypatch.setattr(publish_youtube, "upload", lambda *a, **k: uploads.append(1))
    cfg["youtube"]["enabled"] = True
    orchestrator.upload_pending(cfg)
    assert uploads == []


def test_editor_refusal_rejects_the_segment(fake_production, monkeypatch):
    cfg = fake_production
    segments.find(cfg, _ingested(cfg))
    monkeypatch.setattr(editorial, "write", lambda *a: (_ for _ in ()).throw(editorial.NotFeasible("needs context")))
    job = clips.make_short(cfg)
    assert job["status"] == "failed" and job["stage_failed"] == "editorial"
    assert next(x for x in registry.segments(cfg) if x["id"] == job["segment_id"])["status"] == "rejected"


def test_revoked_rights_block_publishing_even_after_approval(fake_production, monkeypatch):
    cfg = fake_production
    segments.find(cfg, _ingested(cfg))
    job = clips.make_short(cfg)
    jobs.mark(job["folder"], status="ready")
    registry.update(cfg, "nasa:test1", rights={**registry.get(cfg, "nasa:test1")["rights"], "revoked": True})
    uploads = []
    monkeypatch.setattr(publish_youtube, "upload", lambda *a, **k: uploads.append(1))
    cfg["youtube"]["enabled"] = True
    orchestrator.publish(cfg, job["folder"])
    assert uploads == [] and jobs.find(jobs.load(), job["folder"])["status"] == "quarantined"


def test_revocation_pulls_queued_and_flags_live_shorts(fake_production):
    cfg = fake_production
    segments.find(cfg, _ingested(cfg))
    queued = clips.make_short(cfg)
    live = clips.make_short(cfg)
    jobs.mark(live["folder"], status="scheduled", youtube_id="V1", publish_at="2026-10-30T09:00:00+05:30")
    pulled, flagged = rights.revoke(cfg, "nasa:test1", "licence ended")
    by = {j["folder"]: j for j in jobs.load()}
    assert by[queued["folder"]]["status"] == "quarantined" and pulled == [queued["folder"]]
    assert by[live["folder"]]["status"] == "scheduled" and "revoked" in by[live["folder"]]["alert"]
    assert flagged == [live["folder"]]
    assert segments.next_candidate(cfg) is None


def test_shortfall_is_recorded_when_no_cleared_candidates(ccfg, monkeypatch):
    nasa_src(ccfg)  # discovered but not cleared
    monkeypatch.setattr(jobs, "now", lambda: dt.datetime(2026, 10, 24, 10, 0, tzinfo=jobs.TZ))
    clips.produce(ccfg)
    assert jobs.load() == []
    assert "shortfall" in (ccfg["_paths"]["events"]).read_text()


def test_five_slots_a_day_on_the_clip_channel(ccfg, monkeypatch):
    t = dt.datetime(2026, 10, 24, 6, 0, tzinfo=jobs.TZ)
    monkeypatch.setattr(jobs, "now", lambda: t)
    free = jobs.free_slots(ccfg, [], 1)
    assert [f"{s:%d %H:%M}" for s in free][:5] == ["24 08:00", "24 11:30", "24 14:30", "24 18:00", "24 21:00"]
    assert len(free) == 10


# ---------------------------------------------------------------- real FFmpeg render (synthetic source)

@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_real_render_produces_a_valid_vertical_short(ccfg, tmp_path, monkeypatch):
    src_mp4 = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=30:duration=42",
                    "-f", "lavfi", "-i", "sine=frequency=330:duration=42", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(src_mp4)], check=True)
    sid = _ingested(ccfg)
    registry.update(ccfg, sid, media={"local": str(src_mp4)})

    def fake_tts(scenes, cfg, work):  # a tone instead of Edge TTS: keeps the test offline
        n = len(scenes[0]["text"].split())
        dur = n * 0.38
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency=520:duration={dur}",
                        "-ar", "44100", "-ac", "1", str(work / "voice.wav")], check=True)
        words = [{"start": i * 0.38, "end": (i + 1) * 0.38, "word": w} for i, w in enumerate(scenes[0]["text"].split())]
        return [{"duration": dur, "words": words}], work / "voice.wav"
    monkeypatch.setattr(edit.voice, "synthesize", fake_tts)
    seg = {"id": "s", "source_id": sid, "start": 4.5, "end": 18.0}
    out = ccfg["_paths"]["output"] / "short.mp4"
    video, rec = edit.render(seg, registry.get(ccfg, sid), NARR, ccfg, tmp_path / "work", out)
    checks = {n: (ok, d) for n, ok, d in qa.technical(video, ccfg)}
    assert checks["resolution"][0] and checks["video stream"][0] and checks["audio stream"][0], checks
    assert checks["duration"][0] and checks["no black frames"][0], checks
    assert rec["original_share"] > 0.25 and rec["caption_words"] > 20
    assert 13 <= rec["clip_seconds"] <= 14.2 and rec["cut"] == [4.35, 18.15]


def test_framing_repetition_and_unfinished_lists_are_penalised(ccfg):
    s = lambda t, a, b: {"text": t, "start": a, "end": b, "words": []}
    good = [s("What are the dangers of going to space?", 0, 3), s("Radiation is the first, bone loss the second.", 3, 8)]
    meta = [s("What are the dangers of going to space?", 0, 3), s("We Asked a NASA Scientist.", 3, 5),
            s("What are the dangers of going into space?", 5, 8)]
    unfinished = [s("What are the dangers of going to space?", 0, 3),
                  s("The first thing I think about is the rocket launch.", 3, 8)]
    g, _, _ = segments.score_window(good, ccfg)
    m, _, why_m = segments.score_window(meta, ccfg)
    u, _, why_u = segments.score_window(unfinished, ccfg)
    assert g > m and g > u
    assert any("framing" in w for w in why_m) and any("payoff outside" in w for w in why_u)


# ---------------------------------------------------------------- creator / gaming niche

def campaign_src(cfg, sid="yt:abc123", **over):
    ev = rights.ROOT / "campaign-terms.png"
    ev.write_text("screenshot of the accepted campaign terms")
    rec = {"id": sid, "platform": "youtube", "title": "I Tried The Hardest Game", "creator": "BigCreator",
           "description": "funny gaming video", "url": "https://www.youtube.com/watch?v=abc123",
           "rights": {"pathway": "clip_campaign", "campaign": "BigCreator Clip Program (Discord)",
                      "holder": "BigCreator LLC", "evidence": "campaign-terms.png", "commercial_use": True,
                      "permitted_uses": ["edit", "publish_youtube", "monetize"], "attribution_required": True,
                      "attribution_text": "Clip: BigCreator",
                      "requirements": {"hashtags": ["#BigCreatorClips"], "mention": "@BigCreator",
                                       "description_lines": ["Watch the full video on BigCreator's channel"],
                                       "tags": ["bigcreator"]}, **over}}
    registry.upsert(cfg, rec)
    return sid


def test_clip_campaign_needs_human_confirmation_then_clears(ccfg):
    sid = campaign_src(ccfg)
    d = rights.review(ccfg, sid)
    assert d["status"] == "manual_review" and any("confirm the terms" in x for x in d["soft"])
    assert rights.approve(ccfg, sid, "tester")["status"] == "approved"
    # permission alone never fetches anything: without the campaign's own file there is no media pathway
    with pytest.raises(ingest.UnauthorizedSource, match="no authorized media pathway"):
        ingest.ingest(ccfg, sid)


def test_campaign_without_name_or_monetization_is_rejected(ccfg):
    sid = campaign_src(ccfg, campaign="", commercial_use=False)
    hard = rights.evaluate(registry.get(ccfg, sid), ccfg)["hard"]
    assert any("campaign's name" in h for h in hard) and any("commercial" in h for h in hard)


def test_campaign_requirements_are_packaged_and_checked(ccfg, monkeypatch):
    sid = campaign_src(ccfg)
    registry.update(ccfg, sid, media={"kind": "file", "location": "assets/sources/clips/x.mp4"})
    rights.approve(ccfg, sid, "tester")
    src = registry.get(ccfg, sid)
    meta = package.package(seg_rec(sid), src, NARR, ccfg)
    assert "#BigCreatorClips" in meta["description"] and "@BigCreator" in meta["description"]
    assert "Watch the full video on BigCreator's channel" in meta["description"] and "bigcreator" in meta["tags"]
    ccfg["review"]["enabled"] = False
    monkeypatch.setattr(qa, "technical", lambda v, c: [])
    ed = {"clip_seconds": 13, "original_share": 0.4, "attribution_on_screen": "Clip: BigCreator"}
    ok = compliance.review("v.mp4", seg_rec(sid), src, NARR, meta, ed, ccfg, None)
    assert ok["pass"], [c for c in ok["checks"] if not c["ok"]]
    meta["description"] = meta["description"].replace("#BigCreatorClips", "")
    bad = compliance.review("v.mp4", seg_rec(sid), src, NARR, meta, ed, ccfg, None)
    assert not bad["pass"] and any(c["name"] == "campaign/creator requirements met" and not c["ok"] for c in bad["checks"])


def test_youtube_lead_with_supplied_file_attaches_to_the_lead(ccfg, tmp_path):
    registry.upsert(ccfg, {"id": "yt:abc123", "platform": "youtube", "title": "Lead", "creator": "BigCreator"})
    f = tmp_path / "raw.mp4"
    f.write_bytes(b"footage from the campaign pack")
    ev = rights.ROOT / "campaign-terms.png"
    ev.write_text("x")
    sid = ingest.register_file(ccfg, f, {"pathway": "clip_campaign", "campaign": "C", "evidence": "campaign-terms.png",
                                         "commercial_use": True, "permitted_uses": ["edit", "publish_youtube", "monetize"]},
                               "Lead", "BigCreator", youtube_id="abc123")
    src = registry.get(ccfg, sid)
    assert sid == "yt:abc123" and src["media"]["kind"] == "file" and src["rights"]["pathway"] == "clip_campaign"


def test_niche_moment_types_follow_the_profile(ccfg):
    assert segments._type("bro what no way haha that was so funny", ccfg) == "funny"
    assert segments._type("LET'S GO that was a clutch first try", ccfg) == "hype"
    assert segments._type("honestly I think this game is overrated", ccfg) == "hot_take"
    assert ccfg["planning"]["roles"] == ["funny", "hype", "reaction", "story", "hot_take"]


def test_audio_energy_rewards_loud_moments():
    curve = [-30.0] * 60
    curve[40:43] = [-14.0, -12.0, -15.0]  # laughter / shouting
    assert segments._energy(curve, 35, 50) >= 0.9 and segments._energy(curve, 0, 20) == 0.0


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")
def test_loudness_curve_from_real_audio(tmp_path):
    f = tmp_path / "a.wav"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
                    "-af", "volume='if(between(t,3,4),1,0.05)':eval=frame", str(f)], check=True)
    curve = segments.loudness(f)
    assert len(curve) >= 5 and max(curve[3:5]) > curve[1] + 15


def test_streaming_platforms_are_never_downloaded(ccfg):
    for url in ("https://www.twitch.tv/videos/1", "https://kick.com/a/clips/b", "https://v.redd.it/x",
                "https://streamable.com/x"):
        with pytest.raises(ingest.UnauthorizedSource):
            ingest.check_url(url, ccfg)


def test_with_approval_on_a_short_waits_for_a_human(fake_production):
    cfg = fake_production
    cfg["review"]["human_approval"] = True
    segments.find(cfg, _ingested(cfg))
    assert clips.make_short(cfg)["status"] == "awaiting_approval"
