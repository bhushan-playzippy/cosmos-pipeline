# Pipeline architecture

A single Python process, run once a day by macOS launchd (`autopilot/`) or GitHub Actions (`.github/workflows/`).
There's no server, no database and no queue service. Durable state is a few JSON/CSV files in `data/`, which is
enough for 3 videos a day and easy to inspect and fix by hand.

## Agents

Each agent is one module with one responsibility. The orchestrator is the only module that calls the others.

| Agent | Module | Input → output | Fails when |
|---|---|---|---|
| Orchestrator | `pipeline/orchestrator.py` | config + job store → jobs moved through their lifecycle | never raises from `daily`; each step is isolated |
| Topic intelligence | `pipeline/ideas.py` | history → scored, role-tagged backlog (`data/topics.json`) | Gemini unavailable |
| Editorial planner | `pipeline/calendar_plan.py` | backlog + slots → 30-day plan (`reports/[<id>/]CONTENT_CALENDAR.md`, `calendar.json`) | — |
| Script writer + fact-checker | `pipeline/scriptgen.py` | topic → scenes, sources, fact-check log (2 Gemini passes) | script too short after 3 tries |
| Independent verifier | `pipeline/verify.py`, `facts.py`, `evidence.py` | blind claim list from a model of a different family than the writer/checker/repairer → Python statuses (VERIFIED/APPROXIMATE/CONFLICTING_SOURCES/UNSUPPORTED/REJECTED/UNRESOLVED/RHETORICAL); non-numeric claims need a sentence on a fetched page (key terms or exact quote); number coverage incl. graphics; title/headline/description/comment may not exceed the narration → 1 repair (`claims`, `claims_for` v2 over all visible text, `independence`) | still failing → failed at `verify`; no independent family → deferred (draft kept) |
| Packaging (YouTuber) | `pipeline/youtuber.py` | script → title, description, tags, hashtags, comment | keeps the writer's metadata if Gemini fails |
| Voice | `pipeline/voice.py` | scenes → voice.wav + word timings (Edge TTS) | 3 failed tries or no word timings |
| Visuals | `pipeline/visuals.py` | scenes → one image/clip per scene + rights record | never: a generated starfield is the last resort |
| Captions + render | `captions.py`, `render.py` | timeline → 1080×1920 MP4 (FFmpeg) | FFmpeg error |
| Quality gate | `pipeline/qa.py` | MP4 + script → pass/fail per check (deterministic) | — |
| AI reviewer + visual grounding | `pipeline/review.py`, `grounding.py` | frames + per-scene line, intended picture and asset metadata → scores + a MATCH/CONTEXTUAL/MISMATCH call per scene, combined with deterministic metadata rules (`visual_grounding` in script.json) | MISMATCH/UNVERIFIED or too few MATCH → quarantined after remake |
| Publisher | `publish_youtube.py`, `channel.py` | ready job → scheduled YouTube video, verified | quota, auth, 4xx |
| Analytics | `pipeline/analytics.py` | YouTube APIs → CSV snapshots, daily/weekly reports | analytics not authorized (reported) |
| Finance | `pipeline/finance.py` | ledger → operating profit, cash available for debt | — |
| Run control | `pipeline/control.py` | kill switch, dry-run, per-run limits, usage log | — |

## The 19 stages and where they live

| # | Stage | Where | State |
|---|---|---|---|
| 1–2 | Topic discovery, scoring | `ideas.refill_backlog` (Gemini, model-estimated scores) | implemented |
| 3 | Research + source validation | `scriptgen.fact_check` (2nd Gemini pass, arithmetic shown) | implemented; sources are model-cited, not fetched |
| 4–5 | Script + quality/originality | `scriptgen.write_script`, `qa.editorial`, `qa.originality` | implemented |
| 6–7 | Visual plan + assets | per-scene `visual`/`nasa` queries; `visuals.gather` | implemented |
| 8–10 | Voice, render, captions | `voice`, `render`, `captions` | implemented |
| 11 | Thumbnail + metadata | `youtuber.package` | metadata implemented; **custom Shorts thumbnails not used** (see risks) |
| 12–13 | Automated QA, policy/rights | `qa.gate` + `review.review_video` + rights record per scene | implemented |
| 14–15 | Upload + verification | `orchestrator.publish` → `publish_youtube.upload` → `set_disclosure_and_verify` | implemented |
| 16–17 | Analytics + analysis | `analytics.snapshot`, `write_daily_report`, `write_weekly_report` | Data API implemented; Analytics API needs `auth-analytics` |
| 18 | Experiments | `docs/EXPERIMENTS.md` (manual, one change at a time) | process only |
| 19 | Revenue/cost reconciliation | `finance.py` + `data/usage.csv` | implemented; ledger entries are manual |

## Job lifecycle (`data/history.json`)

```
              ┌─ failed ───────────── (crash; error.txt + stage recorded; topic can be retried)
make_video ───┼─ quarantined ──────── (QA or reviewer failed twice; kept for a human)
              ├─ dry_run ──────────── (made with --dry-run; never uploaded)
              └─ ready ── publish ──► scheduled ── sync ──► published
                   (holds a planned slot)  (verified publishAt + AI flag)    (YouTube says public)
                                         └──────► deleted (reason recorded)
```

Only **published** counts toward the 3-a-day target. A job's planned `slot` is separate from its confirmed `publish_at`.

## Daily run (`python main.py daily`)

1. **sync:** reads every live video back from YouTube. Moves scheduled → published, flags videos still private after their publish time, and marks videos missing from YouTube as deleted.
2. **catch-up uploads:** ready jobs, oldest first, until `max_uploads_per_run` or the quota stops it.
3. **production:** fills free slots over the next `buffer_days` days, up to `max_videos_per_run`. Each slot's position sets its role (discovery / authority / retention). A topic that fails its own checks does not count: the next topic gets the slot, up to `max_attempts_per_run` topics. Production stops early on low Gemini budget, an infrastructure failure at the script stage, or two script-stage failures in a row.
4. **uploads** of what was just made.
5. **comments** on videos that have gone public.
6. **analytics snapshot + dashboard** (`reports/daily-YYYY-MM-DD.md`), then a usage row in `data/usage.csv`.

## Safety properties (each covered by tests in `tests/`)

Phase 6 (2 Nov, `tests/test_phase6.py`): no MISMATCH/UNVERIFIED scene passes; an image's recorded `asset_id` is the
image shown; non-numeric claims are cited from fetched documents or explicitly UNRESOLVED; every visible text is in
the verify fingerprint; the verifier is never the writer's, checker's or repairer's model family; growth effects
change the live topic pick; quota limits and deferrals never burn a written script.


- **Idempotent uploads.** A job uploads only from `ready`, and before inserting, the publisher looks for an identical title among the channel's latest 50 uploads.
- **Crash-safe production.** A crash at any stage leaves a `failed` record with the stage and traceback. Partial work is deleted and nothing half-made can be uploaded.
- **Bounded spend.** There are per-run caps on uploads and Gemini calls, models that hit their daily quota are skipped for the rest of the run, and NASA image checks are cached so each image is checked once.
- **Kill switch.** A `STOP` file or `control.kill_switch` halts all new work and publishing.
- **Dry run.** `--dry-run` runs everything except uploads, deletes and comments, and its videos can never be uploaded.
- **Clear auth failures.** An expired or revoked login stops the run with the exact command to fix it.

## Files

| Path | What |
|---|---|
| `data/history.json` | job store (one record per video) |
| `data/topics.json` | scored topic backlog |
| `data/calendar.json` | 30-day plan |
| `data/usage.csv` | per-run usage (the basis for cost per video) |
| `data/vision_cache.json` | NASA image → has text/people (Gemini vision result) |
| `data/analytics/*.csv` | daily channel and video snapshots |
| `data/finance/ledger.csv` | money in and out (manual entries) |
| `output/<job>/` | `video.mp4`, `script.json`, `post.txt` (human-readable record), `error.txt` if failed |
| `reports/` | daily and weekly dashboards |
| `.env`, `client_secret.json`, `youtube_token.json`, `youtube_analytics_token.json` | secrets; all git-ignored |
