"""Creator-stats Shorts: number formatting, the data-only narration rule, fact packs, cards."""
import datetime as dt

from pipeline.clips import registry, stats
from tests.test_clips import ccfg  # noqa: F401  (fixture)


def test_numbers_are_formatted_for_screen_and_voice():
    assert stats.human(145_854_287) == ("145.9M", "145.9 million")
    assert stats.human(1_015_266) == ("1.02M", "1.02 million")
    assert stats.human(523_000_000) == ("523M", "523 million")


def test_only_numbers_from_the_data_may_be_spoken():
    pack = {"facts": [stats._fact("a", "views", 145_854_287),
                      {"id": "d", "label": "days", "value": 8, "display": "8", "spoken": "8"}]}
    assert stats.unsupported_numbers("It hit 145.9 million views in 8 days", pack) == []
    assert stats.unsupported_numbers("That's 145.9M views", pack) == []
    assert stats.unsupported_numbers("He made 50 million dollars", pack) == ["50 million"]


def test_unsupported_claim_words_are_caught():
    pack = {"facts": [stats._fact("s", "subs", 109_000_000)]}
    issues = stats.problems({"scenes": ["Exactly 109 million fans, he never slows down!"], "title_options": []}, pack)
    assert any("exactly" in i.lower() for i in issues) and any("never" in i for i in issues)


def test_fact_packs_cover_every_angle(ccfg, monkeypatch):  # noqa: F811
    for i, (cid, name) in enumerate([("UC1", "Alpha"), ("UC2", "Bravo"), ("UC3", "Charlie")]):
        for k, views in enumerate((1_000_000 * (i + 1), 300_000)):
            registry.upsert(ccfg, {"id": f"yt:{cid}{k}", "platform": "youtube", "title": f"{name} video {k}",
                                   "creator": name, "creator_channel_id": cid, "views": views,
                                   "published": (dt.date.today() - dt.timedelta(days=2 + k)).isoformat()})
    monkeypatch.setattr(stats, "channel_stats", lambda cfg, ids: {c: {"title": n, "subs": 1_000_000, "views": 9,
                                                                       "videos": 9}
                                                                   for c, n in [("UC1", "Alpha"), ("UC2", "Bravo"),
                                                                                ("UC3", "Charlie")]})
    kinds = {p["key"].split(":")[0] for p in stats.fact_packs(ccfg)}
    assert kinds == {"pace", "range", "leaderboard", "versus"}
    pace = next(p for p in stats.fact_packs(ccfg) if p["key"].startswith("pace") and "Charlie" in p["creators"])
    per_day = next(f for f in pace["facts"] if f["id"] == "per_day")
    assert per_day["value"] == 3_000_000 / 2


def test_cards_render_at_shorts_size(tmp_path):
    from PIL import Image
    f = [stats._fact("a", "Alpha subscribers", 2_000_000, "Alpha"), stats._fact("b", "Bravo subscribers", 1_000_000, "Bravo")]
    for kind, facts in (("title", []), ("big", f[:1]), ("versus", f), ("bars", f), ("question", [])):
        p = stats.card(kind, facts, "Who is winning?", tmp_path / f"{kind}.png", seed=1)
        assert Image.open(p).size == (1080, 1920)


def test_repetitive_wording_is_sent_back():
    pack = {"facts": [], "creators": ["IShowSpeed"]}
    words = {"scenes": ["A massive upload.", "Massive numbers.", "So massive!"], "title_options": []}
    assert any("'massive' is used 3 times" in p for p in stats.problems(words, pack))
    assert not any("speed" in p.lower() for p in stats.problems({"scenes": ["Speed, Speed, Speed"]}, pack))
