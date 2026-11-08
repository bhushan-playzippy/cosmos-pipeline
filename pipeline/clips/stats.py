"""Creator-stats Shorts: original videos about famous creators built only from public YouTube numbers.

No one else's footage, thumbnails, logos or likeness is used: the visuals are our own stat cards, the narration
is ours, the numbers come from the YouTube Data API at production time. This is the clip channel's format when
there's no permission-cleared clip to make (stats.enabled), so the channel can publish without reusing anyone's
content.

Accuracy rule: the model writes words, never numbers. Every number on screen is rendered by code from the fact
pack, and every number spoken in the narration must match a fact's spoken form, or the Short is rejected.
"""
import datetime as dt
import random
import re
import shutil
import traceback

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import registry
from .. import captions, control, jobs, qa, render, voice
from .. import orchestrator as core
from ..llm import ask_json
from ..util import ASSETS, read_json, slugify, write_json, log, event

W, H = 1080, 1920
BG, PINK, CYAN, YELLOW, GREY = (10, 8, 20), (255, 46, 136), (0, 220, 255), (255, 214, 0), (190, 195, 220)


# ---------------------------------------------------------------- numbers

def human(n):
    """312,456,789 → ("312.5M", "312 million"): display and spoken forms."""
    n = float(n)
    for div, suf, word in ((1e9, "B", "billion"), (1e6, "M", "million"), (1e3, "K", "thousand")):
        if abs(n) >= div:
            v = n / div
            num = (f"{v:.2f}" if v < 10 else f"{v:.1f}").rstrip("0").rstrip(".")
            disp, spoken = num + suf, f"{num} {word}"
            return disp, spoken
    return f"{n:,.0f}", f"{n:,.0f}"


def clean(text):
    """Drops emoji and symbols the card font can't draw (they'd render as empty boxes)."""
    return re.sub(r"\s+", " ", re.sub(r"[^\x20-\x7E\u00A0-\u024F\u2018-\u201D\u2026]", "", text)).strip()


def _fact(fid, label, value, creator=None, unit=""):
    label = clean(label)
    disp, spoken = human(value)
    return {"id": fid, "label": label, "value": value, "display": disp + unit, "spoken": spoken + unit, "creator": creator}


def channel_stats(cfg, ids):
    from .discovery import _yt
    from ..publish_youtube import with_retry
    _yt.key_env = cfg["discovery"].get("api_key_env")
    yt = _yt()
    r = with_retry(lambda: yt.channels().list(part="statistics,snippet", id=",".join(ids)).execute(), "creator stats")
    return {c["id"]: {"title": c["snippet"]["title"], "subs": int(c["statistics"].get("subscriberCount", 0)),
                      "views": int(c["statistics"].get("viewCount", 0)),
                      "videos": int(c["statistics"].get("videoCount", 0))} for c in r.get("items", [])}


def _recent(cfg):
    by = {}
    for s in registry.sources(cfg).values():
        if s.get("platform") in ("youtube", "file") and s.get("creator_channel_id") and s.get("published"):
            by.setdefault(s["creator_channel_id"], []).append(s)
    for v in by.values():
        v.sort(key=lambda s: s["published"], reverse=True)
    return by


def fact_packs(cfg, today=None):
    """All angles we can make from current data. Each: key, headline, creator(s), facts, card plan."""
    today = today or dt.date.today()
    recent = _recent(cfg)
    stats = channel_stats(cfg, list(recent)) if recent else {}
    packs = []
    for cid, vids in recent.items():
        st = stats.get(cid)
        if not st or not vids:
            continue
        name = st["title"]
        latest = vids[0]
        days = max(1, (today - dt.date.fromisoformat(latest["published"])).days)
        per_day = latest["views"] / days
        facts = [_fact("subs", f"{name} subscribers", st["subs"], name),
                 _fact("latest_views", f"views on \"{latest['title'][:60]}\"", latest["views"], name),
                 {"id": "days", "label": "days since upload", "value": days, "display": str(days),
                  "spoken": str(days), "creator": name},
                 _fact("per_day", "views per day", per_day, name)]
        packs.append({"key": f"pace:{latest['id']}", "headline": f"How fast is {name}'s newest video growing?",
                      "creators": [name], "facts": facts,
                      "cards": [("big", "subs"), ("big", "latest_views"), ("big", "days"), ("big", "per_day")]})
        best = max(vids, key=lambda s: s["views"])
        worst = min(vids, key=lambda s: s["views"])
        if best["id"] != worst["id"] and worst["views"] > 0:
            ratio = best["views"] / worst["views"]
            facts = [_fact("best", f"views on \"{best['title'][:60]}\"", best["views"], name),
                     _fact("worst", f"views on \"{worst['title'][:60]}\"", worst["views"], name),
                     {"id": "ratio", "label": "times more views", "value": ratio,
                      "display": f"{ratio:.1f}x", "spoken": f"{ratio:.1f} times", "creator": name},
                     {"id": "n", "label": "latest uploads compared", "value": len(vids), "display": str(len(vids)),
                      "spoken": str(len(vids)), "creator": name}]
            packs.append({"key": f"range:{cid}:{best['id']}:{worst['id']}",
                          "headline": f"{name}'s biggest vs smallest recent video", "creators": [name],
                          "facts": facts, "cards": [("big", "n"), ("big", "best"), ("big", "worst"), ("big", "ratio")]})
    packs += hilo_packs(recent, stats, today)
    names = [(cid, stats[cid]) for cid in recent if cid in stats]
    if len(names) >= 3:
        avg = {cid: sum(v["views"] for v in recent[cid]) / len(recent[cid]) for cid, _ in names}
        ranked = sorted(names, key=lambda x: -avg[x[0]])
        facts = [_fact(f"avg{i}", f"{st['title']} average views (last {len(recent[cid])} videos)", avg[cid], st["title"])
                 for i, (cid, st) in enumerate(ranked)]
        packs.append({"key": f"leaderboard:{today.isoformat()}", "headline": "Who gets the most views per video right now?",
                      "creators": [st["title"] for _, st in ranked], "facts": facts,
                      "cards": [("bars", [f["id"] for f in facts])] + [("big", f["id"]) for f in facts[:3]]})
    for (a, sa), (b, sb) in [(names[i], names[j]) for i in range(len(names)) for j in range(i + 1, len(names))]:
        facts = [_fact("a_subs", f"{sa['title']} subscribers", sa["subs"], sa["title"]),
                 _fact("b_subs", f"{sb['title']} subscribers", sb["subs"], sb["title"]),
                 _fact("a_latest", f"{sa['title']} newest video views", recent[a][0]["views"], sa["title"]),
                 _fact("b_latest", f"{sb['title']} newest video views", recent[b][0]["views"], sb["title"])]
        packs.append({"key": f"versus:{a}:{b}:{recent[a][0]['id']}:{recent[b][0]['id']}",
                      "headline": f"{sa['title']} vs {sb['title']}: who's winning right now?",
                      "creators": [sa["title"], sb["title"]], "facts": facts,
                      "cards": [("versus", ["a_subs", "b_subs"]), ("versus", ["a_latest", "b_latest"])]})
    return packs


def hilo_packs(recent, stats, today, rounds=3, n=12):
    """'Higher or Lower' games: which of two real videos got more views? Pairs come from different creators and
    differ by 1.25–6× so the answer isn't obvious and isn't a coin flip either."""
    vids = [v for cid, vs in recent.items() if cid in stats for v in vs if v.get("views")]
    rnd = random.Random(today.toordinal())
    pairs = []
    for _ in range(400):
        a, b = rnd.sample(vids, 2) if len(vids) >= 2 else (None, None)
        if not a or a["creator_channel_id"] == b["creator_channel_id"]:
            continue
        r = max(a["views"], b["views"]) / min(a["views"], b["views"])
        if 1.25 <= r <= 6 and not any({a["id"], b["id"]} & {x["id"], y["id"]} for x, y in pairs):
            pairs.append((a, b))
    packs = []
    for g in range(min(n, len(pairs) // rounds)):
        game = pairs[g * rounds:(g + 1) * rounds]
        facts, cards = [], []
        for i, (a, b) in enumerate(game):
            for side, v in (("a", a), ("b", b)):
                facts.append(_fact(f"r{i}{side}", f"\"{v['title'][:70]}\"", v["views"], stats[v["creator_channel_id"]]["title"]))
            cards += [("hilo_q", [f"r{i}a", f"r{i}b"]), ("hilo_a", [f"r{i}a", f"r{i}b"])]
        key = "hilo:" + ":".join(sorted(v["id"] for pair in game for v in pair))
        creators = list(dict.fromkeys(f["creator"] for f in facts))
        packs.append({"key": key, "headline": "Which video got more views?", "creators": creators,
                      "facts": facts, "cards": cards, "game": True})
    return packs


def next_pack(cfg):
    used = set(read_json(cfg["_paths"]["data"] / "stats_used.json", []))
    packs = [p for p in fact_packs(cfg) if p["key"] not in used]
    recent = [j for j in sorted(jobs.load(), key=lambda j: j.get("created", ""))
              if j.get("format") == "creator_stats" and j["status"] not in ("rejected", "failed")][-5:]
    recent_creators = [c for j in recent for c in j.get("creators", [])]
    recent_angles = [j.get("pack", "").split(":")[0] for j in recent]
    random.Random(len(used)).shuffle(packs)
    # vary the format first (pace / versus / leaderboard / range), then spread creators across the day
    packs.sort(key=lambda p: (recent_angles.count(p["key"].split(":")[0]),
                              sum(c in recent_creators for c in p["creators"])))
    return packs[0] if packs else None


# ---------------------------------------------------------------- cards

def _font(size):
    return ImageFont.truetype(str(ASSETS / "fonts" / "Poppins-Bold.ttf"), size)


def _backdrop(seed):
    rnd = random.Random(seed)
    img = Image.new("RGB", (W, H), BG)
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    for color in ((120, 20, 90), (10, 70, 120), (80, 20, 140)):
        cx, cy, r = rnd.uniform(0.1, 0.9), rnd.uniform(0.1, 0.9), rnd.uniform(0.3, 0.5)
        g.ellipse([W * cx - W * r, H * cy - W * r, W * cx + W * r, H * cy + W * r], fill=color)
    img = Image.blend(img, glow.filter(ImageFilter.GaussianBlur(W // 8)), 0.75)
    d = ImageDraw.Draw(img)
    for _ in range(40):
        x, y, ln = rnd.randint(-200, W), rnd.randint(0, H), rnd.randint(80, 260)
        d.line([x, y, x + ln, y - ln * 0.35], fill=rnd.choice([(90, 30, 70), (20, 70, 100), (70, 70, 90)]), width=3)
    return img


def _fit(d, text, max_w, size, min_size=40):
    while size > min_size and d.textlength(text, font=_font(size)) > max_w:
        size -= 4
    return _font(size)


def _wrap(d, text, max_w, size):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip()
        if d.textlength(t, font=_font(size)) <= max_w:
            cur = t
        else:
            lines.append(cur)
            cur = w
    return lines + ([cur] if cur else [])


def card(kind, facts, headline, path, seed):
    img = _backdrop(seed)
    d = ImageDraw.Draw(img)
    d.text((W / 2, 230), "PEAK MOMENTS WORLD · STATS", font=_font(34), fill=GREY, anchor="mm")
    y = 420
    if kind == "title":
        for line in _wrap(d, headline.upper(), W - 160, 92)[:4]:
            d.text((W / 2, y), line, font=_font(92), fill="white", anchor="mm")
            y += 112
        d.rectangle([W / 2 - 120, y + 20, W / 2 + 120, y + 30], fill=PINK)
    elif kind == "big":
        f = facts[0]
        d.text((W / 2, 560), f["display"], font=_fit(d, f["display"], W - 120, 230), fill=YELLOW, anchor="mm")
        for i, line in enumerate(_wrap(d, f["label"], W - 160, 54)[:3]):
            d.text((W / 2, 760 + i * 70), line, font=_font(54), fill="white", anchor="mm")
    elif kind == "versus":
        a, b = facts
        for i, f in enumerate((a, b)):
            cy = 560 + i * 420
            d.text((W / 2, cy - 110), f["creator"].upper(), font=_fit(d, f["creator"].upper(), W - 160, 70),
                   fill=(PINK, CYAN)[i], anchor="mm")
            d.text((W / 2, cy + 20), f["display"], font=_fit(d, f["display"], W - 160, 170), fill="white", anchor="mm")
            d.text((W / 2, cy + 130), f["label"].replace(f["creator"], "").strip(), font=_font(40), fill=GREY, anchor="mm")
        d.text((W / 2, 770), "VS", font=_font(80), fill=YELLOW, anchor="mm")
    elif kind in ("hilo_q", "hilo_a"):
        d.text((W / 2, 330), "WHICH GOT MORE VIEWS?", font=_font(64), fill=YELLOW, anchor="mm")
        win = max(facts, key=lambda f: f["value"])
        for i, f in enumerate(facts):
            top = 430 + i * 470
            box = [80, top, W - 80, top + 380]
            hot = kind == "hilo_a" and f is win
            d.rounded_rectangle(box, radius=36, fill=(40, 20, 60) if not hot else (70, 20, 60),
                                outline=(PINK, CYAN)[i] if not hot else YELLOW, width=6 if not hot else 12)
            d.text((W / 2, top + 60), f["creator"].upper(), font=_fit(d, f["creator"].upper(), W - 220, 58),
                   fill=(PINK, CYAN)[i], anchor="mm")
            for k, line in enumerate(_wrap(d, f["label"], W - 240, 46)[:3]):
                d.text((W / 2, top + 140 + k * 56), line, font=_font(46), fill="white", anchor="mm")
            shown = f["display"] + " views" if kind == "hilo_a" else "? ? ?"
            d.text((W / 2, top + 320), shown, font=_font(70), fill=YELLOW if kind == "hilo_a" else GREY, anchor="mm")
        d.text((W / 2, 855), "OR", font=_font(60), fill="white", anchor="mm")
    elif kind == "bars":
        top = max(f["value"] for f in facts) or 1
        for i, f in enumerate(facts[:6]):
            cy = 470 + i * 140
            w = int((W - 200) * f["value"] / top)
            d.rounded_rectangle([100, cy, 100 + max(w, 30), cy + 70], radius=18, fill=(PINK, CYAN, YELLOW)[i % 3])
            d.text((110, cy - 34), f["creator"], font=_font(40), fill="white", anchor="lm")
            d.text((W - 100, cy - 34), f["display"], font=_font(40), fill=YELLOW, anchor="rm")
        d.text((W / 2, 470 + 6 * 140), "average views, latest uploads", font=_font(40), fill=GREY, anchor="mm")
    elif kind == "question":
        for line in _wrap(d, headline, W - 160, 76)[:5]:
            d.text((W / 2, y + 60), line, font=_font(76), fill="white", anchor="mm")
            y += 96
        d.text((W / 2, y + 160), "COMMENT BELOW", font=_font(52), fill=PINK, anchor="mm")
    img.save(path)
    return path


# ---------------------------------------------------------------- writing + checks

NUM = re.compile(r"\d[\d,.]*\d|\d", re.I)
SUFFIX = re.compile(r"\s*(million|billion|thousand|times|percent|[mkbx%])\b", re.I)


def _norm(s):
    s = s.lower().replace(",", "").strip()
    return re.sub(r"\s+", " ", s)


def unsupported_numbers(text, pack):
    """Numbers in the narration that aren't one of the facts (spoken or display form). Small counts (≤10) and
    years are allowed; everything else must come from the data."""
    ok = {_norm(f["spoken"]) for f in pack["facts"]} | {_norm(f["display"]) for f in pack["facts"]}
    ok |= {o.replace(" ", "") for o in ok}
    bad = []
    for m in NUM.finditer(text):
        num = m.group(0).rstrip(".")
        suf = SUFFIX.match(text, m.end())
        t = _norm(num + (suf.group(0) if suf else ""))
        if t in ok or t.replace(" ", "") in ok:
            continue
        if not suf and (re.fullmatch(r"\d{1,2}", num) and int(num) <= 10 or re.fullmatch(r"(19|20)\d\d", num)):
            continue
        bad.append(t)
    return bad


# Wording the numbers can't support: rounded counts aren't "exact", 2 days isn't "instantly", and rank/money claims
# need data we don't have.
BANNED = re.compile(r"\b(exactly|precisely|instantly|overnight|in seconds|king of|the biggest|biggest youtuber|"
                    r"record[- ]breaking|world record|richest|earn(s|ed)?|money|dollars?|\$|net worth|never (truly )?"
                    r"loses?|best youtuber|number one|#1|always|never|every single|today|tonight|records?)\b", re.I)


FILLER_OK = {"the", "a", "an", "and", "of", "to", "in", "on", "is", "his", "her", "their", "views", "video", "videos",
             "this", "that", "with", "for", "it", "at", "he", "she", "they", "than", "more", "million", "thousand",
             "per", "day", "days", "subscribers", "upload", "uploads", "latest", "new", "newest", "you", "your", "who"}


def problems(words, pack):
    text = " ".join(words.get("scenes", []) + words.get("title_options", [])[:1])
    out = [f"number not in the data: {n}" for n in unsupported_numbers(text, pack)]
    out += [f"unsupported claim word: '{m.group(0)}'" for m in BANNED.finditer(text)]
    names = " ".join(pack.get("creators", [])).lower()  # "speed" is part of "ishowspeed": names never count
    counts = {}
    for w in re.findall(r"[a-z']+", " ".join(words.get("scenes", [])).lower()):
        if w not in FILLER_OK and w not in names and len(w) > 3:  # substring test against the names
            counts[w] = counts.get(w, 0) + 1
    out += [f"'{w}' is used {n} times (vary the wording)" for w, n in counts.items() if n >= 3]
    return out


def write(pack, cfg, attempts=2):
    """Writes the narration; on a problem, rewrites once with the problems listed. Raises if still not clean."""
    feedback = ""
    issues = []
    for _ in range(attempts):
        try:
            out = _write(pack, cfg, feedback)
        except ValueError as e:  # wrong scene count: ask again
            issues, feedback = [str(e)], f"Your previous draft was invalid: {e}. Return exactly the scenes asked for."
            continue
        issues = problems(out, pack)
        if not issues:
            return out
        feedback = "Your previous draft had these problems, fix them all: " + "; ".join(issues)
        log(f"stats writer: {feedback[:160]}")
    raise ValueError(f"narration still not supported by the data: {issues}")


def _write(pack, cfg, feedback=""):
    n_cards = len(pack["cards"])
    facts = "\n".join(f"- [{f['id']}] {f['label']}: say \"{f['spoken']}\"" for f in pack["facts"])
    plan = "\n".join(f"scene {i + 1}: card shows {k} → {ids if isinstance(ids, list) else [ids]}"
                     for i, (k, ids) in enumerate(pack["cards"]))
    prompt = f"""
You write a {n_cards + 2}-scene YouTube Short for "Peak Moments World", a channel about big YouTubers and streamers.
Angle: {pack['headline']}
FACTS (public YouTube numbers, today). When you say a number, say it EXACTLY as written in quotes. Use no other
numbers at all: no estimates, no money, no rankings beyond these facts:
{facts}

Scenes: scene 0 = hook (no card numbers yet, make people stay), then
{plan}
{"This is a HIGHER-OR-LOWER GAME. On a hilo_q scene, read the two video titles (short) and tell viewers to guess "
 "(e.g. 'lock in your answer'), and END the scene with the words 'three, two, one'; say NO other numbers and "
 "don't hint at the winner. On a hilo_a scene, reveal: say both "
 "view counts and which one won. Keep score tension across rounds." if pack.get("game") else ""}
last scene = a punchy takeaway + a question for the comments.
Each scene 1–2 short sentences, 9–20 words, casual, energetic, fan-to-fan. Never insult the creators, never claim
anything about their lives, money or plans. Don't say "subscribe". Numbers are rounded, so never say "exactly";
don't call anyone "the king", "the biggest" or "number one"; describe speed only with the given days.
Titles must describe this exact angle (e.g. one video's pace, a head-to-head), not a bigger claim.
{feedback}

Return JSON: {{"scenes": ["...", "..."], "title_options": ["max 60 chars", "...", "..."],
  "question": "the comment question (max 90 chars)"}}
"""
    out = ask_json(prompt, cfg, temperature=0.8)
    if isinstance(out, list):
        out = out[0] if out and isinstance(out[0], dict) else {}
    scenes = [re.sub(r'"(\d[^"]*)"', r"\1", s).strip() for s in out.get("scenes", []) if isinstance(s, str) and s.strip()]
    if pack.get("game"):  # the question scenes must not leak the answer
        qs = [scenes[i + 1] for i, (k, _) in enumerate(pack["cards"]) if k == "hilo_q" and i + 1 < len(scenes)]
        if any(NUM.search(q) and not re.fullmatch(r"\D*\b([1-3]|one|two|three)\b\D*", q) for q in qs if re.search(r"\d", q)):
            raise ValueError("a question scene reveals a number")
    if len(scenes) != n_cards + 2:
        raise ValueError(f"writer returned {len(scenes)} scenes, wanted {n_cards + 2}")
    return out | {"scenes": scenes}


def package(pack, words, cfg, when):
    from .package import used_titles, _norm as tnorm
    taken = used_titles()
    title = next((t.strip()[:70] for t in words.get("title_options", []) if t and tnorm(t) not in taken), None)
    if not title:
        raise ValueError("no unique title")
    creators = ", ".join(pack["creators"])
    tags = list(dict.fromkeys([*pack["creators"], *cfg["package"].get("base_tags", []), "youtuber stats"]))[:15]
    hashtags = ["#Shorts", "#YouTubers"] + [f"#{re.sub(r'[^A-Za-z0-9]', '', c)}" for c in pack["creators"][:1]]
    desc = "\n".join([words["scenes"][0], "", words.get("question", ""), "",
                      f"📊 Numbers: public YouTube counts as of {when:%d %b %Y, %H:%M} IST. They change fast.",
                      f"Not affiliated with or endorsed by {creators}. Original commentary and graphics.", "",
                      cfg.get("disclosure", {}).get("stats_text", "Voiceover is AI-generated."), "",
                      " ".join(hashtags[:3])])
    return {"title": title, "description": desc[:4900], "tags": tags, "pinned_comment": words.get("question", "")[:140]}


# ---------------------------------------------------------------- production

def make(cfg, slot=None):
    pack = next_pack(cfg)
    if not pack:
        log("stats: no unused fact pack (run discover to refresh creator data)")
        return None
    role = jobs.role_for(cfg, slot) if slot else None
    folder = core.OUTPUT / f"{jobs.now():%Y-%m-%d_%H%M%S}_stats-{slugify(pack['headline'], 40)}"
    work = folder / "_work"
    work.mkdir(parents=True, exist_ok=True)
    jobs.upsert({"folder": folder.name, "topic": pack["headline"], "status": "failed", "stage": "write", "role": role,
                 "slot": slot.isoformat() if slot else None, "created": jobs.stamp(), "stages": {},
                 "youtube_id": None, "format": "creator_stats", "creators": pack["creators"], "pack": pack["key"]})
    stage = "write"
    try:
        words = write(pack, cfg)
        stage = "package"
        meta = package(pack, words, cfg, jobs.now())
        stage = "cards"
        byid = {f["id"]: f for f in pack["facts"]}
        plan = [("title", [])] + [(k, ids if isinstance(ids, list) else [ids]) for k, ids in pack["cards"]] + \
               [("question", [])]
        scenes = []
        for i, ((kind, ids), text) in enumerate(zip(plan, words["scenes"])):
            head = words.get("question") if kind == "question" else pack["headline"]
            img = card(kind, [byid[x] for x in ids], head, work / f"card_{i:02d}.png", seed=hash(pack["key"]) + i)
            scenes.append({"text": text, "media": str(img), "media_kind": "image"})
        stage = "voice"
        timeline, wav = voice.synthesize(scenes, cfg, work)
        stage = "render"
        ass = captions.build_ass(timeline, cfg, work / "captions.ass")
        video = render.render(timeline, wav, ass, cfg, work, folder / "video.mp4")
        control.count("renders")
        stage = "qa"
        checks = [{"name": n, "ok": bool(ok), "detail": str(d or "")} for n, ok, d in qa.technical(video, cfg)]
        issues = problems(words, pack)
        checks.append({"name": "narration supported by the data", "ok": not issues,
                       "detail": "; ".join(issues) or "every number and claim word checked against the fact pack"})
        checks.append({"name": "title unique", "ok": True, "detail": meta["title"]})
        result = {"pass": all(c["ok"] for c in checks), "checks": checks}
        for i, sc in enumerate(scenes):
            shutil.copy(sc["media"], folder / f"card_{i:02d}.png")
        script = {"kind": "creator_stats", "topic": meta["title"], "youtube": meta, "pack": pack, "words": words,
                  "compliance": result, "sources": ["YouTube Data API public statistics"],
                  "scenes": [{"text": s} for s in words["scenes"]]}
        write_json(folder / "script.json", script)
        (folder / "post.txt").write_text("\n".join(
            ["=== YOUTUBE ===", meta["title"], "", meta["description"], "", "TAGS: " + ", ".join(meta["tags"]), "",
             "=== FACTS (from the API) ===", *[f"[{f['id']}] {f['label']}: {f['display']}" for f in pack["facts"]], "",
             "=== CHECKS ===", *[f"[{'ok' if c['ok'] else 'FAIL'}] {c['name']}: {c['detail']}" for c in checks], "",
             "=== NARRATION ===", *words["scenes"]]), encoding="utf-8")
        shutil.rmtree(work, ignore_errors=True)
        used = read_json(cfg["_paths"]["data"] / "stats_used.json", [])
        write_json(cfg["_paths"]["data"] / "stats_used.json", used + [pack["key"]])
        reason = None if result["pass"] else "checks: " + ", ".join(c["name"] for c in checks if not c["ok"])
        status = ("quarantined" if reason else "dry_run" if control.DRY_RUN else
                  "awaiting_approval" if cfg.get("review", {}).get("human_approval", True) else "ready")
        control.count("videos_quarantined" if reason else "videos_made")
        event("short_made", folder=folder.name, status=status, reason=reason, format="creator_stats", pack=pack["key"])
        log(f"{status.upper()} → {folder}" + (f" ({reason})" if reason else ""))
        return jobs.mark(folder.name, status=status, stage="gates", reason=reason, title=meta["title"])
    except SystemExit:
        raise
    except Exception as e:
        log(f"stats FAILED at '{stage}': {e}")
        (folder / "error.txt").write_text(traceback.format_exc(), encoding="utf-8")
        shutil.rmtree(work, ignore_errors=True)
        event("short_failed", folder=folder.name, stage=stage, error=f"{type(e).__name__}: {e}"[:300])
        return jobs.mark(folder.name, status="failed", stage_failed=stage, reason=f"{type(e).__name__}: {e}"[:300])
