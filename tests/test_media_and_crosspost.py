"""Background music, repeated-line safety net, on-subject archive images, odd-shaped stills, Instagram crosspost."""
import datetime as dt
import shutil
import subprocess

import pytest

from pipeline import control, jobs, orchestrator, publish_instagram, qa, render, storygen, virality, visuals
from tests.conftest import add_job, make_script

ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg not installed")


# ---------------------------------------------------------------- repeated lines (the loop-ending bug)

def test_verbatim_echo_of_the_hook_is_dropped():
    s = make_script("Mars")
    s["scenes"][-1]["text"] = s["scenes"][0]["text"].upper().replace(".", "!")  # same words, other case/punctuation
    assert virality.same_line(s["scenes"][0]["text"], s["scenes"][-1]["text"])
    assert virality.drop_repeats(s) == 1 and len(s["scenes"]) == 7


def test_qa_counts_the_same_repeats_as_the_safety_net(cfg):
    s = make_script("Mars")
    s["scenes"][-1]["text"] = s["scenes"][0]["text"].rstrip(".") + "!"
    assert "no repeated lines" in {n for n, ok, _ in qa.editorial(s, [{"media": "x.jpg"}] * 8, cfg) if not ok}


def test_polish_rewrites_an_echo_ending_and_refuses_an_echo_back(cfg, monkeypatch):
    s = make_script("Mars")
    s["scenes"][0]["text"] = "Landing on Mars takes seven minutes of terror, why?"
    s["scenes"][-1]["text"] = "Landing on Mars takes seven minutes of terror, why?"
    calls = []
    monkeypatch.setattr(storygen, "ask_json", lambda *a, **k: calls.append(1) or
                        {"first": "Landing on Mars takes seven minutes of terror, why?",
                         "last": "Landing on Mars takes seven minutes of terror, why?"})
    storygen.polish(s, cfg)
    assert calls  # the echo triggered a rewrite...
    assert virality.same_line(s["scenes"][-1]["text"], s["scenes"][0]["text"])  # ...but an echo answer is refused
    monkeypatch.setattr(storygen, "ask_json", lambda *a, **k: {
        "first": "Landing on Mars takes seven minutes of terror, why?",
        "last": "Seven minutes, no help from Earth: that is landing on Mars."})
    storygen.polish(s, cfg)
    assert not virality.same_line(s["scenes"][-1]["text"], s["scenes"][0]["text"])


# ---------------------------------------------------------------- background music

def test_every_video_needs_music_when_the_channel_has_a_library(cfg, tmp_path):
    lib = tmp_path / "music"
    lib.mkdir()
    cfg["video"]["music_dir"] = str(lib)
    s = make_script("Saturn")
    s.pop("music")
    tl = [{"media": "x.jpg"}] * 8
    assert "background music" not in {n for n, ok, _ in qa.editorial(s, tl, cfg)}  # empty library: voice only is fine
    (lib / "bed.m4a").write_bytes(b"x")
    assert render.pick_music(cfg).name == "bed.m4a"
    assert "background music" in {n for n, ok, _ in qa.editorial(s, tl, cfg) if not ok}
    s["music"] = "bed.m4a"
    assert "background music" not in {n for n, ok, _ in qa.editorial(s, tl, cfg) if not ok}


def test_pick_music_avoids_repeating_the_last_track(cfg, tmp_path):
    for n in ("a.m4a", "b.m4a"):
        (tmp_path / n).write_bytes(b"x")
    cfg["video"]["music_dir"] = str(tmp_path)
    assert {render.pick_music(cfg, avoid="a.m4a").name for _ in range(20)} == {"b.m4a"}


def test_pick_music_matches_the_video_mood(cfg, tmp_path):
    for n in ("calm.m4a", "dark1.m4a", "dark2.m4a"):
        (tmp_path / n).write_bytes(b"x")
    cfg["video"]["music_dir"] = str(tmp_path)
    cfg["video"]["music_moods"] = {"calm": ["calm"], "mysterious": ["dark1", "dark2"]}
    assert {render.pick_music(cfg, mood="mysterious").name for _ in range(30)} == {"dark1.m4a", "dark2.m4a"}
    assert {render.pick_music(cfg, avoid="dark1.m4a", mood="mysterious").name for _ in range(20)} == {"dark2.m4a"}
    # no mood, an unknown mood or a mood whose tracks are missing → any track of the channel (never voice only)
    assert render.pick_music(cfg).name in {"calm.m4a", "dark1.m4a", "dark2.m4a"}
    assert render.pick_music(cfg, mood="jolly") is not None
    (tmp_path / "calm.m4a").unlink()
    assert render.pick_music(cfg, mood="calm").name in {"dark1.m4a", "dark2.m4a"}
    assert "music_mood" in render.music_mood_rule(cfg) and "mysterious" in render.music_mood_rule(cfg)
    cfg["video"].pop("music_moods")
    assert render.music_mood_rule(cfg) == ""


def test_real_channels_moods_point_at_real_tracks():
    from pipeline import channels
    for cfg in (channels.load("cosmic"), channels.load("clips")):
        names = {m.stem for m in render.music_library(cfg)}
        for mood, tracks in cfg["video"]["music_moods"].items():
            assert set(tracks) <= names, (cfg["channel"]["id"], mood)


def test_real_channels_ship_their_own_music():
    from pipeline import channels
    for name in ("cosmic", "clips"):
        assert render.music_library(channels.load(name)), f"{name} has no background music"


@ffmpeg
def test_gate_hears_whether_the_music_made_it_in(tmp_path):
    """Narration with pauses: digital silence between phrases without music, a soft bed with it."""
    speech = "sine=f=300:d=12,volume='if(lt(mod(t,3),2),0.5,0)':eval=frame"
    run = lambda *a: subprocess.run(["ffmpeg", "-y", "-v", "error", *a], check=True)
    run("-f", "lavfi", "-i", speech, str(tmp_path / "voice.wav"))
    run("-f", "lavfi", "-i", speech, "-f", "lavfi", "-i", "anoisesrc=d=12:a=0.06:c=pink",
        "-filter_complex", "[0][1]amix=inputs=2:normalize=0", str(tmp_path / "mixed.wav"))
    assert qa._quiet_floor(tmp_path / "voice.wav") < -80
    assert qa._quiet_floor(tmp_path / "mixed.wav") > -45


def test_odd_shaped_archive_scans_are_fitted_before_ffmpeg(tmp_path):
    from PIL import Image
    Image.new("L", (600, 20000), 128).save(tmp_path / "strip.png")  # grayscale, extreme aspect ratio
    out = render._fit_image(tmp_path / "strip.png", 1080, 1920, tmp_path, 0)
    with Image.open(out) as im:
        assert im.size == (2160, 3840) and im.mode == "RGB"


# ---------------------------------------------------------------- archive images must be about the story

def test_key_words_drop_years_and_filler():
    assert visuals.key_words("The 1942 Battle of Los Angeles where guns fired") == ["battle", "los", "angeles",
                                                                                    "guns", "fired"]


def test_file_must_mention_the_story_subject():
    page = {"title": "File:Map of the Mexican-American War battle.jpg"}
    meta = {"extmetadata": {"Categories": {"value": "Maps|Battles of the Mexican–American War"}}}
    assert not visuals._about(page, meta, "Battle of Los Angeles")  # any battle is not THE battle
    good = {"title": "File:Battle of Los Angeles.jpg"}
    assert visuals._about(good, {"extmetadata": {}}, "Battle of Los Angeles")
    bear = {"title": "File:Polish soldiers 1944.jpg"}
    assert visuals._about(bear, {"extmetadata": {"Categories": {"value": "Wojtek (bear)"}}}, "Wojtek bear")


def test_search_never_shrinks_to_a_generic_word(monkeypatch, tmp_path):
    tried = []
    monkeypatch.setattr(visuals, "_commons_search", lambda q, *a: tried.append(q) or (None, None))
    visuals.commons_image("Battle of Los Angeles 1942", tmp_path, 0, [], subject="Battle of Los Angeles")
    assert tried and all(len(visuals.key_words(q)) >= 2 for q in tried)
    assert "Battle of" not in tried


# ---------------------------------------------------------------- Instagram crosspost

@pytest.fixture
def ig(cfg, monkeypatch, fixed_now):
    cfg["instagram"].update(enabled=True, max_per_run=5, max_age_days=3)
    monkeypatch.setenv("IG_USER_ID_TEST", "1")
    monkeypatch.setenv("IG_ACCESS_TOKEN_TEST", "t")
    posted = []
    monkeypatch.setattr(publish_instagram, "upload", lambda path, script, c: posted.append(script["topic"]) or
                        f"ig_{len(posted)}")
    return posted


def test_reels_go_out_only_after_youtube_made_the_short_public(cfg, ig):
    add_job("pub", status="published", youtube_id="y1", publish_at="2026-10-23T18:00:00+05:30")
    add_job("sched", status="scheduled", youtube_id="y2", publish_at="2026-10-25T09:00:00+05:30")
    add_job("old", status="published", youtube_id="y3", publish_at="2026-10-10T09:00:00+05:30")
    orchestrator.crosspost(cfg)
    assert ig == ["pub"]  # not the scheduled one (still private), not the back catalogue
    assert jobs.find(jobs.load(), "pub")["instagram_id"] == "ig_1"
    orchestrator.crosspost(cfg)
    assert ig == ["pub"]  # never twice


def test_crosspost_is_off_in_dry_run_and_when_disabled(cfg, ig, monkeypatch):
    add_job("pub", status="published", youtube_id="y1", publish_at="2026-10-23T18:00:00+05:30")
    monkeypatch.setattr(control, "DRY_RUN", True)
    orchestrator.crosspost(cfg)
    monkeypatch.setattr(control, "DRY_RUN", False)
    cfg["instagram"]["enabled"] = False
    orchestrator.crosspost(cfg)
    assert ig == []


def test_interrupted_instagram_upload_is_never_retried_blindly(cfg, ig):
    add_job("pub", status="published", youtube_id="y1", publish_at="2026-10-23T18:00:00+05:30",
            instagram_started="2026-10-24T09:00:00+05:30")
    orchestrator.crosspost(cfg)
    assert ig == [] and "Instagram upload was interrupted" in jobs.find(jobs.load(), "pub")["alert"]


def test_instagram_failure_is_recorded_and_retried_later(cfg, ig, monkeypatch):
    add_job("pub", status="published", youtube_id="y1", publish_at="2026-10-23T18:00:00+05:30")
    monkeypatch.setattr(publish_instagram, "upload", lambda *a: (_ for _ in ()).throw(RuntimeError("token expired")))
    orchestrator.crosspost(cfg)  # must not raise: YouTube work in the same run is unaffected
    j = jobs.find(jobs.load(), "pub")
    assert j["instagram_failures"] == 1 and not j.get("instagram_started") and "token expired" in j["instagram_error"]


def test_youtube_upload_no_longer_posts_to_instagram(cfg):
    import inspect
    assert "publish_instagram" not in inspect.getsource(orchestrator.publish)


def test_caption_keeps_at_most_five_hashtags():
    s = make_script("Saturn")
    s["instagram"] = {"caption": "Rings!", "hashtags": ["#Saturn", "#space", "#Space", "#astronomy", "#planets",
                                                        "#science", "#nasa", "#AIgenerated"]}
    tags = publish_instagram.caption(s).split("\n")[-1].split()
    assert len(tags) == 5 and tags[-1] == "#AIgenerated" and [t.lower() for t in tags].count("#space") == 1


# ---------------------------------------------------------------- add-music for videos made before music existed

def _voice_only_job(folder, tmp_path, cfg, status, **fields):
    script = make_script(folder)
    script.pop("music")
    add_job(folder, status=status, script=script, **fields)
    voice = "sine=f=300:d=30,volume='if(lt(mod(t,3),2),0.5,0)':eval=frame"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1080x1920:r=30:d=30", "-f", "lavfi",
                    "-i", voice, "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-shortest", str(orchestrator.OUTPUT / folder / "video.mp4")], check=True)
    lib = tmp_path / "lib"
    lib.mkdir(exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "anoisesrc=d=40:a=0.3:c=pink",
                    str(lib / "bed.m4a")], check=True)
    cfg["video"]["music_dir"] = str(lib)


@ffmpeg
def test_add_music_fixes_a_ready_video_in_place(cfg, tmp_path, fixed_now):
    _voice_only_job("r1", tmp_path, cfg, "ready")
    orchestrator.add_music(cfg, "r1")
    d = orchestrator.OUTPUT / "r1"
    assert (d / "video.voice_only.mp4").exists() and qa._quiet_floor(d / "video.mp4") > -45
    assert jobs.find(jobs.load(), "r1")["music"] == "bed.m4a" and jobs.script_of({"folder": "r1"})["music"] == "bed.m4a"


@ffmpeg
def test_add_music_replaces_a_scheduled_upload_only_when_asked_and_safe(cfg, tmp_path, fixed_now, monkeypatch):
    from pipeline import channel
    deleted, published = [], []
    monkeypatch.setattr(channel, "delete_video", deleted.append)
    monkeypatch.setattr(orchestrator, "publish", lambda c, f, force=False: published.append(f))
    _voice_only_job("s1", tmp_path, cfg, "scheduled", youtube_id="old1", publish_at="2026-10-25T09:00:00+05:30")
    _voice_only_job("s2", tmp_path, cfg, "scheduled", youtube_id="old2", publish_at="2026-10-24T10:10:00+05:30")
    orchestrator.add_music(cfg, "s1")  # no --replace: fixed locally only
    assert deleted == [] and jobs.find(jobs.load(), "s1")["status"] == "scheduled"
    orchestrator.add_music(cfg, "s1", replace=True)
    j = jobs.find(jobs.load(), "s1")
    assert deleted == ["old1"] and published == ["s1"]
    assert j["status"] == "ready" and j["slot"] == "2026-10-25T09:00:00+05:30" and j["replaced_youtube_id"] == "old1"
    orchestrator.add_music(cfg, "s2", replace=True)  # goes public in 10 minutes: never swapped
    assert deleted == ["old1"] and jobs.find(jobs.load(), "s2")["youtube_id"] == "old2"


def test_add_music_never_deletes_in_dry_run(cfg, fixed_now, monkeypatch):
    from pipeline import channel
    add_job("s1", status="scheduled", youtube_id="old1", publish_at="2026-10-25T09:00:00+05:30")  # has music already
    monkeypatch.setattr(channel, "delete_video", lambda v: pytest.fail("deleted in dry run"))
    monkeypatch.setattr(control, "DRY_RUN", True)
    orchestrator.add_music(cfg, "s1", replace=True)


def test_date_first_hooks_are_flagged_for_a_rewrite():
    s = make_script("Balto")
    s["scenes"][0]["text"] = "In January 1925, a diphtheria outbreak struck Nome, Alaska."
    assert virality.score(s, 40)["points"]["hook"] == 0
    s["scenes"][0]["text"] = "Only dogs could save this town. Why?"
    assert virality.score(s, 40)["points"]["hook"] == 2


# ---------------------------------------------------------------- withdraw a scheduled upload with an error

def test_withdraw_unschedules_on_youtube_and_frees_the_slot(cfg, fixed_now, monkeypatch):
    from pipeline import channel
    calls = []
    monkeypatch.setattr(channel, "unschedule", lambda vid, ai_label=True: calls.append((vid, ai_label)))
    add_job("bad", status="scheduled", youtube_id="y9", publish_at="2026-10-25T09:00:00+05:30")
    orchestrator.withdraw(cfg, "bad", "factual error")
    j = jobs.find(jobs.load(), "bad")
    assert calls == [("y9", True)] and j["status"] == "withdrawn" and j["publish_at"] is None
    assert "2026-10-25 09:00" in {f"{s:%Y-%m-%d %H:%M}" for s in jobs.free_slots(cfg, jobs.load(), 3)}


def test_withdraw_does_nothing_in_dry_run_or_for_unscheduled_jobs(cfg, fixed_now, monkeypatch):
    from pipeline import channel
    monkeypatch.setattr(channel, "unschedule", lambda *a, **k: pytest.fail("touched YouTube"))
    add_job("ready1", status="ready")
    orchestrator.withdraw(cfg, "ready1")
    add_job("s1", status="scheduled", youtube_id="y1", publish_at="2026-10-25T09:00:00+05:30")
    monkeypatch.setattr(control, "DRY_RUN", True)
    orchestrator.withdraw(cfg, "s1")
    assert jobs.find(jobs.load(), "s1")["status"] == "scheduled"


def test_unschedule_clears_publish_time_and_keeps_the_ai_label(monkeypatch):
    from pipeline import channel
    sent, state = [], {"privacyStatus": "private", "publishAt": "2026-10-27T17:30:00Z", "license": "youtube"}

    class Req:
        def __init__(self, fn):
            self.execute = fn

    class Videos:
        def list(self, part, id):
            return Req(lambda: {"items": [{"status": dict(state)}]})

        def update(self, part, body):
            sent.append(body["status"])
            state.pop("publishAt")
            return Req(lambda: {"status": {}})
    monkeypatch.setattr(channel, "_yt", lambda: type("Y", (), {"videos": lambda self: Videos()})())
    monkeypatch.setattr("time.sleep", lambda s: None)
    channel.unschedule("v1")
    assert sent[0]["publishAt"] is None and sent[0]["privacyStatus"] == "private"
    assert sent[0]["containsSyntheticMedia"] is True


# ---------------------------------------------------------------- reschedule (lower daily target, same videos)

def test_over_cap_finds_only_future_scheduled_extras(cfg, fixed_now):
    cfg["schedule"].update(videos_per_day=2, slots_ist=["05:30", "23:30"])
    add_job("p1", status="published", youtube_id="a", publish_at="2026-10-24T05:30:00+05:30")
    add_job("p2", status="published", youtube_id="b", publish_at="2026-10-24T09:00:00+05:30")
    add_job("s3", status="scheduled", youtube_id="c", publish_at="2026-10-24T15:00:00+05:30")
    add_job("s4", status="scheduled", youtube_id="d", publish_at="2026-10-25T05:30:00+05:30")  # within target
    assert [j["folder"] for j in orchestrator.over_cap(cfg)] == ["s3"]


def test_reschedule_moves_an_extra_to_the_next_free_slot(cfg, fixed_now, monkeypatch):
    from pipeline import channel
    cfg["schedule"].update(videos_per_day=2, slots_ist=["05:30", "23:30"])
    calls = []
    monkeypatch.setattr(channel, "reschedule", lambda vid, when, ai_label=True: calls.append((vid, f"{when:%d %H:%M}")))
    add_job("p1", status="published", youtube_id="a", publish_at="2026-10-24T05:30:00+05:30")
    add_job("p2", status="published", youtube_id="b", publish_at="2026-10-24T09:00:00+05:30")
    add_job("s3", status="scheduled", youtube_id="c", publish_at="2026-10-24T15:00:00+05:30")
    add_job("s4", status="scheduled", youtube_id="d", publish_at="2026-10-25T05:30:00+05:30")
    for j in orchestrator.over_cap(cfg):
        orchestrator.reschedule(cfg, j["folder"])
    assert calls == [("c", "25 23:30")]
    assert jobs.find(jobs.load(), "s3")["publish_at"].startswith("2026-10-25T23:30")
    assert orchestrator.over_cap(cfg) == []


def test_reschedule_never_touches_youtube_in_dry_run_or_for_past_videos(cfg, fixed_now, monkeypatch):
    from pipeline import channel
    monkeypatch.setattr(channel, "reschedule", lambda *a, **k: pytest.fail("touched YouTube"))
    add_job("past", status="scheduled", youtube_id="a", publish_at="2026-10-24T09:00:00+05:30")  # already public
    assert orchestrator.reschedule(cfg, "past") is None
    add_job("s1", status="scheduled", youtube_id="b", publish_at="2026-10-24T20:00:00+05:30")
    add_job("s2", status="scheduled", youtube_id="c", publish_at="2026-10-24T15:00:00+05:30")
    monkeypatch.setattr(control, "DRY_RUN", True)
    orchestrator.reschedule(cfg, "s1")
    assert jobs.find(jobs.load(), "s1")["publish_at"].startswith("2026-10-24T20:00")


def _fake_yt(state, sent, channel_id="UC_TEST_COSMIC"):
    class Req:
        def __init__(self, fn):
            self.execute = fn

    class Videos:
        def list(self, part, id):
            return Req(lambda: {"items": [{"status": dict(state)}]})

        def update(self, part, body):
            sent.append(body["status"])
            state["publishAt"] = body["status"]["publishAt"]
            return Req(lambda: {"status": {}})

    class Channels:
        def list(self, part, mine):
            return Req(lambda: {"items": [{"id": channel_id}]})
    return type("Y", (), {"videos": lambda self: Videos(), "channels": lambda self: Channels()})()


def test_channel_reschedule_sets_new_time_and_keeps_the_ai_label(monkeypatch):
    from pipeline import channel
    sent, state = [], {"privacyStatus": "private", "publishAt": "2026-10-29T09:30:00Z", "license": "youtube"}
    monkeypatch.setattr(channel, "_yt", lambda: _fake_yt(state, sent))
    monkeypatch.setattr("time.sleep", lambda s: None)
    channel.reschedule("v1", dt.datetime(2026, 10, 30, 23, 30, tzinfo=jobs.IST))
    assert sent[0]["publishAt"] == "2026-10-30T18:00:00Z" and sent[0]["privacyStatus"] == "private"
    assert sent[0]["containsSyntheticMedia"] is True


def test_channel_reschedule_refuses_public_videos_and_wrong_channel(monkeypatch):
    from pipeline import channel, publish_youtube
    when = dt.datetime(2026, 10, 30, 23, 30, tzinfo=jobs.IST)
    monkeypatch.setattr("time.sleep", lambda s: None)
    sent = []
    monkeypatch.setattr(channel, "_yt", lambda: _fake_yt({"privacyStatus": "public"}, sent))
    with pytest.raises(RuntimeError):
        channel.reschedule("v1", when)  # re-privating a live video would hide it
    monkeypatch.setattr(channel, "_yt", lambda: _fake_yt({"privacyStatus": "private", "publishAt": "x"}, sent,
                                                         channel_id="UC_SOMEONE_ELSE"))
    with pytest.raises(publish_youtube.ChannelMismatch):
        channel.reschedule("v1", when)
    assert sent == []
