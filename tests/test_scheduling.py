import datetime as dt

from pipeline import jobs
from tests.conftest import add_job


def test_three_slots_per_day_from_empty(cfg, fixed_now):
    free = jobs.free_slots(cfg, [], 1)
    # 10:00 now + 45 min lead → today's 09:00 is gone; 15:00 and 20:00 remain; tomorrow has all three
    assert [f"{s:%d %H:%M}" for s in free] == ["24 15:00", "24 20:00", "25 09:00", "25 15:00", "25 20:00"]


def test_never_more_than_three_a_day_and_respects_gap(cfg, fixed_now):
    add_job("a", status="scheduled", publish_at="2026-10-25T18:00:00+05:30", youtube_id="x")
    free = jobs.free_slots(cfg, jobs.load(), 1)
    sun = [s for s in free if s.date() == dt.date(2026, 10, 25)]
    # 20:00 is only 2h after 18:00 (allowed at min_gap 2); 3 per day total → 2 more slots on Sunday
    assert len(sun) == 2
    cfg["schedule"]["min_gap_hours"] = 3
    sun = [s for s in jobs.free_slots(cfg, jobs.load(), 1) if s.date() == dt.date(2026, 10, 25)]
    assert [f"{s:%H:%M}" for s in sun] == ["09:00", "15:00"]


def test_ready_jobs_hold_their_planned_slot(cfg, fixed_now):
    add_job("a", status="ready", slot="2026-10-24T15:00:00+05:30")
    free = jobs.free_slots(cfg, jobs.load(), 0)
    assert all(f"{s:%H:%M}" != "15:00" for s in free)


def test_deleted_and_quarantined_jobs_free_their_slot(cfg, fixed_now):
    add_job("a", status="deleted", publish_at="2026-10-24T15:00:00+05:30")
    add_job("b", status="quarantined", slot="2026-10-24T20:00:00+05:30")
    assert [f"{s:%H:%M}" for s in jobs.free_slots(cfg, jobs.load(), 0)] == ["15:00", "20:00"]


def test_next_slot_keeps_preferred_if_free(cfg, fixed_now):
    assert jobs.next_slot(cfg, [], "2026-10-25T15:00:00+05:30") == dt.datetime(2026, 10, 25, 15, tzinfo=jobs.IST)
    # a preferred slot in the past falls back to the earliest free slot
    assert f"{jobs.next_slot(cfg, [], '2026-10-24T09:00:00+05:30'):%d %H:%M}" == "24 15:00"


def test_roles_follow_slot_order(cfg):
    day = dt.date(2026, 10, 26)
    roles = [jobs.role_for(cfg, dt.datetime.combine(day, dt.time(h), tzinfo=jobs.IST)) for h in (9, 15, 20)]
    assert roles == ["discovery", "authority", "retention"]


def test_legacy_records_get_a_status(cfg):
    jobs.save([{"folder": "old", "topic": "t", "youtube_id": "v1"}, {"folder": "old2", "topic": "t"},
               {"folder": "old3", "topic": "t", "youtube_id": "v3", "deleted": True}])
    assert [j["status"] for j in jobs.load()] == ["scheduled", "ready", "deleted"]


def test_daily_counts_distinguish_states(cfg, fixed_now):
    add_job("a", status="published", publish_at="2026-10-24T09:00:00+05:30", youtube_id="1")
    add_job("b", status="scheduled", publish_at="2026-10-24T15:00:00+05:30", youtube_id="2")
    add_job("c", status="ready", slot="2026-10-24T20:00:00+05:30")
    assert jobs.counts_for_day(jobs.load(), dt.date(2026, 10, 24)) == \
        {"published": 1, "scheduled": 1, "planned_or_ready": 1, "awaiting_approval": 0}


def test_two_slots_rotate_through_all_three_roles(cfg):
    # 2 a day with 3 roles: the sequence carries across days, so every series still gets slots
    cfg["schedule"].update(videos_per_day=2, slots_ist=["05:30", "23:30"])
    seq = [jobs.role_for(cfg, dt.datetime.combine(dt.date(2026, 10, 26) + dt.timedelta(days=d), dt.time(h, 30),
                                                  tzinfo=jobs.IST)) for d in range(3) for h in (5, 23)]
    assert sorted(set(seq)) == ["authority", "discovery", "retention"]
    assert all(seq[i] != seq[i + 1] for i in range(len(seq) - 1))


def test_lower_daily_target_caps_new_slots(cfg, fixed_now):
    cfg["schedule"].update(videos_per_day=2, slots_ist=["05:30", "23:30"])
    add_job("a", status="published", publish_at="2026-10-24T05:30:00+05:30", youtube_id="p")
    add_job("b", status="published", publish_at="2026-10-24T09:00:00+05:30", youtube_id="q")  # old slot time
    free = jobs.free_slots(cfg, jobs.load(), 1)
    assert [f"{s:%d %H:%M}" for s in free] == ["25 05:30", "25 23:30"]  # today is already full


def test_calendar_files_follow_the_active_channel(cfg, fixed_now, sandbox):
    from pipeline import analytics, calendar_plan
    entries = calendar_plan.build(cfg, days=2)
    assert len(entries) == 5  # Sat 15:00 + 20:00, Sun ×3 (the fixture's 3 slots)
    assert (jobs.HISTORY.parent / "calendar.json").exists() and (analytics.REPORTS / "CONTENT_CALENDAR.md").exists()
    cfg["schedule"].pop("slots_ist")
    cfg["schedule"]["slots"] = ["05:30", "23:30"]  # clip-style key is honoured, not replaced by defaults
    assert [e["slot"][11:16] for e in calendar_plan.build(cfg, days=2)] == ["23:30", "05:30", "23:30"]
