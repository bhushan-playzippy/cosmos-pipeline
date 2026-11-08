"""Publishing: duplicate guard, retries, quota handling, verification, dry-run, idempotent catch-up, status sync."""
import types

import pytest
from googleapiclient.errors import HttpError

from pipeline import control, jobs, orchestrator, publish_youtube
from tests.conftest import TEST_CHANNEL, add_job, make_script


def http_error(status, msg="err"):
    return HttpError(types.SimpleNamespace(status=status, reason=msg), msg.encode())


class Exec:
    def __init__(self, value):
        self.value = value

    def execute(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeYT:
    """Just enough of the YouTube client for upload(): uploads playlist, insert, list, update."""

    def __init__(self, existing_titles=(), synthetic_sticks=True, channel_id=TEST_CHANNEL):
        self.channel_id = channel_id
        self.existing = list(existing_titles)
        self.inserted, self.updated = [], []
        self.synthetic_sticks = synthetic_sticks

    def channels(self):
        return types.SimpleNamespace(list=lambda **k: Exec(
            {"items": [{"id": self.channel_id, "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]}))

    def playlistItems(self):
        items = [{"snippet": {"title": t, "resourceId": {"videoId": f"old{i}"}}} for i, t in enumerate(self.existing)]
        return types.SimpleNamespace(list=lambda **k: Exec({"items": items}))

    def videos(self):
        yt = self

        class V:
            def insert(self, part, body, media_body):
                yt.inserted.append(body)
                return types.SimpleNamespace(next_chunk=lambda: (None, {"id": "NEW1"}))

            def list(self, part, id):
                return Exec({"items": [{"id": id, "status": {"uploadStatus": "uploaded", "privacyStatus": "private",
                                                             "publishAt": "2026-10-25T03:30:00Z"}}]})

            def update(self, part, body):
                yt.updated.append(body)
                st = dict(body["status"], uploadStatus="uploaded")
                if not yt.synthetic_sticks:
                    st.pop("containsSyntheticMedia")
                return Exec({"status": st})
        return V()


@pytest.fixture
def fake_yt(monkeypatch):
    yt = FakeYT()
    monkeypatch.setattr("googleapiclient.discovery.build", lambda *a, **k: yt)
    monkeypatch.setattr(publish_youtube, "_creds", lambda: None)
    monkeypatch.setattr("googleapiclient.http.MediaFileUpload", lambda *a, **k: None)
    return yt


def test_upload_sets_ai_flag_after_insert_and_verifies(cfg, fake_yt, fixed_now):
    vid, ver = publish_youtube.upload("v.mp4", make_script("Moon"), cfg, fixed_now)
    assert vid == "NEW1" and ver["ok"]
    assert fake_yt.updated[0]["status"]["containsSyntheticMedia"] is True
    assert fake_yt.inserted[0]["snippet"]["title"].endswith("#Shorts")


def test_verification_flags_missing_ai_flag(cfg, fake_yt, fixed_now):
    fake_yt.synthetic_sticks = False
    _, ver = publish_youtube.upload("v.mp4", make_script("Moon"), cfg, fixed_now)
    assert not ver["ok"] and "AI disclosure not set" in ver["problems"]


def test_duplicate_title_is_not_uploaded_again(cfg, fake_yt, fixed_now):
    fake_yt.existing = ["Moon explained #Shorts"]
    vid, _ = publish_youtube.upload("v.mp4", make_script("Moon"), cfg, fixed_now)
    assert vid == "old0" and fake_yt.inserted == []


def test_dry_run_never_calls_youtube(cfg, fake_yt, monkeypatch, fixed_now):
    monkeypatch.setattr(control, "DRY_RUN", True)
    vid, ver = publish_youtube.upload("v.mp4", make_script("Moon"), cfg, fixed_now)
    assert vid is None and ver.get("dry_run") and fake_yt.inserted == []


def test_retry_recovers_from_transient_5xx(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise http_error(503)
        return "ok"
    assert publish_youtube.with_retry(flaky, "t") == "ok" and calls["n"] == 3


def test_retry_gives_up_after_limit(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    with pytest.raises(HttpError):
        publish_youtube.with_retry(lambda: (_ for _ in ()).throw(http_error(500)), "t", tries=3)


def test_quota_error_is_not_retried():
    with pytest.raises(publish_youtube.QuotaExceeded):
        publish_youtube.with_retry(lambda: (_ for _ in ()).throw(http_error(403, "quotaExceeded")), "t")


def test_client_errors_are_not_retried(monkeypatch):
    calls = {"n": 0}

    def bad():
        calls["n"] += 1
        raise http_error(400)
    with pytest.raises(HttpError):
        publish_youtube.with_retry(bad, "t")
    assert calls["n"] == 1


def test_expired_login_gives_actionable_error(monkeypatch, tmp_path):
    from google.auth.exceptions import RefreshError
    import json
    tok = tmp_path / "tok.json"
    tok.write_text(json.dumps({"token": "x", "refresh_token": "r", "client_id": "c", "client_secret": "s",
                               "expiry": "2000-01-01T00:00:00Z"}))
    monkeypatch.setattr(publish_youtube, "TOKEN", tok)
    monkeypatch.delenv("YOUTUBE_TOKEN_JSON", raising=False)
    monkeypatch.setattr("google.oauth2.credentials.Credentials.refresh",
                        lambda self, req: (_ for _ in ()).throw(RefreshError("invalid_grant")))
    with pytest.raises(SystemExit, match="auth-youtube"):
        publish_youtube._creds()


# ---------------------------------------------------------------- orchestrator publishing

@pytest.fixture
def fake_upload(monkeypatch):
    calls = []

    def up(path, script, cfg, slot, on_insert=None):
        if on_insert:
            on_insert()
        calls.append((str(path), slot))
        control.count("uploads")
        return f"VID{len(calls)}", {"ok": True, "problems": []}
    monkeypatch.setattr(publish_youtube, "upload", up)
    monkeypatch.setattr(orchestrator, "OUTPUT", jobs.OUTPUT)
    return calls


def test_upload_pending_is_idempotent(cfg, fake_upload, fixed_now):
    cfg["youtube"]["playlist"] = None
    add_job("a")
    add_job("b")
    orchestrator.upload_pending(cfg)
    orchestrator.upload_pending(cfg)  # second run must not re-upload
    assert len(fake_upload) == 2
    assert {j["status"] for j in jobs.load()} == {"scheduled"}
    slots = sorted(j["publish_at"] for j in jobs.load())
    assert slots[0] != slots[1]


def test_quarantined_failed_and_dry_run_jobs_never_upload(cfg, fake_upload, fixed_now):
    cfg["youtube"]["playlist"] = None
    for s in ("quarantined", "failed", "dry_run"):
        add_job(s, status=s)
    orchestrator.upload_pending(cfg)
    assert fake_upload == []


def test_upload_limit_per_run(cfg, fake_upload, fixed_now):
    cfg["youtube"]["playlist"] = None
    cfg["control"]["max_uploads_per_run"] = 2
    for f in "abcd":
        add_job(f)
    orchestrator.upload_pending(cfg)
    assert len(fake_upload) == 2
    assert sum(1 for j in jobs.load() if j["status"] == "ready") == 2


def test_quota_exhaustion_leaves_rest_queued(cfg, monkeypatch, fixed_now):
    cfg["youtube"]["playlist"] = None
    add_job("a")
    add_job("b")
    monkeypatch.setattr(publish_youtube, "upload",
                        lambda *a, **k: (_ for _ in ()).throw(publish_youtube.QuotaExceeded("quota")))
    orchestrator.upload_pending(cfg)
    assert {j["status"] for j in jobs.load()} == {"ready"}


def test_kill_switch_blocks_daily(cfg, fake_upload, sandbox, fixed_now):
    add_job("a")
    (sandbox / "STOP").write_text("")
    orchestrator.daily(cfg)
    assert fake_upload == [] and jobs.load()[0]["status"] == "ready"


def test_sync_confirms_published_and_flags_stuck_private(cfg, monkeypatch, fixed_now):
    add_job("pub", status="scheduled", youtube_id="P", publish_at="2026-10-24T09:00:00+05:30")
    add_job("stuck", status="scheduled", youtube_id="S", publish_at="2026-10-24T08:00:00+05:30")
    add_job("gone", status="scheduled", youtube_id="G", publish_at="2026-10-25T09:00:00+05:30")
    from pipeline import channel
    monkeypatch.setattr(channel, "video_status", lambda ids: [
        {"id": "P", "status": {"privacyStatus": "public"}}, {"id": "S", "status": {"privacyStatus": "private"}}])
    orchestrator.sync_status(cfg)
    by = {j["folder"]: j for j in jobs.load()}
    assert by["pub"]["status"] == "published"
    assert by["stuck"]["status"] == "scheduled" and "still private" in by["stuck"]["alert"]
    assert by["gone"]["status"] == "deleted"


def test_daily_upload_cap_is_a_quota_stop_and_job_stays_ready(cfg, monkeypatch, fixed_now):
    cfg["youtube"]["playlist"] = None
    add_job("a")

    def capped(path, script, cfg, slot, on_insert=None):
        on_insert()
        raise publish_youtube.QuotaExceeded("uploadLimitExceeded")
    monkeypatch.setattr(publish_youtube, "upload", capped)
    orchestrator.upload_pending(cfg)
    j = jobs.load()[0]
    assert j["status"] == "ready" and not j.get("alert")


def test_upload_limit_400_is_recognised(monkeypatch):
    with pytest.raises(publish_youtube.QuotaExceeded):
        publish_youtube.with_retry(lambda: (_ for _ in ()).throw(http_error(400, "uploadLimitExceeded")), "t")


def test_phone_alert_posts_to_ntfy_topic_and_never_in_dry_run(cfg, monkeypatch):
    import subprocess
    import urllib.request
    sent = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout: sent.append(req) or types.SimpleNamespace(close=lambda: None))
    orchestrator.alert(cfg, "1 slot has no video")
    assert sent == []  # no NTFY_TOPIC: desktop banner only
    monkeypatch.setenv("NTFY_TOPIC", "secret-topic")
    orchestrator.alert(cfg, "1 slot has no video")
    assert sent[0].full_url == "https://ntfy.sh/secret-topic" and sent[0].data == b"1 slot has no video"
    monkeypatch.setattr(control, "DRY_RUN", True)
    orchestrator.alert(cfg, "dry")
    assert len(sent) == 1
    monkeypatch.setattr(control, "DRY_RUN", False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("offline")))
    orchestrator.alert(cfg, "offline is not fatal")
