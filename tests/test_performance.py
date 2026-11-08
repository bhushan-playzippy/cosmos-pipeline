"""Performance-review agent: fair ranking, playbook, and that the other agents actually use it."""
import datetime as dt
import json

import pytest

from pipeline import ideas, jobs, performance
from tests.conftest import add_job, make_script


def _video(vid, views, hours_ago, title):
    pub = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {"id": vid, "status": {"privacyStatus": "public"}, "snippet": {"title": title, "publishedAt": pub},
            "statistics": {"viewCount": str(views), "likeCount": "10", "commentCount": "1"}}


@pytest.fixture
def channel_stats(monkeypatch):
    vids = {}
    from pipeline import channel
    monkeypatch.setattr(channel, "video_status", lambda ids: [vids[i] for i in ids if i in vids])
    return vids


def _add(vids, folder, vid, views, hours, title, pillar, hook):
    s = make_script(folder)
    s["youtube"]["title"] = f"{title} #Shorts"
    s["scenes"][0]["text"] = hook
    s["plan"] = {"pillar": pillar, "role": "discovery"}
    add_job(folder, status="published", youtube_id=vid, script=s, publish_at="2026-10-24T09:00:00+05:30",
            role="discovery")
    vids[vid] = _video(vid, views, hours, title)


def test_ranking_is_fair_across_ages(cfg, channel_stats):
    _add(channel_stats, "old", "O", 2000, 100, "Old video", "A", "old hook")     # 20 views/h
    _add(channel_stats, "new", "N", 600, 10, "New video", "B", "new hook")       # 60 views/h
    pb = performance.analyse(cfg, force=True)
    assert [r["folder"] for r in pb["ranking"]] == ["new", "old"]  # fewer views, but faster


def test_playbook_examples_patterns_and_weights(cfg, channel_stats):
    cfg["analytics"]["min_sample"] = 3
    for i in range(3):
        _add(channel_stats, f"w{i}", f"W{i}", 3000, 10, f"7 Tons in 1 Spoon {i}", "Scale", f"One spoon weighs tons {i}?")
        _add(channel_stats, f"l{i}", f"L{i}", 50, 10, f"A long meandering title about something {'xyz'[i]} that never ends",
             "Missions", f"Today we will talk about a long list of things in great detail, part {'xyz'[i]}")
    pb = performance.analyse(cfg, force=True)
    assert pb["leaders"] and pb["leaders"][0]["pillar"] == "Scale"
    assert pb["laggards"] and pb["laggards"][0]["pillar"] == "Missions"
    assert pb["pillar_weight"]["Scale"] > 1 > pb["pillar_weight"]["Missions"]
    assert any("hook length" in p for p in pb["patterns"]) and any("number in the title" in p for p in pb["patterns"])
    g = performance.guidance(cfg)
    assert "WINNER" in g and "WEAK" in g
    assert performance.topic_weight({"pillar": "Scale", "role": "discovery"}) > \
        performance.topic_weight({"pillar": "Missions", "role": "discovery"})


def test_small_samples_never_move_weights(cfg, channel_stats):
    _add(channel_stats, "a", "A1", 5000, 10, "Lucky one", "Scale", "hook")
    pb = performance.analyse(cfg, force=True)
    assert pb["pillar_weight"] == {} and "patterns appear from 6" in pb["patterns"][0]


def test_topic_picker_follows_the_playbook(cfg, channel_stats):
    cfg["analytics"]["min_sample"] = 3
    for i in range(3):
        _add(channel_stats, f"w{i}", f"W{i}", 3000, 10, f"Win {i}", "Scale", "h?")
        _add(channel_stats, f"l{i}", f"L{i}", 50, 10, f"Lose {i}", "Missions", "h")
    performance.analyse(cfg, force=True)
    ideas.BACKLOG.write_text(json.dumps([
        {"topic": "Mission topic slightly higher score", "pillar": "Missions", "role": "discovery", "score": 40},
        {"topic": "Scale topic", "pillar": "Scale", "role": "discovery", "score": 36}] + [
        {"topic": f"filler {i} zeta", "pillar": "X", "role": "authority", "score": 1} for i in range(10)]))
    assert ideas.next_topic(cfg, "discovery")["topic"] == "Scale topic"


def test_review_is_cached_for_every_days(cfg, channel_stats):
    cfg["performance"] = {"every_days": 2}
    _add(channel_stats, "a", "A1", 100, 10, "A", "Scale", "h")
    first = performance.analyse(cfg, force=True)
    channel_stats.clear()
    assert performance.analyse(cfg) == first  # within 2 days: no new API calls, same playbook
