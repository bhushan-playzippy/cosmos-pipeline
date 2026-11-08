# 30-day growth plan: both channels (written 2026-10-29)

Goal: give each channel the best honest chance of 1,000 subscribers by **2026-11-28**. This is a stretch goal.
At today's conversion rates it would take roughly 200,000 to 800,000 views per channel, so the plan is built to
find out what converts, not to promise an outcome. No bought engagement, sub-for-sub, giveaways or comment spam.

## Baseline (verified 2026-10-29, Data API snapshots in `data/**/analytics/channel.csv`)

| | Universe in a Teaspoon (cosmic) | Unbelievably True (clips) |
|---|---|---|
| Subscribers | **6** (gap 994) | **7** (gap 993) |
| Lifetime views | 5,196 | 1,441 (true-story format since 26 Oct) |
| Subs per 1,000 views | ~1.2 | ~4.9 |
| Public Shorts | 15 | 10 (true stories) |
| Best | "One Teaspoon Weighs a Billion Tons" 1,335 · "780,000 km/kg: How India Beat Bike Mileage to Mars" 1,264 | "Mauled by a bear, left for dead, crawled 200 miles" 621 · "The Gestapo Put 5 Million Francs On Her Head" 389 |
| Failure rate (all jobs) | 10 quarantined + 3 failed of 34; 2 withdrawn for wrong numbers | 15 quarantined + 7 failed of 54 (mostly mismatched images) |

**Not available yet:** retention, viewed-vs-swiped-away, traffic source, geography and subscribers gained per
video. The YouTube Analytics token was never created. **Action for the owner:** run
`.venv/bin/python main.py auth-analytics` and `.venv/bin/python main.py --channel clips auth-analytics`. Until
then, "what converts" can only be inferred from views and likes, and every conclusion below is provisional.

Sample sizes are tiny (fewer than 20 public videos per channel, all under 4 days old). No pattern below is
statistically established.

## What the early data suggests (hypotheses, not findings)

- **Cosmic:** the two winners are concrete, *physical* comparisons with one surprising number in the title (mass
  of a teaspoon of neutron star; fuel cost per km). Abstract "vs." titles ("The Lifespan of a Red Dwarf vs. …")
  sit at 100–400 views. Hypothesis: **one object + one shocking everyday unit** beats two-thing comparisons.
- **Clips:** survival and "one person vs. an empire" stories lead. Disaster-mechanics stories (molasses flood) lag.
  Hypothesis: a **human protagonist with a visible ordeal** converts better than event explainers.
- Clips converts about 4× better per view than cosmic. Stories create a "channel identity" faster than
  standalone facts. Cosmic needs a stronger recurring identity (named series) to convert viewers into subscribers.

## Strategy: cosmic, "Universe in a Teaspoon"

- **Viewer:** a curious 16–40-year-old in the US, UK, Canada, Australia or Western Europe who likes "wait, what?"
  facts but not textbooks. English is the right language. These markets watch English Shorts natively, and
  splitting into other languages would fragment a 6-subscriber channel.
- **Promise (say it in every video's last line or caption):** "the universe, measured in things you own."
- **Series (already in `planning.series`), rotated by `role_for`:** *How Big Is It?* (size/distance),
  *Scale Explained* (one counterintuitive number unpacked), *Mission Files* (real missions: cost, speed, fuel).
- **One idea per Short.** The verify stage now rejects wrong numbers, so pick topics where the number is well
  established (agency pages) rather than estimates.
- **Units:** US units first with metric once (the `audience_rule` already in the prompts), e.g. "2 billion
  miles, about 3 billion km".
  Avoid India-only references unless the story is Indian. Use them where the story is Indian (the Mars-mileage
  Short did well), because they are content, not localisation.

## Strategy: clips, "Unbelievably True"

- **Viewer:** a 18–45-year-old US/UK history-curious viewer who watches survival, WWII and "forgotten hero" content.
- **Promise:** "real stories, checked against the record." Make the fact-checking visible as a trust signal
  (sources in the description, already done; "disputed" stated as disputed).
- **Pillars:** lead with *Impossible survivals* and *Forgotten heroes*, which are the current best, and keep
  *Disasters that hinged on one detail* as the 1-in-4 test pillar.
- **Visual constraint is the bottleneck:** most quarantines are mismatched images. Prefer pre-1930 or US-government
  events (public-domain photos exist). The `visual_question` scoring already says this, so keep the
  `visual_feasibility` weight high.

## Subscriber conversion (both channels)

- **One natural call to subscribe, not every video:** test it in the final line on alternating days (experiment
  E4). Never ask before the payoff.
- **Pinned comment:** a question that invites an answer (existing behaviour), not a subscribe plea.
- **Playlists per series** (existing `youtube.playlists` behaviour), so a viewer who likes one *Survived* story
  lands on the next.
- **Channel page:** the owner should set a banner and a description that state the promise, in Studio. The API
  can't change the name or picture.

## Experiments (change one variable per week; read results only with 5+ public videos per arm)

| Week | Dates | Cosmic test | Clips test | Read-out metric |
|---|---|---|---|---|
| 1 | 29 Oct–4 Nov | Baseline at 2/day, verify stage live. Title: number-first vs. question-first (alternate by slot) | Baseline at 2/day. Hook: "ordeal first" vs. "stakes question" | views at 48 h, likes/view; subs/1k views once Analytics is linked |
| 2 | 5–11 Nov | Slot: is 05:30 (US evening) or 23:30 IST (EU evening) stronger? US clocks changed on 1 Nov, so compare inside week 2 only | Same slot comparison | views at 48 h per slot |
| 3 | 12–18 Nov | Length: 35–42 s vs. 48–55 s (`qa.words` band per arm) | Length: 40 s vs. 55 s | average view %, which needs Analytics |
| 4 | 19–25 Nov | Ending: soft subscribe line ("Follow for the universe in things you own") on alternate days | Same, with "Follow for the next true story" | subs per 1k views, which needs Analytics |
| 26–28 Nov | | Review: keep winners, write the next month's plan | | |

**Decision rules:**

- Adopt a variant only if it wins by ≥30% on the median (not the mean, because one viral video skews it) with ≥5
  videos per arm.
- Otherwise call the test inconclusive and keep the default.
- Don't change topics *and* format in the same week.
- Log each week's result in the weekly report (`main.py weekly`).

## 30-day topic calendar (clusters by week; production picks the best-scored unused topic per slot)

60 slots per channel (2/day × 30). Run `.venv/bin/python main.py calendar` (and `--channel clips calendar`) for the
slot-by-slot plan. It is now per channel: `reports/[<id>/]CONTENT_CALENDAR.md`. Without `--fill` it makes no
model calls.

| Week | Cosmic cluster (series) | Clips cluster (pillar) |
|---|---|---|
| 1 | Densest things: neutron stars, white dwarfs, black hole mass (*How Big Is It?*) | Impossible survivals: castaways, frozen, lost at sea |
| 2 | Missions on a budget: fuel, cost and speed of real probes (*Mission Files*) | Forgotten heroes: spies, rescuers, whistle-blowers |
| 3 | Distances you can't imagine: light-time, driving and walking comparisons (*How Big Is It?*) | Disasters that hinged on one detail (with public-domain photos only) |
| 4 | Planet extremes: weather, days, gravity in everyday units (*Scale Explained*) | Mysteries finally solved (only where the solution is well documented) |

## Capacity and cost (free tier)

- Gemini free tier: about 20 requests/day per Flash model (2 strong models ≈ 40/day), shared by both channels on
  one key. A made video needs about 4–6 strong calls, plus about 2 more on a verify repair. At 2+2/day that is
  about 16–32 strong calls/day, which fits.
- The old 3+5/day needed 40–60 and silently fell back to Flash-Lite for fact-checks and reviews. The pipeline now
  defers instead.
- Deferrals did appear on 30 Oct (quota spent, Google overloaded). Fix 1 (code done, needs a key): a free
  **Groq** key (`GROQ_API_KEY`) takes fact-check and verify, plus anything Gemini can't serve, so Gemini only
  writes and reviews (~2 strong calls/video).
- If deferrals appear in reports, the lowest-cost fix is a **second free AI Studio project key for clips**
  (`gemini.api_key_env` in `channels/clips.yaml`), not a paid plan. Before going paid, check the terms: free-tier
  prompts may be used by Google to improve its products.
- Usage per stage is now logged as `llm_call` events in each channel's `events.jsonl`. Measure "published videos
  per strong call" after week 1.
- YouTube: 2+2 uploads/day is well inside the upload bucket. Other calls (sync, comments, reschedule ≈ 50 units)
  stay far below 10,000 units.
