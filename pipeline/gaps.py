"""Gap-finder agent (post-publish): what is stopping each public video, and what the channel should do more of.

Reads live public stats (Data API: views, likes, comments) for every published video, normalises by hours since
publication, joins each video's own features (hook, title, length, virality score, slot, pillar/role), and writes:
  - a per-video diagnosis (e.g. "under 100 views after a day → the feed stopped testing it: the opening loses
    people; hook was 18 words")
  - the channel's current winners (best views/hour): their titles and hooks are fed back into topic brainstorming
    (data/…/gaps.json → ideas.refill_backlog), so the channel makes more of what its own audience rewards
Retention / swipe-away data needs the YouTube Analytics connection (python main.py [--channel X] auth-analytics);
without it the agent says what it can't see instead of guessing.
"""
import datetime as dt

from . import jobs
from .util import log, read_json, write_json


def _path(cfg):
    return jobs.HISTORY.parent / "gaps.json"  # the active channel's data folder


def analyse(cfg):
    from . import channel
    live = [j for j in jobs.load() if j.get("youtube_id") and j["status"] in ("scheduled", "published")]
    stats = {}
    for i in range(0, len(live), 50):
        for v in channel.video_status([j["youtube_id"] for j in live[i:i + 50]]):
            stats[v["id"]] = v
    now = dt.datetime.now(dt.timezone.utc)
    rows = []
    for j in live:
        v = stats.get(j["youtube_id"])
        if not v or v["status"]["privacyStatus"] != "public":
            continue
        s = v.get("statistics", {})
        pub = dt.datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
        hours = max(1.0, (now - pub).total_seconds() / 3600)
        views, likes, comments = (int(s.get(k, 0) or 0) for k in ("viewCount", "likeCount", "commentCount"))
        try:
            sc = jobs.script_of(j)
        except (OSError, ValueError):
            sc = {}
        hook = (sc.get("scenes") or [{"text": ""}])[0].get("text", "")
        row = {"folder": j["folder"], "youtube_id": j["youtube_id"], "title": v["snippet"]["title"], "hook": hook,
               "hours": round(hours, 1), "views": views, "views_per_hour": round(views / hours, 1),
               "like_rate": round(likes / views, 3) if views else 0.0,
               "comment_rate": round(comments / views, 4) if views else 0.0,
               "virality": (sc.get("virality") or {}).get("score"), "slot": (j.get("publish_at") or "")[11:16],
               "role": j.get("role"), "diagnosis": []}
        d = row["diagnosis"]
        if hours >= 24 and views < 100:
            d.append(f"under 100 views after {hours:.0f}h: the feed stopped testing it, so people swiped in the first "
                     f"seconds. Hook was {len(hook.split())} words: \"{hook[:90]}\"")
        elif hours >= 6 and row["views_per_hour"] < 2:
            d.append("slow start: test a sharper first line and a more concrete title on the next video like this")
        if views >= 200 and row["like_rate"] < 0.02:
            d.append(f"like rate {row['like_rate']:.1%} (<2%): viewers didn't feel the payoff; strengthen the twist")
        if views >= 300 and comments == 0:
            d.append("no comments: end on a sharper question and pin the comment in the app")
        if row["virality"] is not None and row["virality"] < 70:
            d.append(f"pre-publish virality score was {row['virality']} — see its tips in post.txt")
        if not d and views >= 500:
            d.append("working: make more videos with this hook/title pattern")
        rows.append(row)
    rows.sort(key=lambda r: -r["views_per_hour"])
    winners = [r for r in rows if r["views"] >= 300][:3]
    out = {"date": dt.date.today().isoformat(), "videos": rows,
           "winners": [{"title": r["title"], "hook": r["hook"], "views": r["views"]} for r in winners],
           "retention": "not connected (run auth-analytics for watch-time and average % viewed)"}
    write_json(_path(cfg), out)
    log(f"gaps: {len(rows)} public video(s) diagnosed, {len(winners)} winner(s)")
    return out


def winners(cfg):
    return read_json(_path(cfg), {}).get("winners", [])


def report_lines(cfg):
    g = read_json(_path(cfg), None)
    if not g:
        return []
    lines = ["## Gap finder (public videos, best first)",
             "| Video | Age | Views | Views/h | Like % | Virality | What's holding it back |", "|---|---|---|---|---|---|---|"]
    for r in g["videos"][:15]:
        lines.append(f"| {r['title'][:45]} | {r['hours']:.0f}h | {r['views']} | {r['views_per_hour']} | "
                     f"{r['like_rate']:.1%} | {r['virality'] or '-'} | {'; '.join(r['diagnosis']) or 'too early'} |")
    if g["winners"]:
        lines += ["", "Winners fed into the next topic brainstorm: " + "; ".join(f"\"{w['title']}\"" for w in g["winners"])]
    lines += [f"- Retention: {g['retention']}", ""]
    return lines
