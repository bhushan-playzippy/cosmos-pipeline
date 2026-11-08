# Monetization strategy

All policy facts come from `docs/research/YOUTUBE_POLICY_NOTES.md`, which was checked against the YouTube Help Center on 2026-10-24, with sources listed there. Revenue figures here are **not** forecasts; see `REVENUE_AND_UNIT_ECONOMICS.md` for the labelled scenarios.

## Where the channel stands (2026-10-24)

| Milestone | Requirement (official) | Channel today |
|---|---|---|
| YPP fan-funding tier (no ad revenue) | 500 subs, 3 public uploads in 90 days, and 3,000 public watch hours in 12 months or 3M Shorts views in 90 days | 0 subs |
| YPP ad-revenue tier (apply before 2027-01-31) | 1,000 subs, and 4,000 public **long-form** watch hours in 12 months or **10M** public Shorts views in 90 days | 0 subs |
| YPP ad-revenue tier (apply from 2027-02-01) | 1,000 subs, and 8,000 hours or **20M** Shorts views in 90 days | — |
| Shorts revenue, monthly (from 2027-02-01) | Only months where the channel has 10M qualified Shorts views over the past 90 days | — |
| Also required | AdSense, 2-step verification, no active strikes, passing a human channel review | not set up |

What doesn't count: Shorts watch time toward the hours route, and views on private, unlisted or deleted videos. Passing the thresholds doesn't guarantee approval, because a reviewer looks at the newest videos, the most-viewed videos and the metadata.

**Honest reading.** A Shorts-only path needs 10M views in 90 days, about 111,000 views a day. Most new channels never reach it. Plan for a long runway, and treat everything below as building toward it, not as expected income.

## The biggest risk: "inauthentic content"

YouTube's inauthentic-content policy (renamed 2025-07-15, clarified 2026-07-16) names templated or mass-produced AI content and "image slideshows… with little narrative or value". It's judged at **channel** level. Today's format (TTS narration over NASA stills with a Ken Burns zoom, written by Gemini, 3 a day) is close to that pattern. If the channel fails review, the 90 days of work spent qualifying are wasted.

### Originality plan, in order of impact

1. **Original scale graphics (implemented 2026-10-24).** The writer marks 1–2 scenes as a comparison ("Earth vs Sun", "ISS vs a bullet"). The pipeline then draws its own to-scale diagram, so the signature idea is shown visually, not just narrated over stock stills.
2. **Your own voice.** Record the narration (the script is in `post.txt`). This is the single strongest originality and trust signal, and it costs nothing. It would replace Edge TTS for the videos you voice.
3. **Moving footage.** A free Pixabay or Pexels key, plus Veo hook clips from your Gemini subscription (`python main.py veo-prompts`).
4. **Variation.** Three recurring series with different structures (`config.yaml → planning.series`). The originality screen rejects scripts that overlap more than 25% with earlier ones.
5. **Accuracy as the brand.** Sources and the fact-check log go in every description. A science channel's moat is being right.

**Cadence decision.** You asked for 3 a day, and the system is built for that. My recommendation is to *start* at 3 a day only while the human glance happens. If quarantines stay low and the weekly report shows retention isn't falling, keep going. If videos start getting fewer views per video (a sign of a channel-level quality signal), drop to 1–2 a day. Change one variable at a time (`docs/EXPERIMENTS.md`).

## Revenue streams, ranked for this niche

| # | Stream | When | Fit | Notes |
|---|---|---|---|---|
| 1 | Shorts ad revenue share (45% creator share) | After YPP | Medium | Indian Shorts RPM estimates conflict (₹5–₹30 vs about $0.008 per 1,000 views, third-party). Global English viewers are worth several times more, which is a reason to keep narration globally legible. |
| 2 | Long-form compilations (e.g. "10 scale facts", 8–10 minutes) | After a few hit Shorts | High | Long-form watch hours are the other YPP route, and education long-form RPM estimates are far higher than Shorts (third-party, ₹80–₹150). The pipeline can assemble these from existing scripts; not built yet. |
| 3 | Affiliate (telescopes, binoculars, star-map apps, astronomy books) | From about 1,000 subs | High | Only genuine recommendations, with disclosure. Amazon Associates India terms not verified yet. |
| 4 | Sponsorships (ed-tech, science apps, museums or planetaria) | From about 10,000 engaged subs | Medium | Needs real audience data first. |
| 5 | Digital products (printable scale posters, a classroom pack) | After a proven audience | Medium | Low cost; build only on demand signals from comments. |
| 6 | Fan funding (Super Thanks, memberships) | 500-sub tier | Low–medium | Small at first. |

Never: bought views or subscribers, engagement exchanges, or sub-for-sub. They violate policy and get channels terminated.

## Readiness checklist (tracked in `config.yaml → monetization.status` and the daily dashboard)

- [ ] Channel renamed and branded (Studio → Customization)
- [ ] 2-step verification on the Google account
- [ ] AdSense account (create when applying; it's free)
- [ ] YouTube Analytics connected (`python main.py auth-analytics`)
- [ ] 500 subs → fan-funding tier
- [ ] 1,000 subs plus 10M Shorts views in 90 days or 4,000 long-form hours → apply
- [ ] Applied → approved → first payment received (three separate events; only the last is income)
