"""Editorial agent: the original contribution that makes a clip a new work rather than a re-upload.

For each segment it writes (one model call):
  intro   context + hook in the channel's own voice, spoken before the clip
  outro   explanation of why the moment matters / the payoff, spoken after the clip
  titles  options, plus a debate question for the pinned comment
Rules: only facts stated in the clip or the source description; never put words in the speaker's mouth; if the
clip can't be framed honestly, the model must say so (feasible: false) and the segment is rejected.

YouTube's reused-content policy applies "even if you have permission", so this step is not optional: no
narration, no Short.
"""
from ..llm import ask_json
from ..util import log

SYSTEM = """You are the host of a Shorts channel that explains remarkable moments from authorized source videos.
You add real context and explanation in your own words. You never exaggerate, never invent facts, and never
misrepresent what the speaker said or meant."""


class NotFeasible(RuntimeError):
    pass


def write(seg, src, cfg):
    ch = cfg["channel"]
    e = cfg["editorial"]
    prompt = f"""
Channel: "{ch['name']}" — {ch['niche']}. Audience: {ch['audience']}.

SOURCE: "{src.get('title')}" by {src.get('creator')} ({src.get('published', '')}).
SOURCE DESCRIPTION (facts you may use): {src.get('description', '')[:1200]}

CLIP TRANSCRIPT (exact words of the speaker, {seg['end'] - seg['start']:.0f}s):
"{seg['text']}"

STYLE: {e.get("style", "Explain the moment clearly, in your own words.")}

Write narration that wraps this clip:
- intro: {e.get('intro_words', [12, 30])[0]}–{e.get('intro_words', [12, 30])[1]} words, spoken BEFORE the clip. Set up
  the moment: who is speaking (as the source describes them) and why the viewer should care. Make the first
  sentence a strong, honest hook. Do not repeat the clip's own words.
- outro: {e.get('outro_words', [20, 45])[0]}–{e.get('outro_words', [20, 45])[1]} words, spoken AFTER the clip. Explain
  what it means, why it matters, or the surprising implication. Add value the clip alone doesn't give.
- Only use facts stated in the transcript or the source description. If you need a fact that isn't there, leave
  it out. Do not quote the speaker saying anything they did not say.
- title_options: 3 titles, max 60 chars, honest curiosity, matching what the clip actually shows.
- pinned_comment: one question that invites discussion, max 140 chars.
- claims: every factual claim you made in intro/outro, each with where it comes from ("transcript" or "description").
- feasible: false if this clip can't be framed honestly as a standalone Short (missing context, misleading out of
  context, nothing to explain). Then explain why in "reason".

Return JSON:
{{"intro": "...", "outro": "...", "title_options": ["..."], "pinned_comment": "...",
  "claims": [{{"claim": "...", "from": "transcript"}}], "feasible": true, "reason": ""}}
"""
    out = ask_json(prompt, cfg, system=e.get("system", SYSTEM), temperature=0.6)
    if isinstance(out, list):
        out = out[0] if out and isinstance(out[0], dict) else {}
    if not out.get("feasible", False):
        raise NotFeasible(out.get("reason") or "editor judged the clip not framable")
    for k in ("intro", "outro"):
        lo, hi = e.get(f"{k}_words", [12, 30] if k == "intro" else [20, 45])
        n = len((out.get(k) or "").split())
        if not lo <= n <= hi + 10:
            raise NotFeasible(f"{k} has {n} words (wanted {lo}–{hi})")
    bad = [c for c in out.get("claims", []) if c.get("from") not in ("transcript", "description")]
    if bad:
        raise NotFeasible(f"unsourced claims: {[c.get('claim') for c in bad]}")
    log(f"editorial: intro {len(out['intro'].split())}w, outro {len(out['outro'].split())}w, "
        f"{len(out.get('claims', []))} sourced claim(s)")
    return out
