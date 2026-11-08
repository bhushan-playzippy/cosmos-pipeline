"""Analytics agent: snapshots, daily dashboard, weekly report, monetization-readiness tracker.

Two sources:
  1. YouTube Data API (already authorized): views, likes, comments per video; channel subscribers/views.
  2. YouTube Analytics API (optional, separate login: python main.py auth-analytics): watch time, average view
     duration/percentage, subscribers gained, per-video. Kept in its own token file so it can never break uploads.
Every number in the reports comes from these files; nothing is estimated here.
"""
import csv
import datetime as dt
import json

from . import control, jobs
from .util import ROOT, DATA, event, log, read_json, write_json

ADIR = DATA / "analytics"
REPORTS = ROOT / "reports"
ANALYTICS_TOKEN = ROOT / "youtube_analytics_token.json"
ANALYTICS_SCOPES = ["https://www.googleapis.com/auth/yt-analytics.readonly",
                    "https://www.googleapis.com/auth/youtube.readonly"]


def _append(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    new = not path.exists()
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(row))
        if new:
            w.writeheader()
        w.writerow(row)


def _upsert(path, rows, key):
    """Replaces rows with the same key (e.g. date + video) instead of appending: two runs on one day, or a manual
    `weekly` after the scheduled pull, used to double the rows. Atomic write with a .bak. None → "" (= unavailable,
    never 0)."""
    import os
    old = _read(path)
    fields = list(dict.fromkeys([*(old[0].keys() if old else []), *(k for r in rows for k in r)]))
    k = lambda r: tuple(str(r.get(x, "")) for x in key)
    merged = {k(r): r for r in old}
    for r in rows:
        merged[k(r)] = {**merged.get(k(r), {}), **{f: ("" if v is None else v) for f, v in r.items()}}
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(merged.values())
    if path.exists():
        os.replace(path, path.with_name(path.name + ".bak"))
    os.replace(tmp, path)


def _read(path):
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def snapshot(cfg):
    """Appends today's channel + per-video stats (Data API, ~1 quota unit per 50 videos)."""
    from . import channel
    if control.DRY_RUN:
        return
    today = jobs.now().date().isoformat()
    ch = channel.info()
    st = ch["statistics"]
    _upsert(ADIR / "channel.csv", [{"date": today, "subscribers": st.get("subscriberCount"),
                                    "views": st.get("viewCount"), "videos": st.get("videoCount")}], ("date",))
    live = [j for j in jobs.load() if j.get("youtube_id") and j["status"] in jobs.LIVE]
    by_id = {j["youtube_id"]: j for j in live}
    rows = []
    for i in range(0, len(live), 50):
        for v in channel.video_status([j["youtube_id"] for j in live[i:i + 50]]):
            j, s = by_id[v["id"]], v.get("statistics", {})
            rows.append({
                "date": today, "video_id": v["id"], "folder": j["folder"], "role": j.get("role"),
                "slot": (j.get("publish_at") or "")[11:16], "privacy": v["status"]["privacyStatus"],
                "views": s.get("viewCount", 0), "likes": s.get("likeCount", 0), "comments": s.get("commentCount", 0)})
    _upsert(ADIR / "videos.csv", rows, ("date", "video_id"))
    log(f"analytics snapshot saved ({len(live)} videos)")


# ---------------------------------------------------------------- YouTube Analytics API (optional)

def authorize():
    from google_auth_oauthlib.flow import InstalledAppFlow
    from .publish_youtube import CLIENT_SECRET
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), ANALYTICS_SCOPES)
    creds = flow.run_local_server(port=0, prompt="consent", access_type="offline")
    from googleapiclient.discovery import build
    from .publish_youtube import check_channel
    check_channel(build("youtube", "v3", credentials=creds, cache_discovery=False).channels().list(
        part="id", mine=True).execute()["items"][0]["id"])  # never store another channel's analytics login
    ANALYTICS_TOKEN.parent.mkdir(parents=True, exist_ok=True)
    ANALYTICS_TOKEN.write_text(creds.to_json())
    ANALYTICS_TOKEN.chmod(0o600)
    log(f"saved {ANALYTICS_TOKEN.name}")


BASE_METRICS = ["views", "estimatedMinutesWatched", "averageViewDuration", "averageViewPercentage",
                "subscribersGained", "subscribersLost", "likes", "comments", "shares"]
OPTIONAL_METRICS = ["engagedViews"]  # newer metric: requested, recorded as unavailable if the API refuses it
# Not exposed by the public YouTube Analytics API (as far as can be verified): always unavailable, never 0.
UNAVAILABLE = ["impressions", "impressionsClickThroughRate", "viewedVsSwipedAway"]


def _clients():
    """(analytics client, data client) with the analytics token, or None with the blocker logged."""
    if not ANALYTICS_TOKEN.exists():
        log("YouTube Analytics not connected: run `python main.py auth-analytics` and enable "
            "'YouTube Analytics API' in Google Cloud Console (APIs & Services → Library)")
        return None
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    creds = Credentials.from_authorized_user_info(json.loads(ANALYTICS_TOKEN.read_text()), ANALYTICS_SCOPES)
    if not creds.valid:
        from google.auth.exceptions import RefreshError
        try:
            creds.refresh(Request())
        except RefreshError as e:
            log(f"YouTube Analytics login expired ({e}): run `python main.py auth-analytics`")
            event("auth_expired", token="analytics")
            return None
    return (build("youtubeAnalytics", "v2", credentials=creds, cache_discovery=False),
            build("youtube", "v3", credentials=creds, cache_discovery=False))


def _identity_ok(yt):
    """The analytics token must belong to this channel (2 Nov audit: `ids=channel==MINE` trusted any token, so a
    wrong one would have filed the other channel's numbers here)."""
    from . import publish_youtube
    mine = (yt.channels().list(part="id", mine=True).execute().get("items") or [{}])[0].get("id")
    if publish_youtube.EXPECTED_CHANNEL and mine != publish_youtube.EXPECTED_CHANNEL:
        log(f"analytics token belongs to {mine}, not this channel ({publish_youtube.EXPECTED_CHANNEL}): nothing saved")
        event("analytics_identity_mismatch", got=mine)
        return False
    return True


def analytics_due():
    today = jobs.now().date().isoformat()
    return not any(r.get("date") == today for r in _read(ADIR / "video_analytics.csv"))


def fetch_video_analytics(cfg, days=28, detail=20):
    """Per-video watch metrics for the last `days` (upserted by date + video), plus traffic sources and the
    retention curve of the `detail` most recent public videos. Metrics the API doesn't give are blank (null).
    Returns the rows, or None (not connected, wrong channel, or a dry run)."""
    if control.DRY_RUN:
        return None
    cl = _clients()
    if not cl:
        return None
    ya, yt = cl
    if not _identity_ok(yt):
        return None
    end = jobs.now().date()
    start = (end - dt.timedelta(days=days)).isoformat()
    q = lambda metrics, **kw: ya.reports().query(ids="channel==MINE", startDate=start, endDate=end.isoformat(),
                                                 metrics=",".join(metrics), **kw).execute()
    metrics = BASE_METRICS + OPTIONAL_METRICS
    try:
        r = q(metrics, dimensions="video", sort="-views", maxResults=200)
    except Exception as e:  # an optional metric this API version doesn't know: ask again without it
        if "400" not in str(e) and "nvalid" not in str(e):
            raise
        log(f"analytics: optional metrics unavailable ({str(e)[:80]}); recording them as null")
        metrics = BASE_METRICS
        r = q(metrics, dimensions="video", sort="-views", maxResults=200)
    cols = [c["name"] for c in r.get("columnHeaders", [])]
    rows = []
    for row in r.get("rows", []):
        d = dict(zip(cols, row))
        rows.append({"date": end.isoformat(), **{m: d.get(m) for m in ["video"] + BASE_METRICS + OPTIONAL_METRICS},
                     **{m: None for m in UNAVAILABLE}})
    _upsert(ADIR / "video_analytics.csv", rows, ("date", "video"))
    live = sorted((j for j in jobs.load() if j.get("youtube_id") and j["status"] == "published"),
                  key=lambda j: j.get("publish_at") or "", reverse=True)[:detail]
    traffic, curves = [], read_json(ADIR / "retention.json", {})
    for j in live:
        vid = j["youtube_id"]
        try:
            t = q(["views"], dimensions="insightTrafficSourceType", filters=f"video=={vid}")
            traffic += [{"date": end.isoformat(), "video": vid, "source": src, "views": v} for src, v in t.get("rows", [])]
            c = q(["audienceWatchRatio", "relativeRetentionPerformance"], dimensions="elapsedVideoTimeRatio",
                  filters=f"video=={vid}")
            pts = [[float(x), float(w), float(rel)] for x, w, rel in c.get("rows", [])]
            half = next((x for x, w, _ in pts if w < 0.5), None)
            curves[vid] = {"date": end.isoformat(), "curve": pts, "below_half_at": half,
                           "start_ratio": pts[0][1] if pts else None}
        except Exception as e:  # one video's detail must not lose the rest
            log(f"analytics: detail for {vid} unavailable ({type(e).__name__})")
    if traffic:
        _upsert(ADIR / "traffic.csv", traffic, ("date", "video", "source"))
    write_json(ADIR / "retention.json", curves)
    event("analytics_ingested", videos=len(rows), detailed=len(live), metrics=metrics)
    log(f"analytics: {len(rows)} video rows, traffic + retention for {len(live)} recent Shorts")
    return rows


# ---------------------------------------------------------------- monetization readiness

def readiness(cfg):
    """Eligibility tracker. Only counts what the API reports; YPP approval is a separate human review."""
    m = cfg.get("monetization", {})
    rows = _read(ADIR / "channel.csv")
    subs = int(rows[-1]["subscribers"]) if rows else None
    out = {"subscribers": subs, "thresholds": m.get("thresholds", {}), "status": m.get("status", {}),
           "notes": ["Shorts views (90 days) and public watch hours (12 months) need the YouTube Analytics "
                     "connection or YouTube Studio → Earn; the Data API total views is not the eligible count."]}
    return out


# ---------------------------------------------------------------- reports

def _status_table(all_jobs):
    c = {}
    for j in all_jobs:
        c[j["status"]] = c.get(j["status"], 0) + 1
    return c


def write_daily_report(cfg):
    all_jobs = jobs.load()
    today = jobs.now().date()
    target = cfg["schedule"].get("videos_per_day", 3)
    usage = _read(control.USAGE)  # this channel's usage only
    lines = [f"# Daily dashboard — {today}", "",
             f"Target: {target} confirmed publications per day. Only **published** (YouTube says public) counts.", "",
             "| Day | Published | Scheduled | Ready | Awaiting approval | Shortfall |", "|---|---|---|---|---|---|"]
    for d in range(-1, cfg["schedule"].get("buffer_days", 2) + 1):
        day = today + dt.timedelta(days=d)
        c = jobs.counts_for_day(all_jobs, day)
        short = max(0, target - c["published"] - c["scheduled"] - c["planned_or_ready"] - c["awaiting_approval"])
        lines.append(f"| {day} | {c['published']} | {c['scheduled']} | {c['planned_or_ready']} | "
                     f"{c['awaiting_approval']} | {short} |")
    waiting = [j for j in all_jobs if j["status"] == "awaiting_approval"]
    if waiting:
        lines += ["", f"## Awaiting your approval ({len(waiting)})",
                  "Watch `video.mp4`, check the narration against the FACT-CHECK LOG in `post.txt`, then run the command.", ""]
        for j in sorted(waiting, key=lambda j: j.get("slot") or j.get("created") or ""):  # no slot: approval-mode jobs
            try:
                s = jobs.script_of(j)
            except (OSError, ValueError):
                continue
            when = f"{jobs.when(j):%a %d %b %H:%M} IST" if jobs.when(j) else "next free slot"  # Hunley has none: the
            lines += [f"### {when} — {s['youtube']['title']}",  # clips daily report crashed on it every run, 1-2 Nov
                      *[f"> {sc['text']}" for sc in s["scenes"]], "",
                      f"`python main.py approve output/{j['folder']}`  ·  "
                      f"`python main.py reject output/{j['folder']} --note \"why\"`", ""]
    lines += ["", "## Jobs by status", *[f"- {k}: {v}" for k, v in sorted(_status_table(all_jobs).items())], ""]
    attention = [j for j in all_jobs if j["status"] in ("failed", "quarantined") or j.get("alert")]
    lines += ["## Needs attention"] + ([f"- `{j['folder']}` — {j['status']}: {j.get('alert') or j.get('reason')}"
                                        for j in attention] or ["- nothing"]) + [""]
    if usage:
        u = usage[-1]
        lines += ["## Last run usage", f"- command `{u['command']}`, Gemini calls {u['gemini_calls']} "
                  f"(+{u['gemini_vision_calls']} vision), uploads {u['uploads']} ({u['upload_units']} quota units), "
                  f"videos made {u['videos_made']}, quarantined {u['videos_quarantined']}", ""]
    r = readiness(cfg)
    lines += ["## Monetization readiness (tracked, not promised)",
              f"- Subscribers: {r['subscribers']} / {r['thresholds'].get('subscribers', '?')}",
              *[f"- {k}: {v}" for k, v in r["status"].items()], *[f"- note: {n}" for n in r["notes"]], ""]
    from . import gaps, performance, quality
    lines += quality.report_lines(cfg)
    from . import health
    lines += health.report_lines(cfg)
    lines += performance.report_lines(cfg)
    from . import comments
    lines += comments.report_lines(cfg)
    lines += gaps.report_lines(cfg)
    if cfg["channel"].get("type") == "clips":
        from .clips.orchestrator import report_lines
        lines += report_lines(cfg)
    from . import finance
    lines += finance.summary_lines(cfg)
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"daily-{today}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    log(f"dashboard → {path}")
    return path


def latest_rows(all_jobs=None):
    """The newest videos.csv row of each video that is still live on the channel. snapshot() only covers live
    jobs, so a video later withdrawn or deleted kept its last "public" row forever (clips -xTUmC2KASU, withdrawn
    after 25 Oct, still counted in the 1 Nov weekly report and growth agent)."""
    live = {j["folder"] for j in (all_jobs if all_jobs is not None else jobs.load()) if j["status"] in jobs.LIVE}
    latest = {}
    for r in _read(ADIR / "videos.csv"):
        if r.get("folder") in live:
            latest[r["video_id"]] = r
    return latest


def median(v):
    v, n = sorted(v), len(v)
    return 0.0 if not n else v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def write_weekly_report(cfg):
    """Groups the latest snapshot by role and slot. Flags small samples instead of drawing conclusions."""
    all_jobs = jobs.load()
    today = jobs.now().date()
    latest = latest_rows(all_jobs)
    by_folder = {j["folder"]: j for j in all_jobs}
    groups, per_day = {}, {}
    for r in latest.values():
        if r["privacy"] != "public":
            continue
        j = by_folder.get(r["folder"], {})
        keys = [f"role:{r['role'] or '(none)'}", f"slot:{r['slot'] or '(none)'}"]
        if j.get("source_id"):  # clip channel: which creators/sources work
            try:
                src = jobs.script_of(j)["source"]
                keys += [f"creator:{src.get('creator')}", f"type:{jobs.script_of(j)['segment'].get('type')}"]
            except (OSError, ValueError, KeyError):
                pass
        # exposure time differs, so also compare views per day since publication
        age = max(1, (today - dt.date.fromisoformat((j.get("publish_at") or r["date"])[:10])).days)
        for key in keys:
            groups.setdefault(key, []).append(int(r["views"] or 0))
            per_day.setdefault(key, []).append(int(r["views"] or 0) / age)
    min_n = cfg.get("analytics", {}).get("min_sample", 5)
    lines = [f"# Weekly report — week ending {today}", "",
             "Views are lifetime-to-date per video at the latest snapshot; older videos have had longer to accrue.",
             f"Groups with fewer than {min_n} videos are marked as too small to conclude anything.", "",
             "| Group | Videos | Median views | Median views/day | Mean views | Conclusive? |", "|---|---|---|---|---|---|"]
    for k, v in sorted(groups.items()):
        med, pd = median(v), median(per_day[k])  # a true median (the upper middle value was used for even counts)
        lines.append(f"| {k} | {len(v)} | {med:.0f} | {pd:.1f} | {sum(v) / len(v):.0f} | "
                     f"{'yes' if len(v) >= min_n else 'no — keep collecting'} |")
    va = _read(ADIR / "video_analytics.csv")
    lines += ["", "## Retention (YouTube Analytics)",
              f"- {len(va)} rows collected" if va else "- not connected yet (python main.py auth-analytics)", "",
              "## Experiments", "Record hypotheses and results in docs/EXPERIMENTS.md (one change at a time).", ""]
    from .growth import analyse, report_lines
    analyse(cfg)
    lines += report_lines(cfg)
    REPORTS.mkdir(exist_ok=True)
    path = REPORTS / f"weekly-{today}.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    log(f"weekly report → {path}")
    return path
