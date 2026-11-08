# Analytics and growth system

## What's collected

| Data | Source | When | File |
|---|---|---|---|
| Subscribers, total views, video count | YouTube Data API (connected) | end of every `daily` run | `data/analytics/channel.csv` |
| Per video: views, likes, comments, privacy, role, slot | YouTube Data API (connected) | end of every `daily` run | `data/analytics/videos.csv` |
| Per video: watch minutes, avg view duration, avg % viewed, subs gained, shares | YouTube Analytics API (**not connected**) | `python main.py weekly` | `data/analytics/video_analytics.csv` |
| Impressions and CTR for Shorts | Not reliably in the API for the Shorts feed | YouTube Studio only | — |

**To connect retention data (5 minutes):**
1. In Google Cloud Console (same project), go to APIs & Services → Library and enable **YouTube Analytics API**.
2. Run `.venv/bin/python main.py auth-analytics` and sign in with the channel account.

This uses a separate read-only token, so it can't affect uploading.

## Reports

- **Daily** (`reports/daily-YYYY-MM-DD.md`):
  - the target of 3 a day against published, scheduled and ready, for yesterday through the buffer days, with any shortfall
  - jobs by status, and anything needing attention
  - the last run's usage
  - monetization readiness and the money summary
- **Weekly** (`reports/weekly-YYYY-MM-DD.md`): median and mean views by **role** and by **slot**. Groups with fewer than `analytics.min_sample` (5) videos are marked inconclusive.

## Rules for reading the numbers

1. Compare videos of the same age. A 2-day-old Short isn't comparable to a 9-day-old one; use the snapshot from the same age where possible.
2. Medians beat means. One viral Short distorts the mean.
3. Fewer than 5 videos per group means no conclusion.
4. Change one thing at a time and log it in `docs/EXPERIMENTS.md` with the hypothesis and the date. Judge it after at least 7 days and 9+ videos per arm.
5. Never delete a video just because it underperforms. Delete only for factual errors or policy problems. Shorts can pick up weeks later.

## Signals that change the strategy

| Signal (sustained over 2+ weeks) | Action |
|---|---|
| A slot's median views well above the others | Move the weakest slot closer to it (`schedule.slots_ist`) |
| One role or series clearly ahead | Raise its share; keep at least 1 discovery video a day |
| Avg % viewed under about 70% on Shorts (once connected) | Work on the first 2 seconds: test hook styles (experiment) |
| Many views, few subscribers | Make series more recognisable (consistent opening line, playlist) |
| Quarantine rate above 30% | Fix the cause (visuals or script) before making more |
