"""Virality agent (pre-publish) and gap finder (post-publish)."""
import datetime as dt

from pipeline import gaps, virality
from tests.conftest import add_job, make_script


def test_hashtags_are_enforced_on_the_last_line(cfg):
    s = make_script("Titanic")
    s["youtube"]["description"] = "Story.\n\nSources: Britannica\n#titanic #History #history #a #b #c #d"
    s["hashtags"] = ["#Titanic"]
    cfg["packaging"] = {"hashtags": ["#TrueStory"]}
    tags = virality.ensure_hashtags(s, cfg)
    last = s["youtube"]["description"].split("\n")[-1]
    assert tags[0] == "#Shorts" and last == " ".join(tags) and len(tags) <= 5
    assert [t.lower() for t in tags].count("#history") == 1 and "#Shorts" not in s["youtube"]["description"][:-len(last)]


def test_virality_score_rewards_a_tight_hook_and_loop():
    s = make_script("x")
    s["scenes"] = [{"text": "One teaspoon of this star weighs a billion tons."}] + \
                  [{"text": f"filler line {i}"} for i in range(5)] + [{"text": "All of that in one teaspoon of star."}]
    s["youtube"]["title"] = "One Teaspoon Weighs 1 Billion Tons #Shorts"
    s["youtube"]["description"] = "d\n\n#Shorts #Space #Science"
    good = virality.score(s, 40)
    s["scenes"][0]["text"] = "Hey guys welcome back today we are going to talk about something really very interesting"
    bad = virality.score(s, 40)
    assert good["score"] > bad["score"] and any("hook" in t for t in bad["tips"])


def test_gap_finder_diagnoses_and_feeds_back_winners(cfg, monkeypatch, fixed_now):
    add_job("hit", status="published", youtube_id="H", publish_at="2026-10-23T09:00:00+05:30")
    add_job("flop", status="published", youtube_id="F", publish_at="2026-10-23T09:00:00+05:30")
    pub = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    from pipeline import channel
    monkeypatch.setattr(channel, "video_status", lambda ids: [
        {"id": "H", "status": {"privacyStatus": "public"}, "snippet": {"title": "Big hit", "publishedAt": pub},
         "statistics": {"viewCount": "1200", "likeCount": "40", "commentCount": "3"}},
        {"id": "F", "status": {"privacyStatus": "public"}, "snippet": {"title": "Flop", "publishedAt": pub},
         "statistics": {"viewCount": "40", "likeCount": "0", "commentCount": "0"}}])
    g = gaps.analyse(cfg)
    flop = next(v for v in g["videos"] if v["title"] == "Flop")
    assert any("under 100 views" in d for d in flop["diagnosis"])
    assert gaps.winners(cfg)[0]["title"] == "Big hit"
    assert any("Gap finder" in l for l in gaps.report_lines(cfg))
