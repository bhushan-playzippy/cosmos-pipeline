# Channel and pipeline audit — 2026-10-24

## Starting point

- **Niche, kept:** space facts explained through everyday comparisons ("Cosmic Scale"), as 40–55 second vertical Shorts in English with Indian reference points.
- **Channel:** the channel ID ends in `…c2OQ`. It was a psychology channel, "MindRewired" (handle `@ig_psichochan`), with 0 subscribers and 1 old video.
  - The description and keywords were rebranded through the API on 2026-10-24.
  - The name, handle and profile picture can't be changed through the API, so they're still the old ones.
  - The original branding is backed up in `data/channel_backup_before_rebrand.json`.
- **Stack:** Python 3.12, Gemini (free tier), Microsoft Edge TTS, NASA Image Library, FFmpeg with libass, and the YouTube Data API v3. It runs on a Mac via launchd.

## Capability status (evidence in brackets)

| Capability | Status | Evidence |
|---|---|---|
| Topic → script → fact-check → package | **Working, verified** | 7 real videos made 2026-10-24; fact-check logs in each `post.txt` |
| Voice, visuals, captions, render (1080×1920, 30 fps, about −15 LUFS) | **Working, verified** | `qa.technical` passes on all 5 good videos made today |
| Deterministic quality gate | **Working, verified** | `tests/test_quality_gate.py` on real FFmpeg files with planted silence, black frames, wrong size, too short, corrupt file and near-copy |
| AI reviewer gate | **Working, but lenient** | It rejected 2 bad Chandrayaan renders (Mars images used for the Moon). It **passed** 2 videos with factual errors and 1 with a Jupiter image, all caught by a human check. |
| Upload, scheduling and publishing | **Working, verified** | Neutron star video uploaded private with publishAt 18:00 IST; `main.py sync` read it back as **public** at 19:21 IST |
| AI disclosure (`containsSyntheticMedia`) | **Was broken, now fixed** | `videos.insert` dropped the flag silently. It's now set through `videos.update` and read back after every upload (`test_upload_sets_ai_flag_after_insert_and_verifies`). |
| Duplicate-upload prevention | **Was missing, now implemented; lookup verified** | Title check against the latest 50 uploads ran against the real API for 4 uploads on 2026-10-25 (no false matches). The "match found → skip" branch is unit-tested only. |
| New publisher path (insert → AI flag → verify → playlist) | **Working, verified** | 4 real uploads on 2026-10-25 00:4x IST: all `verification.ok`, all in the playlist |
| Human approval gate | **Implemented, unit-tested** | `review.human_approval: true`; first real use at the 13:00 run on 2026-10-25 |
| Retries, quota and expired-login handling | **Implemented, unit-tested** | `tests/test_publishing.py`. Not yet exercised against real failures. |
| Persistent job states, recovery, kill switch, dry-run | **Implemented, unit-tested** | `pipeline/jobs.py`, `control.py`, `tests/` |
| 3-a-day slot scheduler and 30-day calendar | **Implemented, unit-tested** | `tests/test_scheduling.py`; `python main.py calendar` |
| Channel management (branding, playlists, comments, deletes) | **Working, verified** | Branding updated, playlist created, 3 flawed videos deleted on 2026-10-24 |
| Analytics: Data API snapshots | **Implemented, unverified** | runs at the end of `daily` |
| Analytics: retention and watch time | **Blocked on credentials** | needs `python main.py auth-analytics` plus enabling the YouTube Analytics API in Google Cloud |
| Finance ledger, profit vs debt-repayment cash | **Implemented, unit-tested** | `tests/test_pipeline_units.py`; ledger is empty (no revenue or expenses yet) |
| Instagram Reels | **Implemented, unverified, blocked** | needs `IG_USER_ID` and `IG_ACCESS_TOKEN` |
| Custom thumbnails | **Missing (by decision)** | Shorts thumbnails can't be reliably set through the API and don't show in the Shorts feed (see research notes) |
| Background music | **Missing** | `assets/music/` is empty, which is fine for revenue: licensed music shrinks the Shorts revenue pool |
| Stock video (Pexels/Pixabay) | **Blocked** | no key. Visuals are NASA stills plus generated starfields, which is the main **inauthentic-content risk** (see `RISK_REGISTER.md`). |

## Defects found and fixed today

| Priority | Defect | Fix |
|---|---|---|
| P0 | The Gemini client had no timeout, so one stalled request hung the whole run | 180-second timeout; models with exhausted or overloaded quota are skipped (`llm.py`) |
| P0 | Retired fallback models (`gemini-2.5-flash`, `2.0-flash`) caused 404s | Model list updated, with a check at runtime |
| P1 | The AI disclosure flag silently didn't apply | Set after upload and verified |
| P1 | Factual errors (radius vs diameter, wrong timing, theory stated as fact) passed the AI gates | Stricter fact-check and reviewer prompts; the 3 affected videos were deleted and rebuilt; rule: a human glance before videos go public |
| P1 | No duplicate-upload guard, so a crash after upload meant a re-upload on the next run | Title guard plus a job-state machine |
| P1 | A crash in any stage killed the entire daily run | Each step isolated; failed jobs recorded with stage and traceback |
| P1 | Reviewer crashed when Gemini returned a JSON list | Unwrapped (`test_reviewer_tolerates_list_wrapped_json`) |
| P1 | `post.txt` crashed on scenes without a rights record (found by the new tests) | Fixed |
| P2 | Every video got identical starfield backgrounds (seeded by scene number) | Seeded per video and scene |
| P2 | NASA labelled diagrams, infographics, people and copyrighted third-party items used as B-roll | Metadata filter, Gemini vision check (cached per image), copyright filter |
| P2 | No limits on uploads or Gemini calls per run | `control.max_uploads_per_run`, `max_gemini_calls_per_run` |
| P2 | One video a day at a fixed time | Slot scheduler: 3 a day, minimum gap, buffer days, role per slot |
| P3 | `logs/` and the analytics token weren't git-ignored | Added |

## Known gaps that remain

1. **The AI reviewer isn't a reliable fact gate.** On days when Flash quota runs out, both writing and reviewing fall to Flash-Lite. Mitigation for now: the daily dashboard lists every scheduled video, and a human checks `post.txt` before its slot.
2. **Sources are cited by the model, not fetched and checked.** A retrieval step (fetch the cited NASA/ESA page and quote it) is the next research upgrade.
3. **Visual originality.** Narrated stills are the pattern YouTube's inauthentic-content policy names. See `MONETIZATION_STRATEGY.md` → "originality plan".
4. **Single machine.** If the Mac is off for a day, no production happens. The 2-day buffer covers short outages, and GitHub Actions is the fallback.
