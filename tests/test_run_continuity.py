"""Run continuity (pipeline/runs.py): interrupted runs, missed scheduled runs, wake/login catch-ups, offline
starts. Network probes, sleeps and the daily steps are faked; nothing leaves the sandbox."""
import datetime as dt

from pipeline import control, jobs, orchestrator, runs
from pipeline.util import read_json, write_json
from tests.conftest import add_job


def _steps(monkeypatch):
    ran = []
    monkeypatch.setattr(orchestrator, "_daily_steps", lambda cfg: ran.append(1))
    return ran


def test_interrupted_run_is_recorded_and_its_placeholder_explained(cfg, monkeypatch, fixed_now):
    started = fixed_now - dt.timedelta(hours=20)
    write_json(runs._file(), {"current": {"started": started.isoformat(), "finished": None}})
    add_job("half-made", status="failed", created=(started + dt.timedelta(minutes=5)).isoformat(), stage="script")
    add_job("older", status="failed", created=(started - dt.timedelta(days=1)).isoformat())
    ran = _steps(monkeypatch)
    orchestrator.daily(cfg)
    by = {j["folder"]: j for j in jobs.load()}
    assert by["half-made"]["reason"].startswith("interrupted") and by["half-made"]["status"] == "failed"
    assert not by["older"].get("reason")                       # only that run's placeholders
    assert ran == [1] and read_json(runs._file(), {})["current"]["outcome"] == "ok"


def test_missed_scheduled_runs_are_listed(cfg, fixed_now):
    cfg["control"]["run_times_ist"] = ["13:35", "21:00"]
    prev = {"current": {"started": (fixed_now - dt.timedelta(days=2)).isoformat()}}   # Thu 10:00 → Sat 10:00
    assert runs.missed(cfg, prev, fixed_now) == ["2026-10-22T13:35+05:30", "2026-10-22T21:00+05:30",
                                                  "2026-10-23T13:35+05:30", "2026-10-23T21:00+05:30"]
    assert runs.missed(cfg, {}, fixed_now) == []               # first run ever: nothing to compare


def test_catch_up_right_after_a_completed_run_is_skipped_unless_forced(cfg, monkeypatch, fixed_now):
    write_json(runs._file(), {"last_ok": (fixed_now - dt.timedelta(minutes=10)).isoformat(),
                              "current": {"started": "x", "finished": "y"}})
    ran = _steps(monkeypatch)
    orchestrator.daily(cfg)
    assert ran == [] and read_json(runs._file(), {})["current"]["outcome"] == "skipped_recent"
    orchestrator.daily(cfg, force=True)
    assert ran == [1]


def test_offline_start_is_bounded_and_skips_the_run(cfg, monkeypatch, fixed_now):
    probes, sleeps = [], []
    monkeypatch.setattr(runs, "online", lambda *a, **k: probes.append(1) or False)
    monkeypatch.setattr(runs.time, "sleep", lambda s: sleeps.append(s))
    ran = _steps(monkeypatch)
    cfg["control"].update(network_checks=3, network_check_seconds=60)
    orchestrator.daily(cfg)
    assert len(probes) == 3 and sleeps == [60, 60] and ran == []    # no endless retry loop
    assert read_json(runs._file(), {})["current"]["outcome"] == "offline"


def test_network_back_within_the_checks_runs_normally(cfg, monkeypatch, fixed_now):
    answers = iter([False, True])
    monkeypatch.setattr(runs, "online", lambda *a, **k: next(answers))
    monkeypatch.setattr(runs.time, "sleep", lambda s: None)
    ran = _steps(monkeypatch)
    orchestrator.daily(cfg)
    assert ran == [1]


def test_stop_and_dry_run_leave_no_run_record(cfg, monkeypatch, sandbox, fixed_now):
    ran = _steps(monkeypatch)
    (sandbox / "STOP").write_text("")
    orchestrator.daily(cfg)
    assert ran == [] and not runs._file().exists()
    (sandbox / "STOP").unlink()
    monkeypatch.setattr(control, "DRY_RUN", True)
    orchestrator.daily(cfg)
    assert ran == [1] and not runs._file().exists()           # a rehearsal never counts as a completed run


def test_interrupted_run_does_not_duplicate_or_block_its_slot(cfg, monkeypatch, fixed_now):
    from tests.test_pipeline_units import _fake_production
    from pipeline import ideas
    _fake_production(monkeypatch)
    monkeypatch.setattr(ideas, "next_topic", lambda c, role=None, **k: {"topic": f"t{len(jobs.load())}", "role": role})
    cfg["schedule"]["buffer_days"] = 0
    slot = jobs.free_slots(cfg, jobs.load(), 0)[0]
    add_job("half-made", status="failed", slot=slot.isoformat())     # the placeholder an interrupted run left
    orchestrator.produce(cfg, max_videos=5)
    orchestrator.produce(cfg, max_videos=5)                         # a second (catch-up) run makes nothing new
    live = [j for j in jobs.load() if j["status"] == "ready"]
    assert len(live) == len({j["slot"] for j in live}) == 2          # one video per free slot, no duplicates


def test_run_record_follows_the_active_channel(tmp_path, monkeypatch):
    monkeypatch.setattr(jobs, "HISTORY", tmp_path / "channels" / "clips" / "history.json")
    assert runs._file() == tmp_path / "channels" / "clips" / "runs.json"
