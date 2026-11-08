"""Performance-review agent: ranks every public Short on the channel and turns the results into a playbook that the
other agents follow, so each new video is built on what this channel's own audience actually rewarded.

Runs inside each daily run, re-analysing at most every performance.every_days days (default 1).

Measurement (fair across ages): views per hour since publication, plus like and comment rate. Each video gets a
"performance index" = its views/hour ÷ the channel median (1.0 = typical, 2.0 = twice as good).

The playbook (data/…/playbook.json) holds:
  leaders / laggards   the best and worst Shorts with their titles and hooks (examples to follow / avoid)
  pillar_weight        0.7–1.4× per content pillar/role, used when picking the next topic
  patterns             what the top third does differently from the bottom third (hook length, title length,
                       numbers in titles, question hooks, video length) once there are enough videos
  best_slots           which publishing times do best
Small samples are labelled and never drive big changes: weights move only for groups with analytics.min_sample
videos, and patterns appear only with at least 6 videos.

Consumers: ideas.refill_backlog (examples + pillar weights), ideas.next_topic (pillar weight on topic score),
scriptgen/storygen (style guidance), youtuber.package (title examples), daily report (leaderboard).
"""
import datetime as dt
import re

from . import jobs
from .util import log, media_duration, read_json, write_json


def _path():
    return jobs.HISTORY.parent / "playbook.json"


def _median(v):
    v, n = sorted(v), len(v)
    return 0.0 if not n else v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def _features(job):
    try:
        s = jobs.script_of(job)
    except (OSError, ValueError):
        s = {}
    scenes = [x.get("text", "") for x in s.get("scenes", [])]
    hook = scenes[0] if scenes else ""
    title = re.sub(r"\s*#Shorts\s*$", "", (s.get("youtube") or {}).get("title", job.get("title") or ""), flags=re.I)
    try:
        secs = media_duration(jobs.OUTPUT / job["folder"] / "video.mp4")  # the active channel's output folder
    except Exception:
        secs = sum(len(x.split()) for x in scenes) / 2.6
    plan = s.get("plan") or {}
    return {"title": title, "hook": hook, "hook_words": len(hook.split()), "title_chars": len(title),
            "title_number": bool(re.search(r"\d", title)), "question_hook": "?" in hook, "seconds": round(secs),
            "pillar": plan.get("pillar") or job.get("pillar") or "unknown", "role": job.get("role") or plan.get("role")
            or "unknown", "slot": (job.get("publish_at") or "")[11:16]}


def analyse(cfg, force=False):
    """Re-ranks every public Short; returns the playbook (cached for performance.every_days days)."""
    every = cfg.get("performance", {}).get("every_days", 1)
    old = read_json(_path(), None)
    if old and not force and (dt.date.today() - dt.date.fromisoformat(old["date"])).days < every:
        return old
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
        st = v.get("statistics", {})
        pub = dt.datetime.fromisoformat(v["snippet"]["publishedAt"].replace("Z", "+00:00"))
        hours = max(1.0, (now - pub).total_seconds() / 3600)
        views = int(st.get("viewCount", 0) or 0)
        rows.append({"folder": j["folder"], "youtube_id": j["youtube_id"], "hours": round(hours, 1), "views": views,
                     "views_per_hour": round(views / hours, 2),
                     "like_rate": round(int(st.get("likeCount", 0) or 0) / views, 4) if views else 0.0,
                     "comment_rate": round(int(st.get("commentCount", 0) or 0) / views, 4) if views else 0.0,
                     **_features(j)})
    med = _median([r["views_per_hour"] for r in rows]) or 1.0
    for r in rows:
        r["index"] = round(r["views_per_hour"] / med, 2)
    rows.sort(key=lambda r: -r["index"])
    min_n = cfg.get("analytics", {}).get("min_sample", 5)
    pb = {"date": dt.date.today().isoformat(), "videos": len(rows), "median_views_per_hour": med, "ranking": rows,
          # small channels: "leader" = at or above typical; larger ones: clearly above (1.2x)
          "leaders": [_ex(r) for r in rows if r["index"] >= (1.0 if len(rows) <= 4 else 1.2)][:3],
          "laggards": [_ex(r) for r in rows[::-1] if r["index"] < 0.6 and len(rows) >= 3][:2],
          "pillar_weight": _weights(rows, "pillar", min_n) | _weights(rows, "role", min_n),
          "best_slots": _groups(rows, "slot", min_n), "patterns": _patterns(rows)}
    write_json(_path(), pb)
    log(f"performance: {len(rows)} public Short(s) ranked; leaders: {[l['title'][:40] for l in pb['leaders']]}")
    return pb


def _ex(r):
    return {"title": r["title"], "hook": r["hook"], "views": r["views"], "index": r["index"], "pillar": r["pillar"]}


def _groups(rows, key, min_n):
    g = {}
    for r in rows:
        g.setdefault(r[key], []).append(r["index"])
    return {k: {"n": len(v), "index": round(_median(v), 2), "conclusive": len(v) >= min_n} for k, v in g.items()}


def _weights(rows, key, min_n):
    from .growth import shrink  # small groups move the topic picker less (one viral Short isn't a trend)
    return {k: round(max(0.7, min(1.4, shrink(v["index"], v["n"], min_n))), 2)
            for k, v in _groups(rows, key, min_n).items() if v["conclusive"] and k != "unknown"}


def _patterns(rows):
    """Top third vs bottom third, only with enough videos to mean something."""
    if len(rows) < 6:
        return [f"only {len(rows)} public Short(s): patterns appear from 6 onward; follow the leaders' examples"]
    k = len(rows) // 3
    top, bottom = rows[:k], rows[-k:]
    avg = lambda rs, f: sum(float(r[f]) for r in rs) / len(rs)
    out = []
    for f, label, unit in (("hook_words", "hook length", " words"), ("title_chars", "title length", " chars"),
                           ("seconds", "video length", "s")):
        a, b = avg(top, f), avg(bottom, f)
        if abs(a - b) >= max(2, 0.15 * max(a, b)):
            out.append(f"top videos average {a:.0f}{unit} {label} vs {b:.0f}{unit} for the weakest: aim near {a:.0f}{unit}")
    for f, label in (("title_number", "a number in the title"), ("question_hook", "a question as the hook")):
        a, b = avg(top, f), avg(bottom, f)
        if abs(a - b) >= 0.34:
            out.append(f"{label}: {a:.0%} of top videos vs {b:.0%} of the weakest → {'use' if a > b else 'avoid'} it")
    return out or ["no clear difference between top and bottom videos yet"]


# ---------------------------------------------------------------- what other agents read

def playbook():
    return read_json(_path(), {})


def guidance(cfg):
    """Short block for writer prompts: what worked here, what didn't, and what viewers say in the comments.
    Never overrides accuracy rules."""
    from .comments import guidance as viewer_guidance
    said = viewer_guidance()
    pb = playbook()
    if not pb.get("videos"):
        return said
    lines = [f"WHAT THIS CHANNEL'S AUDIENCE REWARDS (from {pb['videos']} public Shorts; style only, facts rules still apply):"]
    lines += [f'- WINNER ({l["index"]}x typical): title "{l["title"]}" | hook "{l["hook"]}"' for l in pb.get("leaders", [])]
    lines += [f'- WEAK ({l["index"]}x): title "{l["title"]}" | hook "{l["hook"]}" (avoid this style)'
              for l in pb.get("laggards", [])]
    lines += [f"- {p}" for p in pb.get("patterns", []) if not p.startswith("only ") and not p.startswith("no clear")]
    return "\n\n".join(b for b in ("\n".join(lines) if len(lines) > 1 else "", said) if b)


def topic_weight(topic):
    """Multiplier for a candidate topic's score from how its pillar/role performs (1.0 when unknown)."""
    w = playbook().get("pillar_weight", {})
    return w.get(topic.get("pillar"), 1.0) * w.get(topic.get("role"), 1.0)


def report_lines(cfg):
    pb = playbook()
    if not pb:
        return []
    lines = [f"## Performance review ({pb['date']}, {pb['videos']} public Shorts, median {pb['median_views_per_hour']} views/h)",
             "| # | Short | Age | Views | Views/h | Index | Like % | Pillar |", "|---|---|---|---|---|---|---|---|"]
    for i, r in enumerate(pb["ranking"][:20], 1):
        lines.append(f"| {i} | {r['title'][:45]} | {r['hours']:.0f}h | {r['views']} | {r['views_per_hour']} | "
                     f"{r['index']}x | {r['like_rate']:.1%} | {r['pillar'][:22]} |")
    lines += ["", "**Playbook the other agents now follow:**",
              *[f"- leader: \"{l['title']}\" ({l['index']}x)" for l in pb["leaders"]],
              *[f"- avoid: \"{l['title']}\" ({l['index']}x)" for l in pb["laggards"]],
              *[f"- {p}" for p in pb["patterns"]],
              *([f"- topic weights: " + ", ".join(f"{k} {v}x" for k, v in pb["pillar_weight"].items())]
                if pb["pillar_weight"] else ["- topic weights: not enough videos per pillar yet"]),
              *([f"- best slot so far: {max(pb['best_slots'], key=lambda s: pb['best_slots'][s]['index'])}"]
                if pb["best_slots"] else []), ""]
    return lines
