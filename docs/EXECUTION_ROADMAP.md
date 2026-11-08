# Execution roadmap

## Done (2026-10-24, verified unless marked)

- [x] Phase 1 Audit: `AUTONOMOUS_CHANNEL_AUDIT.md`, `PIPELINE_ARCHITECTURE.md`
- [x] Phase 2 Stabilise: timeouts, model fallback, quota-aware skipping, crash isolation, job states, duplicate-upload guard *(unit-tested)*, retries *(unit-tested)*, auth errors, kill switch, dry-run, secrets git-ignored
- [x] Phase 3 Daily engine: 3 slots a day with roles, buffer days, catch-up uploads, publication verification, status sync *(scheduled → public verified on a real video)*, 30-day calendar
- [x] Phase 4 Quality and compliance: deterministic gate, AI reviewer, originality screen, rights records, copyright filter, AI disclosure fixed, original to-scale graphics
- [x] Phase 5 Analytics: Data API snapshots and daily/weekly reports *(Analytics API blocked on auth)*
- [x] Phase 6 Money: ledger, profit vs debt-repayment cash, usage log, budget caps
- [x] Phase 7 Validation: 54 automated tests; real end-to-end production run *(see final report)*

## Your actions (blocking or high value), in order

1. **Publish the Google Cloud app to production** (Google Auth Platform → Audience → Publish app), then `python main.py auth-youtube`. Without this the YouTube login dies in about 7 days.
2. **Rotate the Gemini API key** (it was pasted in chat), and put the new key in `.env`.
3. **Rename the channel** to a distinctive name ("Cosmic Scale" is taken) and replace the psychology profile picture and banner.
4. **Turn on 2-step verification** on the Google account.
5. **Connect retention analytics:** enable the YouTube Analytics API, then `python main.py auth-analytics`.
6. Get a free **Pixabay** key for moving footage. I'll add the integration once you have it.
7. Optional: record the narration yourself for some videos. It's the strongest originality signal.

## Next 30 days

| Week | Focus | Exit criteria |
|---|---|---|
| 1 (Oct 25–31) | Run 3 a day with a human glance before each slot; fix every recurring quarantine cause | ≥ 15 published; zero factual errors public; quarantine rate measured |
| 2 (Nov 1–7) | Connect analytics; first weekly report; start experiment E1 (hook style) | Retention data flowing; E1 logged |
| 3 (Nov 8–14) | Act on slot and role data only where n ≥ 5 per group; add Pixabay or Veo hooks | One evidence-based change made |
| 4 (Nov 15–21) | Decide on cadence (keep 3 a day or drop to 1–2) using views per video, retention and quarantine rate; first long-form compilation if any Short broke 10k views | Written decision in `EXPERIMENTS.md` |
| Day 30 review | Unit economics from `data/usage.csv`; decide whether paid Gemini is worth it (needs approval) | Go / no-go on any spending |

## Later (not started)

- Long-form compilation assembler (the second YPP route; higher RPM)
- Retrieval step: fetch the cited NASA/ESA page and quote it in the fact-check
- Instagram Reels (code exists; needs tokens)
- Thumbnails for long-form videos (Shorts thumbnails aren't reliably settable)
