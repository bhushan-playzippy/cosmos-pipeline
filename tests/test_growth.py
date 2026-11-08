"""Growth agent: views/day normalisation, small-sample guard, slot advice, bounded type preference."""
import csv

from pipeline import analytics, growth, jobs
from tests.conftest import add_job


def _stats(sandbox, rows):
    path = analytics.ADIR / "videos.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["date", "video_id", "folder", "role", "slot", "privacy", "views", "likes",
                                          "comments"])
        w.writeheader()
        w.writerows(rows)


def _cfg(cfg, sandbox):
    cfg["_paths"] = {"data": sandbox / "data"}
    cfg["analytics"]["min_sample"] = 3
    return cfg


def test_too_little_data_changes_nothing(cfg, sandbox, fixed_now):
    cfg = _cfg(cfg, sandbox)
    add_job("a", status="published", publish_at="2026-10-20T09:00:00+05:30", role="funny")
    _stats(sandbox, [{"date": "2026-10-24", "video_id": "A", "folder": "a", "role": "funny", "slot": "09:00",
                      "privacy": "public", "views": 900, "likes": 0, "comments": 0}])
    g = growth.analyse(cfg)
    assert g["type_boost"] == {} and "too early" in g["recommendations"][-1]


def test_views_per_day_drives_boost_and_slot_advice(cfg, sandbox, fixed_now):
    cfg = _cfg(cfg, sandbox)
    rows = []
    for i in range(3):  # 'funny' at 09:00: 3000 views over 3 days = 1000/day
        add_job(f"f{i}", status="published", publish_at="2026-10-21T09:00:00+05:30", role="funny")
        rows.append({"date": "2026-10-24", "video_id": f"F{i}", "folder": f"f{i}", "role": "funny", "slot": "09:00",
                     "privacy": "public", "views": 3000, "likes": 0, "comments": 0})
    for i in range(3):  # 'story' at 23:00: 3000 views but over 30 days = 100/day (older, not better)
        add_job(f"s{i}", status="published", publish_at="2026-09-24T23:00:00+05:30", role="story")
        rows.append({"date": "2026-10-24", "video_id": f"S{i}", "folder": f"s{i}", "role": "story", "slot": "23:00",
                     "privacy": "public", "views": 3000, "likes": 0, "comments": 0})
    _stats(sandbox, rows)
    g = growth.analyse(cfg)
    assert g["type_boost"]["funny"] == 1.3 and g["type_boost"]["story"] == 0.7  # bounded
    assert any("Slot 23:00" in r for r in g["recommendations"])
    assert growth.type_boost(cfg) == g["type_boost"]
    assert jobs.load()
