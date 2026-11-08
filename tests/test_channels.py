"""Multi-channel isolation: profiles, identity checks, credentials, queues, kill switches, locks, recovery."""
import datetime as dt
import json
import re

import pytest

from pipeline import analytics, channels, control, jobs, orchestrator, publish_youtube, util
from tests.conftest import TEST_CHANNEL, add_job, make_script
from tests.test_publishing import FakeYT

CLIPS_ID = "UCPzixXDunk2uJUqLp-ndLVA"


def clips_text(channel_id=None):
    """The real clips profile, optionally with its linked channel id replaced (or blanked with "")."""
    text = (channels.PROFILES / "clips.yaml").read_text()
    if channel_id is not None:
        text = re.sub(r'(\n  youtube_channel_id: )"[^"]*"', rf'\1"{channel_id}"', text, count=1)
    return text


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """channels.paths() resolves under a temp project, so activating a profile never touches real files."""
    monkeypatch.setattr(channels, "DATA", tmp_path / "proj" / "data")
    monkeypatch.setattr(channels, "OUTPUT", tmp_path / "proj" / "output")
    monkeypatch.setattr(channels, "ROOT", tmp_path / "proj")
    return tmp_path / "proj"


def test_original_channel_keeps_its_settings_and_locations(roots):
    cfg = channels.load("cosmic")
    p = cfg["_paths"]
    assert cfg["schedule"]["videos_per_day"] == 2  # owner 2026-10-29: 2 a day (was 3)
    assert len(cfg["schedule"]["slots_ist"]) == 2
    assert cfg["review"]["human_approval"] is False  # owner's choice (2026-10-25): the gates approve
    assert p["data"] == roots / "data" and p["output"] == roots / "output"
    assert p["token"] == roots / "youtube_token.json" and p["token_env"] == "YOUTUBE_TOKEN_JSON"
    assert cfg["channel"]["youtube_channel_id"] == "UCS3WDSezET11wMQjfWuc2OQ"


def test_clip_channel_is_configured_independently(roots):
    cfg = channels.load("clips")
    p = cfg["_paths"]
    assert cfg["schedule"]["videos_per_day"] == 2 and len(cfg["schedule"]["slots"]) == 2  # owner 2026-10-29 (was 5)
    assert p["data"] == roots / "data" / "channels" / "clips"
    assert p["token"] == roots / "credentials" / "clips" / "youtube_token.json"
    assert p["token_env"] == "YOUTUBE_TOKEN_JSON_CLIPS"
    assert cfg["channel"]["youtube_channel_id"] == CLIPS_ID and cfg["channel"]["name"] == "Unbelievably True"
    assert cfg["channel"]["type"] == "generated" and cfg["writer"]["template"] == "story"
    assert cfg["review"]["human_approval"] is False and cfg["youtube"]["privacy"] == "private"
    assert cfg["video"]["max_seconds"] < 60  # claimed Shorts over 1 minute get blocked


def test_real_profiles_share_no_identity_or_credentials():
    channels.validate_all()


def test_shared_identity_between_profiles_is_refused(tmp_path, monkeypatch):
    import shutil
    prof = tmp_path / "channels"
    prof.mkdir()
    text = clips_text()
    (prof / "clips.yaml").write_text(clips_text("UCS3WDSezET11wMQjfWuc2OQ"))
    monkeypatch.setattr(channels, "PROFILES", prof)
    with pytest.raises(channels.ProfileError, match="share the same youtube_channel_id"):
        channels.validate_all()
    (prof / "clips.yaml").write_text(text.replace("credentials:\n", "credentials:\n  token_env: YOUTUBE_TOKEN_JSON\n"))
    with pytest.raises(channels.ProfileError, match="token env"):
        channels.validate_all()
    shutil.rmtree(prof)


def test_enabling_uploads_without_channel_id_is_invalid(tmp_path, monkeypatch):
    prof = tmp_path / "channels"
    prof.mkdir()
    (prof / "clips.yaml").write_text(clips_text(""))
    monkeypatch.setattr(channels, "PROFILES", prof)
    with pytest.raises(channels.ProfileError, match="youtube_channel_id is empty"):
        channels.load("clips")


def test_activate_points_every_module_at_the_channel(roots):
    cfg = channels.activate(channels.load("clips"))
    d = roots / "data" / "channels" / "clips"
    assert jobs.HISTORY == d / "history.json" and orchestrator.OUTPUT == roots / "output" / "channels" / "clips"
    assert analytics.ADIR == d / "analytics" and control.USAGE == d / "usage.csv"
    assert publish_youtube.TOKEN_ENV == "YOUTUBE_TOKEN_JSON_CLIPS" and publish_youtube.EXPECTED_CHANNEL == CLIPS_ID
    from pipeline import publish_instagram
    assert (publish_instagram.USER_ENV, publish_instagram.TOKEN_ENV) == ("IG_USER_ID_CLIPS", "IG_ACCESS_TOKEN_CLIPS")
    assert str(jobs.TZ) == "Asia/Kolkata" and util.LOG_TAG == "clips"
    assert control.CHANNEL_STOP == roots / "STOP.clips"
    jobs.upsert({"folder": "clip1", "topic": "t", "status": "ready"})
    channels.activate(channels.load("cosmic"))
    assert jobs.HISTORY == roots / "data" / "history.json"
    assert jobs.load() == []  # channel 1 never sees channel 2's queue
    assert publish_instagram.TOKEN_ENV == "IG_ACCESS_TOKEN"  # each channel posts to its own Instagram account
    assert cfg["_paths"]["data"] != roots / "data"


def test_upload_refuses_a_token_for_another_channel(cfg, monkeypatch, fixed_now):
    yt = FakeYT(channel_id="UC_SOMEONE_ELSE")
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **k: yt)
    monkeypatch.setattr(publish_youtube, "_creds", lambda: None)
    monkeypatch.setattr("googleapiclient.http.MediaFileUpload", lambda *a, **k: None)
    with pytest.raises(publish_youtube.ChannelMismatch, match="UC_SOMEONE_ELSE"):
        publish_youtube.upload("v.mp4", make_script("Moon"), cfg, fixed_now)
    assert yt.inserted == []


def test_upload_refuses_when_profile_has_no_channel_id(cfg, monkeypatch, fixed_now):
    monkeypatch.setattr(publish_youtube, "EXPECTED_CHANNEL", None)
    yt = FakeYT()
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **k: yt)
    monkeypatch.setattr(publish_youtube, "_creds", lambda: None)
    with pytest.raises(publish_youtube.ChannelMismatch, match="no channel.youtube_channel_id"):
        publish_youtube.upload("v.mp4", make_script("Moon"), cfg, fixed_now)
    assert yt.inserted == []


def test_channel_mismatch_leaves_job_ready_and_unuploaded(cfg, monkeypatch, fixed_now):
    cfg["youtube"]["playlist"] = None
    add_job("a")
    yt = FakeYT(channel_id="UC_WRONG")
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **k: yt)
    monkeypatch.setattr(publish_youtube, "_creds", lambda: None)
    with pytest.raises(SystemExit):
        orchestrator.upload_pending(cfg)
    assert jobs.load()[0]["status"] == "ready" and yt.inserted == []


def test_each_channel_reads_only_its_own_token_variable(monkeypatch):
    monkeypatch.setenv("YOUTUBE_TOKEN_JSON", json.dumps({"token": "cosmic"}))
    monkeypatch.delenv("YOUTUBE_TOKEN_JSON_TEST", raising=False)
    with pytest.raises(SystemExit, match="No YouTube token for this channel"):
        publish_youtube._creds()  # sandbox channel's variable is YOUTUBE_TOKEN_JSON_TEST, not channel 1's


def test_per_channel_kill_switch(cfg, sandbox, monkeypatch):
    monkeypatch.setattr(control, "CHANNEL_STOP", sandbox / "STOP.clips")
    assert not control.killed(cfg)
    (sandbox / "STOP.clips").write_text("")
    assert control.killed(cfg)


def test_second_run_of_same_channel_is_locked_out(roots):
    cfg = channels.load("clips")
    with channels.lock(cfg):
        with pytest.raises(SystemExit, match="in progress"):
            with channels.lock(cfg):
                pass
        with channels.lock(channels.load("cosmic")):  # a different channel may run at the same time
            pass


def test_daily_report_uses_only_this_channels_usage(cfg, sandbox, monkeypatch):
    monkeypatch.setattr(control, "USAGE", sandbox / "data" / "mine.csv")
    control.write_usage("daily")
    path = analytics.write_daily_report(cfg)
    assert "command `daily`" in path.read_text()


# ---------------------------------------------------------------- state safety and upload recovery

def test_corrupt_queue_stops_the_run_instead_of_reading_as_empty(sandbox):
    jobs.upsert({"folder": "a", "topic": "t", "status": "scheduled"})
    jobs.HISTORY.write_text("{ half a file")
    with pytest.raises(util.CorruptState):
        jobs.load()


def test_writes_are_atomic_and_keep_a_backup(sandbox):
    jobs.upsert({"folder": "a", "topic": "t", "status": "ready"})
    jobs.upsert({"folder": "b", "topic": "t", "status": "ready"})
    assert json.loads(jobs.HISTORY.with_name("history.json.bak").read_text())[0]["folder"] == "a"
    assert not jobs.HISTORY.with_name("history.json.tmp").exists()


def _crashing_upload(path, script, cfg, slot, on_insert=None):
    on_insert()
    raise ConnectionError("network dropped mid-upload")


def test_interrupted_upload_is_reconciled_not_reuploaded(cfg, monkeypatch, fixed_now):
    cfg["youtube"]["playlist"] = None
    add_job("a")
    monkeypatch.setattr(publish_youtube, "upload", _crashing_upload)
    with pytest.raises(ConnectionError):
        orchestrator.publish(cfg, "a")
    j = jobs.load()[0]
    assert j["status"] == "uploading" and "unknown" in j["alert"] and j["slot"]
    # next run: YouTube has it → adopted, and no second insert happens
    inserts = []
    monkeypatch.setattr(publish_youtube, "upload", lambda *a, **k: inserts.append(1))
    monkeypatch.setattr(publish_youtube, "reconcile", lambda s, c: ("VID_A", {"ok": True}))
    orchestrator.upload_pending(cfg)
    j = jobs.load()[0]
    assert j["status"] == "scheduled" and j["youtube_id"] == "VID_A" and inserts == [] and not j.get("alert")


def test_interrupted_upload_not_on_youtube_retries_only_after_wait(cfg, monkeypatch, fixed_now):
    cfg["youtube"]["playlist"] = None
    add_job("a")
    monkeypatch.setattr(publish_youtube, "upload", _crashing_upload)
    with pytest.raises(ConnectionError):
        orchestrator.publish(cfg, "a")
    monkeypatch.setattr(publish_youtube, "reconcile", lambda s, c: (None, None))
    uploads = []
    monkeypatch.setattr(publish_youtube, "upload", lambda p, s, c, slot, on_insert=None:
                        uploads.append(1) or ("VID_NEW", {"ok": True}))
    orchestrator.upload_pending(cfg)  # too soon: might still be processing on YouTube's side
    assert jobs.load()[0]["status"] == "uploading" and uploads == []
    monkeypatch.setattr(jobs, "now", lambda: fixed_now + dt.timedelta(hours=1))
    orchestrator.upload_pending(cfg)
    assert jobs.load()[0]["status"] == "scheduled" and uploads == [1]


def test_title_collision_with_another_job_is_quarantined(cfg, monkeypatch, fixed_now):
    cfg["youtube"]["playlist"] = None
    add_job("old", status="scheduled", youtube_id="VID_OLD", publish_at="2026-10-25T09:00:00+05:30")
    add_job("new")
    monkeypatch.setattr(publish_youtube, "upload", lambda *a, **k: ("VID_OLD", {"ok": True}))
    orchestrator.publish(cfg, "new")
    j = jobs.find(jobs.load(), "new")
    assert j["status"] == "quarantined" and "collides" in j["reason"] and not j.get("youtube_id")


def test_logs_and_events_never_contain_tokens(capsys, sandbox):
    util.log('refresh failed: {"refresh_token": "1//0gAbcdefghijklmnopqrstuvwxyz"} ya29.secretAccessToken')
    util.event("x", detail="token ya29.abcdefghij")
    out = capsys.readouterr().out + (sandbox / "data" / "events.jsonl").read_text()
    assert "1//0gAbc" not in out and "ya29.secret" not in out and "ya29.abc" not in out
    assert "[redacted]" in out


def test_daily_target_counts_only_confirmed_publications(cfg, fixed_now):
    day = dt.date(2026, 10, 25)
    add_job("p", status="published", publish_at="2026-10-25T09:00:00+05:30")
    add_job("s", status="scheduled", publish_at="2026-10-25T15:00:00+05:30")
    add_job("u", status="uploading", slot="2026-10-25T18:00:00+05:30")
    c = jobs.counts_for_day(jobs.load(), day)
    assert c["published"] == 1 and c["scheduled"] == 1
    assert TEST_CHANNEL  # (sandbox identity in use)


def test_first_login_links_the_new_channel_into_its_profile(tmp_path, monkeypatch):
    prof = tmp_path / "channels"
    prof.mkdir()
    (prof / "clips.yaml").write_text(clips_text("").replace("  enabled: true ", "  enabled: false "))
    monkeypatch.setattr(channels, "PROFILES", prof)
    monkeypatch.setattr(channels, "ACTIVE", "clips")
    assert publish_youtube.link_profile("UC_NEW_CLIPS")
    assert channels.load("clips")["channel"]["youtube_channel_id"] == "UC_NEW_CLIPS"
    assert not publish_youtube.link_profile("UC_OTHER")  # never overwrites a linked channel
    monkeypatch.setattr(channels, "ACTIVE", "cosmic")
    assert not publish_youtube.link_profile("UC_X")       # channel 1's config.yaml is never edited
