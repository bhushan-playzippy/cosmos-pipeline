"""Growth agent: turns this channel's own analytics into decisions, never guesses.

Every comparison uses views per day since publication (Shorts of different ages have had different exposure),
and a group is used only once it has analytics.min_sample public videos. With less data it says so and
changes nothing.

What it decides:
  - best / weakest upload slots → recommendation in the weekly report (slots are changed by a human, because a
    slot change moves the whole schedule)
  - which moment types / roles / creators work → a bounded preference (0.7–1.3×), read only by the unused
    clip-segment picker (data/…/growth.json)
  - which HOOK FEATURES work (2 Nov) → feature_boost, applied to every live topic pick (ideas.next_topic via
    topic_multiplier). Why: role weights were the only conclusive signal, and next_topic picks within the slot's
    role, so they cancelled out: no growth number changed which topic was made. Features are computed the same
    way for a published hook and a backlog topic's hook_idea, so what the audience rewarded ranks the backlog.
"""
import datetime as dt
import math

from . import analytics, facts, jobs
from .util import log, read_json, write_json


def _path(cfg):
    return jobs.HISTORY.parent / "growth.json"  # the active channel's data folder


def _median(v):
    v, n = sorted(v), len(v)
    return 0.0 if not n else v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def shrink(ratio, n, k):
    """A group's ratio pulled toward 1.0 by its sample size: n/(n+k) of the observed effect. With 5 videos and
    k=5 a group that looks 1.6× typical counts as 1.3×, so one viral Short can't swing the picker."""
    return 1 + (ratio - 1) * n / (n + k)


def features(hook):
    """Deterministic hook features, same for a published hook and a backlog hook_idea."""
    hook = str(hook or "")
    nums = facts.quantities(hook)
    return {"question_hook": "?" in hook, "number_hook": bool(nums),
            "big_number_hook": any(v >= 1000 for v, _ in nums),  # abstract scale ("15 billion miles")
            "long_hook": len(hook.split()) > 12}


def _feature_boost(rows, overall, min_n):
    """{feature: {with, without, n_with, n_without, conclusive}}: each side's median views/day vs the channel,
    shrunk by its sample size and bounded 0.8–1.25. Inconclusive (either side under min_sample) → 1.0 both."""
    out = {}
    for f in ("question_hook", "number_hook", "big_number_hook", "long_hook"):
        yes = [v for feats, v in rows if feats.get(f)]
        no = [v for feats, v in rows if not feats.get(f)]
        ok = len(yes) >= min_n and len(no) >= min_n and overall > 0
        b = lambda vs: round(max(0.8, min(1.25, shrink(_median(vs) / overall, len(vs), min_n))), 2) if ok else 1.0
        out[f] = {"with": b(yes), "without": b(no), "n_with": len(yes), "n_without": len(no), "conclusive": ok}
    return out


def analyse(cfg):
    all_jobs = jobs.load()
    latest = analytics.latest_rows(all_jobs)  # still-live videos only (withdrawn ones kept stale public rows)
    by_folder = {j["folder"]: j for j in all_jobs}
    today = jobs.now().date()
    groups = {"slot": {}, "type": {}, "creator": {}}
    all_vpd, feat_rows, ret_rows, sub_rows = [], [], [], []
    watch = _watch_metrics()  # YouTube Analytics: avg % viewed and subscribers per 1,000 views, per video id
    for r in latest.values():
        if r.get("privacy") != "public":
            continue
        j = by_folder.get(r["folder"], {})
        pub = (j.get("publish_at") or r["date"])[:10]
        age = max(1, (today - dt.date.fromisoformat(pub)).days)
        vpd = int(r["views"] or 0) / age
        all_vpd.append(vpd)
        groups["slot"].setdefault(r.get("slot") or "?", []).append(vpd)
        kind = j.get("role")
        if j.get("source_id"):
            try:
                s = jobs.script_of(j)
                kind = s["segment"].get("type") or kind
                groups["creator"].setdefault(s["source"].get("creator", "?"), []).append(vpd)
            except (OSError, ValueError, KeyError):
                pass
        groups["type"].setdefault(kind or "?", []).append(vpd)
        try:
            hook = (jobs.script_of(j).get("scenes") or [{}])[0].get("text", "")
        except (OSError, ValueError, KeyError):
            hook = None
        if hook:
            feat_rows.append((features(hook), vpd))
            w = watch.get(r.get("video_id") or j.get("youtube_id"))
            if w and w.get("retention") is not None:
                ret_rows.append((features(hook), w["retention"]))
            if w and w.get("subs_per_1k") is not None:
                sub_rows.append((features(hook), w["subs_per_1k"]))
    min_n = cfg.get("analytics", {}).get("min_sample", 5)
    overall = _median(all_vpd)
    out = {"date": today.isoformat(), "videos": len(all_vpd), "median_views_per_day": round(overall, 1),
           "min_sample": min_n, "groups": {}, "type_boost": {}, "recommendations": []}
    for g, d in groups.items():
        out["groups"][g] = {k: {"n": len(v), "median_vpd": round(_median(v), 1), "conclusive": len(v) >= min_n}
                            for k, v in d.items()}
    ready = {k: v for k, v in out["groups"]["type"].items() if v["conclusive"] and k != "?"}  # "?" = no role
    if overall > 0:
        out["type_boost"] = {k: round(max(0.7, min(1.3, shrink(v["median_vpd"] / overall, v["n"], min_n))), 2)
                             for k, v in ready.items()}
    out["feature_boost"] = _feature_boost(feat_rows, overall, min_n)
    # 2 Nov: views alone aren't the goal; the same hook features measured on retention and subscriber conversion
    out["feature_boost_retention"] = _feature_boost(ret_rows, _median([v for _f, v in ret_rows]), min_n)
    out["feature_boost_subs"] = _feature_boost(sub_rows, _median([v for _f, v in sub_rows]), min_n)
    out["metric_samples"] = {"views": len(feat_rows), "retention": len(ret_rows), "subscribers": len(sub_rows)}
    out["playbook"] = _playbook(out, min_n)
    for f, b in out["feature_boost"].items():
        if b["conclusive"] and abs(b["with"] - b["without"]) >= 0.1:
            out["recommendations"].append(f"hooks with {f.replace('_', ' ')} earn {b['with']}× vs {b['without']}× "
                                          f"without (n={b['n_with']}/{b['n_without']}): the topic picker applies it.")
    slots = {k: v for k, v in out["groups"]["slot"].items() if v["conclusive"]}
    if len(slots) >= 2:
        best = max(slots, key=lambda k: slots[k]["median_vpd"])
        worst = min(slots, key=lambda k: slots[k]["median_vpd"])
        if slots[worst]["median_vpd"] < 0.5 * slots[best]["median_vpd"]:
            out["recommendations"].append(
                f"Slot {worst} earns {slots[worst]['median_vpd']} views/day vs {slots[best]['median_vpd']} at {best}: "
                f"try moving {worst} to an hour near {best} (edit schedule.slots), then re-check in 2 weeks.")
    for k, b in out["type_boost"].items():
        if b >= 1.2:
            out["recommendations"].append(f"'{k}' moments outperform ({b}×): the picker now prefers them.")
        elif b <= 0.8:
            out["recommendations"].append(f"'{k}' moments underperform ({b}×): the picker now uses them less.")
    creators = {k: v for k, v in out["groups"]["creator"].items() if v["conclusive"]}
    if creators:
        top = max(creators, key=lambda k: creators[k]["median_vpd"])
        out["recommendations"].append(f"Best creator so far: {top} ({creators[top]['median_vpd']} views/day): "
                                      "look for more of their campaigns/footage.")
    if len(all_vpd) < min_n:
        out["recommendations"].append(f"Only {len(all_vpd)} public video(s) with stats: too early to change anything.")
    write_json(_path(cfg), out)
    log(f"growth: {len(all_vpd)} videos analysed, {len(out['recommendations'])} recommendation(s)")
    return out


def _watch_metrics():
    """{video id: {"retention": avg % viewed, "subs_per_1k": subscribers gained per 1,000 views}} from the latest
    YouTube Analytics pull of the active channel; blank metrics stay None (unavailable, not zero)."""
    rows = analytics._read(analytics.ADIR / "video_analytics.csv")
    latest = {}
    for r in rows:
        if r.get("video") and (r["video"] not in latest or r["date"] >= latest[r["video"]]["date"]):
            latest[r["video"]] = r
    num = lambda v: float(v) if v not in (None, "") else None
    out = {}
    for vid, r in latest.items():
        views, subs = num(r.get("views")), num(r.get("subscribersGained"))
        out[vid] = {"retention": num(r.get("averageViewPercentage")),
                    "subs_per_1k": subs * 1000 / views if views and subs is not None and views >= 100 else None}
    return out


def _combined(fb_list, feat):
    """Geometric mean of a feature's conclusive effects across metrics: (with, without, metrics used)."""
    used = [fb[feat] for fb in fb_list if fb.get(feat, {}).get("conclusive")]
    if not used:
        return None
    g = lambda side: round(math.prod(u[side] for u in used) ** (1 / len(used)), 2)
    return g("with"), g("without"), len(used)


def _playbook(out, min_n):
    """Repeat / stop / test, with the evidence: the combined effect across views/day, retention and subscriber
    conversion, its sample sizes and a confidence label. Never a causal claim: an association on a few Shorts."""
    recs = []
    fbs = [out["feature_boost"], out["feature_boost_retention"], out["feature_boost_subs"]]
    for feat, label in (("question_hook", "a question as the hook"), ("number_hook", "a number in the hook"),
                        ("big_number_hook", "a big abstract number in the hook"), ("long_hook", "a hook over 12 words")):
        c = _combined(fbs, feat)
        n = out["feature_boost"].get(feat, {})
        smaller = min(n.get("n_with", 0), n.get("n_without", 0))
        conf = "moderate" if smaller >= 2 * min_n else "low"
        if c and c[0] >= c[1] * 1.1:
            recs.append({"action": "repeat", "what": label, "evidence": f"{c[0]}× vs {c[1]}× across {c[2]} metric(s)",
                         "n": [n.get("n_with"), n.get("n_without")], "confidence": conf})
        elif c and c[1] >= c[0] * 1.1:
            recs.append({"action": "stop", "what": label, "evidence": f"{c[0]}× vs {c[1]}× across {c[2]} metric(s)",
                         "n": [n.get("n_with"), n.get("n_without")], "confidence": conf})
        elif not c:
            recs.append({"action": "test", "what": label, "evidence": f"not enough videos on each side (need {min_n})",
                         "n": [n.get("n_with"), n.get("n_without")], "confidence": "insufficient"})
    return recs


def topic_multiplier(topic, cfg, recent=()):
    """(multiplier, parts) for a backlog topic: the performance agent's pillar/role weight × the growth agent's
    conclusive hook-feature effects × a repetition penalty (0.85× per use of the same pillar among the last 4
    made videos, floor 0.7) that keeps the mix diverse. Every part is in `parts`, recorded with the pick."""
    from .performance import topic_weight
    parts = {"pillar_role": round(topic_weight(topic), 2)}
    g = read_json(_path(cfg), {})
    fbs = [g.get("feature_boost", {}), g.get("feature_boost_retention", {}), g.get("feature_boost_subs", {})]
    for f, on in features(topic.get("hook_idea") or topic.get("topic")).items():
        c = _combined(fbs, f)  # views/day, retention and subscriber conversion together, not views alone
        if c:
            parts[f] = c[0] if on else c[1]
    same = sum(1 for p in list(recent)[-4:] if p and p == topic.get("pillar"))
    if same:
        parts["repeat_pillar"] = round(max(0.7, 0.85 ** same), 2)
    feat = 1.0
    for k, v in parts.items():
        if k not in ("pillar_role", "repeat_pillar"):
            feat *= v
    # features overlap (a big-number hook is also a number hook): their product is bounded, never compounding
    m = parts["pillar_role"] * max(0.7, min(1.4, feat)) * parts.get("repeat_pillar", 1.0)
    return round(m, 3), parts


def type_boost(cfg):
    return read_json(_path(cfg), {}).get("type_boost", {})


def report_lines(cfg):
    g = read_json(_path(cfg), None) or analyse(cfg)
    pb = g.get("playbook") or []
    ms = g.get("metric_samples") or {}
    lines = ["## Growth agent", f"- {g['videos']} public video(s) analysed · median {g['median_views_per_day']} views/day "
             f"· groups need {g['min_sample']}+ videos to count", *[f"- {r}" for r in g["recommendations"]]]
    fb = {k: v for k, v in (g.get("feature_boost") or {}).items() if v["conclusive"]}
    lines.append("- Topic picker hook effects: " + (", ".join(f"{k} {v['with']}×/{v['without']}×" for k, v in fb.items())
                                                    if fb else "none conclusive yet (each side needs "
                                                    f"{g['min_sample']}+ videos)"))
    lines.append(f"- Samples: views {ms.get('views', '?')}, retention {ms.get('retention', '?')}, subscriber conversion "
                 f"{ms.get('subscribers', '?')} (YouTube Analytics; viewed-vs-swiped and impressions aren't in the API)")
    for r in pb:
        lines.append(f"- {r['action'].upper()}: {r['what']}: {r['evidence']} (n={r['n']}, confidence {r['confidence']})")
    if g["type_boost"]:
        lines.append("- Moment-type preference applied: " + ", ".join(f"{k} {v}×" for k, v in g["type_boost"].items()))
    return lines + [""]
