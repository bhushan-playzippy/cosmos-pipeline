# Why the Shorts aren't going viral, what changes, and how 3–5 minute videos could work (2026-10-31)

This page covers both channels. The data comes from `data/**/analytics/videos.csv` (snapshots up to 30 Oct), the
opening frames of the rendered videos, and a read-only YouTube API check. Research sources are listed at the end.
Labels: **[YT]** means YouTube's own statements; **[guru]** means creator or vendor blogs, unproven;
**[ours]** means our own numbers. These are small samples, so read every pattern as a hypothesis.

## 1. Where the channels stand

| | Universe in a Teaspoon (cosmic) | Unbelievably True (clips) |
|---|---|---|
| Public Shorts / views / subs | 16 / 6,757 / 6 | 16 / 8,148 / 13 |
| Median views per Short | 337 | 403 |
| Best | Teaspoon of neutron star 1,336 · India's Mars mileage 1,265 | Joan of Arc's tower leap 1,377 · Dyatlov Pass 1,245 · Titanic band 1,201 |
| Worst | Kuiper Belt "drive across America" 54 · Andromeda light age 83 · sunlight 100,000 years 129 | 1904 marathon 0 · Halifax explosion 2 · Petrov 1983 112 |
| Trend | getting **worse** (first week 300–1,300, last 3 days 54–294) | getting **better** (28 Oct batch 818–1,377) |
| Likes per view | ~2–3% | ~2–3.5% |
| Comments | only our own pinned question, almost everywhere | same |

**Every Short follows the same curve.** It gets nearly all its views in the first 24–48 h, then stops completely. For example:
Neutron star went 0 → 1,141 → 1,262 → 1,325 → 1,336, and Kuiper Belt stalled at 54. That matches how YouTube describes
Shorts **[YT]**: each Short is shown to a small "seed" audience, and it only spreads if that audience chooses to watch
instead of swiping and then keeps watching. 100–1,000 views means **the test wasn't won. It is not a penalty or a ban.**
The 0- and 2-view clips videos are public with no restrictions (checked via the API); their seed test died at once.

## 2. Diagnosis: why they stop

1. **The first second is a bare photo.** On every video I sampled, frame 0 shows just an image. The captions only start
   with the first spoken word, and the same still image stays for ~3 s. On a black-and-white archive photo (1904
   marathon, Halifax) that reads as "old slideshow" and gets swiped. YouTube's first gate is *viewed vs swiped
   away* **[YT]**, decided in about a second, before our spoken hook finishes.
2. **Familiar subjects win, obscure ones lose.** This is the clearest pattern in our own data **[ours]**. Titanic, Joan of
   Arc, Caesar, Dyatlov Pass and neutron stars got 800–1,400 views. Halifax, the 1904 marathon, the molasses flood, Petrov and
   "light age" topics got 0–255. People stop for a name they already know, shown from an angle they don't.
3. **Vague titles hide the subject.** "They watched the fire, not knowing what was coming" (2 views) never says what it
   is about. The winners all name the subject ("Joan of Arc's 70-Foot Castle Tower Leap").
4. **Cosmic drifted into abstract "X vs Y" comparisons and India-only references.** Examples: the Mumbai trains Short
   (219 views), the Gaganyaan Short (381 views, 1% likes), Maharashtra. The audience is US/EU (rule added 27 Oct). Abstract
   time comparisons ("light that is 2.5 million years old") are the bottom of the list. One concrete object with one
   shocking everyday number wins (the teaspoon).
5. **Slideshow look.** Slow pans over stills, mostly black-and-white for clips. That causes swipe-aways, and it is also the
   wording of YouTube's "inauthentic content" policy ("image slideshows … with minimal narrative") **[YT]**. Our
   narration and fact-checking count as narrative value, but more visual variety (maps, annotated images,
   documents, a change every 2–3 s) is both a retention fix and a policy cushion.
6. **We are flying blind on the most important numbers.** We have no viewed-vs-swiped %, average % viewed, or
   subscribers per video, because YouTube Analytics was never linked. **Action for the owner:** run
   `main.py auth-analytics` and `main.py --channel clips auth-analytics`.
7. **Supply stalled.** Nothing was produced on 30–31 Oct because of Gemini quota and Google overload. That was fixed
   on 31 Oct with the Groq fallback.

Things that are **not** the problem: posting time and frequency (YouTube says no number of posts per day unlocks reach
**[YT]**), video length (35–55 s is fine), and the AI disclosure flag (YouTube says disclosure doesn't limit reach **[YT]**).
**Never delete and re-upload underperformers.** Since Oct 2026 YouTube reduces reach for re-uploads and favours original
Shorts **[press]**. Make a new, different Short instead.

## 3. What changed today (in the code, active from the next video)

- **Hook headline (`captions.hook`).** A big boxed headline of 3–6 words sits at the top of the frame for the first
  2.8 s (never past the first visual), e.g. "76 DAYS ALONE ON A RAFT". The text, the picture and the voice now land
  together in second one. It is written by the packaging step as `youtube.hook_text`. A short title is the fallback,
  and nothing is shown if the title is too long.
- **Titles must name the recognisable subject** (packaging prompt, with our own 2-vs-1,377 example).
- **Topic scoring: new `familiarity` score** (weight 2.0 on both channels). The idea generator is told to lead with
  subjects US/EU viewers already know, seen from a little-known angle.
- **Punch-in cuts (`video.max_shot_seconds: 2.8`).** A still scene longer than 2.8 s is cut into 2–3 shots of the
  same picture: wide first, then tighter framings of different parts of it. That gives a visual change about every
  2.5 s with no extra searches or AI calls. Video clips and to-scale graphics are never cut.
- Already in place: 12-word hooks, no date-first openings, endings that loop back into the hook, US-units audience
  rule, and the independent fact-check.

## 4. Next steps, by impact (not built yet)

1. **Owner: link YouTube Analytics on both channels.** Without it we can't tell a hook problem (low viewed %) from a
   pacing problem (low % viewed).
2. **A second, different image per long scene**, on top of the punch-in cuts. This costs more Commons/NASA searches
   and image checks, but no strong AI calls.
3. **Colour and contrast for clips:** prefer colour or colourised public-domain sources for the first shot (paintings,
   lithographs, Library of Congress colour plates), and use black-and-white in the middle.
4. **Named series** on screen and in titles ("Forgotten Heroes #4", "How Big Is It? #7"). A recurring format gives the
   seed audience a reason to subscribe.
5. **Cosmic: one object + one everyday unit** ("A teaspoon of X weighs Y"). Drop "light age"/"vs" abstractions and
   India-only comparisons unless the story itself is Indian (the Mars-mileage Short worked because ISRO *is* the story).
6. **Owner, in Studio:** remove "ISRO Chandrayaan" from the cosmic channel keywords (the target is US/EU now), and add a
   channel trailer.

Be honest about the target: at today's ~1–3.5 subscribers per 1,000 views, 1,000 subscribers needs roughly 300k–1M
views per channel. One Short that wins its seed test can do that, but nothing can guarantee one will.

## 5. 3–5 minute horizontal videos with free AI (research only, nothing built)

**Why bother:** long-form earns far more per view. Education/science long-form RPM is about $10 (median, wide range),
and history about $5–12. Shorts earn about 3–14% of that **[guru, AIR Media-Tech data]**. Long-form also builds watch
hours and converts viewers to subscribers better. **Caveat:** mid-roll ads only start at **8 minutes**, so 3–5 minutes is
a stepping stone. Move to 8–10 minutes once retention holds.

**Structure (~650–750 words ≈ 4.5–5 min at 150 words per minute):**

- **Space:**

  | Time | Section |
  |---|---|
  | 0:00–0:20 | Cold open with an everyday-scale shock and a promised payoff |
  | 0:20–1:10 | One familiar anchor object |
  | 1:10–2:10 | Escalation 1 |
  | 2:10–3:10 | The counter-intuitive twist |
  | 3:10–4:10 | What we don't know (a named mission) |
  | 4:10–4:45 | Callback to the opening image |

- **History:**

  | Time | Section |
  |---|---|
  | 0:00–0:25 | The moment of peril, told in present tense |
  | 0:25–1:00 | Who, where, when, with a map |
  | 1:00–2:00 | Inciting event and cliffhanger |
  | 2:00–3:15 | Two or three setbacks |
  | 3:15–4:15 | Resolution and aftermath |
  | 4:15–4:45 | What happened next, echoing the opening |

- **In both:** chapters every 45–90 s (they feed YouTube chapters), an open loop every 30–60 s ("but that wasn't the
  strangest part…"), and a visual change every 3–6 s.

**Free stack, per stage:**

| Stage | Free option | Notes |
|---|---|---|
| Research → outline → draft → polish | Groq `gpt-oss-120b` (1,000 req/day, 8k tokens/min) | already configured; space the calls; qwen as a second bucket |
| Fact-check + independent verify | Groq first, then Gemini | one batched call per script, not per claim |
| Final review (frames + audio) | Gemini | ~1–2 calls, frames sampled sparsely |
| Voice | Edge TTS (current) + **Kokoro-82M** (Apache-2.0, local) as a fallback | generate per paragraph, join with 250–400 ms pauses |
| Visuals (~40 assets for 5 min) | NASA library, ESA/ESO (CC BY: credit needed), Wikimedia Commons, Library of Congress, US National Archives, Pexels/Pixabay | avoid UK National Archives Flickr (non-commercial only) |
| Original graphics | **matplotlib + cartopy/Natural Earth maps**, timelines, to-scale diagrams, quote cards | cheap, original, and the best answer to the "slideshow" policy |
| AI images (optional) | FLUX.1-schnell (Apache-2.0) locally on the Mac | illustrative only, never as a "photo" of a real event; disclose |
| Music | our own synthesised tracks (Content-ID-safe) | needs 3–5 min loops with variation |
| Render | FFmpeg, clips in parallel, `h264_videotoolbox` | est. 5–15 min per video on an M-series Mac |
| Thumbnail | Pillow: best image + 2–4 bold words, 1280×720 | needs phone verification of the channel |

**Quota:** one long video ≈ 3–4 Groq calls plus 3–6 strong calls (verify and review). **One long video per week per
channel fits easily** next to 2 Shorts/day, run as a separate weekly job that defers instead of starving the Shorts.

**Risks:** the "inauthentic content" policy (July 2025, enforced against AI-slop channels in Jan 2026) targets
mass-produced slideshow/TTS videos. Each long video needs its own angle, original maps and diagrams, named sources, and
a human read-through before publishing. Archive images need their licence checked per file (`used_media.json` already
logs this).

**Recommended phases:**

0. **Owner:** phone-verify both channels; pick the top 5 Shorts topics per channel.
1. **Weeks 1–4, "expand the winners":** one long video per week per channel from a proven Short (Joan of Arc, Dyatlov,
   neutron stars), with human approval. Link the matching Shorts to it with "Related video" (set by hand in Studio).
2. **Weeks 5–8, long-form becomes the source:** cut 3–4 Shorts from each long script (cold open, best twist, payoff).
   This means fewer AI calls per Short, and excerpted Shorts convert viewers better.
3. **Month 3+:** if average view % stays at 40% or more, try 8–10 minute videos for mid-roll ads.

## Sources

- YouTube Shorts Q&A (Todd Sherman, Creator Insider): https://www.youtube.com/watch?v=n3jsYK_-aRU
- Viewed vs swiped away: https://support.google.com/youtube/answer/12942217
- YPP inauthentic content policy: https://support.google.com/youtube/answer/1311392
- Altered/synthetic disclosure: https://support.google.com/youtube/answer/14328491
- Shorts originality change (Oct 2026): https://9to5google.com/2026/10/02/youtube-wont-recommend-unoriginal-shorts/
- Shorts view-count change: https://techcrunch.com/2025/03/26/youtube-is-changing-how-youtube-shorts-views-are-counted
- AI slop enforcement 2026: https://www.cnbc.com/2026/01/21/youtube-chief-says-managing-ai-slop-is-a-priority-for-2026-.html
- Shorts vs long-form RPM: https://air.io/en/air-data-findings/youtube-shorts-rpm-vs-long-form-how-much-do-shorts-earn-in-2026
- Mid-roll at 8 min: https://www.tubefilter.com/2020/07/07/youtube-lowering-minimum-video-length-mid-roll-ads/
- Groq limits: https://console.groq.com/docs/rate-limits
- Kokoro vs Piper TTS: https://www.promptquorum.com/power-local-llm/piper-vs-kokoro-tts
- FLUX.1-schnell licence: https://huggingface.co/black-forest-labs/FLUX.1-schnell
- NASA media usage: https://nasa.gov/nasa-brand-center/images-and-media
- Library of Congress rights: https://www.loc.gov/rr/print/195_copr.html
- National Archives photos: https://www.archives.gov/global-pages/publish-photos.html
