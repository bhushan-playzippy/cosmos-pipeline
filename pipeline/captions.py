"""Word-by-word 'karaoke' captions in ASS format (the TikTok/Shorts style), plus a big hook headline at the top
for the first seconds. Why the headline: the swipe decision happens in about a second, before the narrated hook
finishes, and every opening frame used to be a bare photo (captions only start with the first spoken word).
Viewed-vs-swiped-away is the first gate in the Shorts feed, so the hook lands as text, picture and voice at once."""
import re


def _ts(t):
    t = max(0.0, t)
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:05.2f}"


def _clean(word, upper):
    w = word.replace("{", "").replace("}", "").replace("\\", "")
    w = re.sub(r"[\"“”‘’,;:.]+$", "", w)
    w = re.sub(r"^[\"“”‘’]+", "", w)
    return w.upper() if upper else w


def build_ass(timeline, cfg, out_path, hook=None):
    c = cfg["captions"]
    W, H = cfg["video"]["width"], cfg["video"]["height"]
    upper = cfg["channel"].get("language", "en") == "en"
    n = c.get("words_per_group", 3)
    h = c.get("hook", {})

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{c['font']},{c['size']},{c['color']},&H000000FF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,{c['outline']},3,2,70,70,{c['position_from_bottom']},1
Style: Hook,{c['font']},{h.get('size', 96)},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,3,{h.get('pad', 22)},0,8,90,90,{h.get('from_top', 260)},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    text = _hook_text(hook) if h.get("enabled", True) else ""
    if text and timeline:  # boxed headline over the first scene, gone before the second visual
        end = min(h.get("seconds", 2.8), timeline[0].get("duration") or h.get("seconds", 2.8))
        events.append(f"Dialogue: 1,{_ts(0)},{_ts(end)},Hook,,0,0,0,,{{\\fad(0,200)}}{text}")
    groups = []
    for sc in timeline:  # a group never runs across a sentence end: "…12,000 feet. Titan's…" on one line read as
        cur = []          # "≈12,000 FT TITAN'S", a false label (1 Nov, the reviewer failed the video for it)
        for w in (w for w in sc["words"] if _clean(w["word"], upper)):
            cur.append(w)
            if len(cur) == n or re.search(r"[.!?…][\"”’']?$", w["word"]):
                groups.append(cur)
                cur = []
        if cur:
            groups.append(cur)

    for gi, g in enumerate(groups):
        nxt = groups[gi + 1][0]["start"] if gi + 1 < len(groups) else g[-1]["end"] + 0.4
        g_end = nxt if nxt - g[-1]["end"] < 0.6 else g[-1]["end"] + 0.25
        for k, w in enumerate(g):
            start = w["start"]
            end = g[k + 1]["start"] if k + 1 < len(g) else g_end
            parts = []
            for j, x in enumerate(g):
                txt = _clean(x["word"], upper)
                parts.append(f"{{\\c{c['highlight']}}}{txt}{{\\c{c['color']}}}" if j == k else txt)
            pop = "{\\fscx88\\fscy88\\t(0,90,\\fscx100\\fscy100)}" if k == 0 else ""
            events.append(f"Dialogue: 0,{_ts(start)},{_ts(end)},Cap,,0,0,0,,{pop}{' '.join(parts)}")

    out_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    return out_path


def _hook_text(hook, max_words=7):
    """Upper-case, ASS-safe, short: a headline, not a sentence (a long one would cover the picture)."""
    words = re.sub(r"#\w+", "", str(hook or "")).replace("{", "").replace("}", "").replace("\\", "").split()
    if not words or len(words) > max_words:
        return ""
    return " ".join(words).upper().rstrip(".")
