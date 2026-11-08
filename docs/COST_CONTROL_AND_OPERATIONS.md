# Cost control and operations runbook

## Spending policy

- **Cash spend today: ₹0.** Everything runs on free tiers (Gemini free, Edge TTS, NASA, YouTube API, your Mac).
- **Nothing gets bought or upgraded without your explicit approval.** That includes the Gemini paid tier, stock footage, TTS and hosting.
- **Hard per-run limits** (`config.yaml → control`):
  - `max_uploads_per_run: 5`
  - `max_gemini_calls_per_run: 120`
  - `gemini_calls_per_video: 25` (a new video won't start without this much budget left)
- **Waste controls:**
  - Models that hit their daily quota are skipped for the rest of the run.
  - The NASA vision check is cached per image.
  - A failed gate gets one remake, then quarantine. Nothing loops forever.
  - Production stops if the script stage hits an infrastructure error (all models failed) or fails twice in a row. A topic that fails its own checks (verify, too-short script) hands its slot to the next topic, up to `schedule.max_attempts_per_run` topics per run (default 4).
- **Stop-loss rule:** if more than half of a week's videos are quarantined, or the weekly report shows median views falling for 2 weeks in a row, cut `videos_per_day` before spending more effort. Bad videos hurt the channel more than missing ones.
- **Usage log:** every run appends to `data/usage.csv` (Gemini calls, vision calls, TTS calls, renders, uploads, videos made or quarantined).

## Everyday commands

| Task | Command |
|---|---|
| See the queue, free slots and alerts | `.venv/bin/python main.py status` |
| Today's dashboard | `.venv/bin/python main.py report` (writes `reports/daily-*.md`) |
| Weekly review | `.venv/bin/python main.py weekly` |
| Rehearse without touching YouTube | `.venv/bin/python main.py daily --dry-run` |
| Check what YouTube actually did | `.venv/bin/python main.py sync` |
| Record money | `.venv/bin/python main.py finance --kind revenue --category adsense --amount 0 --status received` |

## Stop, resume, recover

| Situation | Do this |
|---|---|
| **Stop everything now** | `touch ~/Downloads/cosmos-pipeline/STOP`. No new videos, uploads or comments until the file is removed. Videos already scheduled on YouTube still go public; to stop those too, set them to private in Studio. |
| Resume | `rm ~/Downloads/cosmos-pipeline/STOP` |
| Turn off the daily schedule | `launchctl bootout gui/$(id -u)/com.cosmicscale.daily` |
| A video is wrong after upload | Delete or privatise it in Studio, then `python main.py sync` (it gets marked deleted and its slot frees up) |
| Fix a script and rebuild | Edit `output/<job>/script.json`, then `python main.py rerender output/<job>` |
| A job is `failed` | Read `output/<job>/error.txt`. The stage is in `data/history.json`. Usually transient: the next daily run makes a replacement for the slot. |
| A job is `quarantined` | Read the QUALITY GATE and AI REVIEWER sections in `output/<job>/post.txt`. If it's actually fine: `python main.py publish output/<job> --force` |
| "YouTube login expired" | `python main.py auth-youtube` (browser sign-in) |
| "YouTube quota used up" | Nothing to do: ready videos upload on the next run |
| Gemini errors all day | Free-tier caps; production resumes the next day. The 2-day buffer covers it. |
| The run crashed halfway | Just run `python main.py daily` again: it's idempotent (uploads only from `ready`, duplicate-title guard, slots recomputed) |

## Backup runner: GitHub Actions (optional)

`.github/workflows/` runs `python main.py daily` at 12:00 IST on GitHub's machines, for when the Mac is off. It only runs when the repo variable `SCHEDULER` is set to `github` (off by default since 1 Nov). Setup is in README step 7. The YouTube token secret must be the **new** `youtube_token.json`, which has the management scopes. Don't run both the Mac and GitHub on the same day: the duplicate guard prevents double uploads, but both would spend Gemini quota.

## Logs

- launchd run: `logs/daily.log`
- per job: `output/<job>/post.txt` (and `error.txt` on failure)
- per run: `data/usage.csv`

## Run continuity (added 2026-11-01)

- `data/[channels/<id>/]runs.json` records each `daily` run (start, finish, outcome). Outcomes: `ok`,
  `skipped_recent`, `offline`. A run that never finished is logged as `interrupted_run` at the next start, and
  the placeholder jobs it left get the reason "interrupted…".
- Missed scheduled runs (`control.run_times_ist`) appear as `missed_runs` events. launchd runs a calendar job
  missed during sleep once on wake. With `RunAtLoad` in the plists, it also runs one after a restart or login.
  A run missed while the Mac is off and not logged in is only logged, never replayed.
- Offline at start: `control.network_checks` × `network_check_seconds` (default 4 × 60 s), then the run is skipped.
  There is no retry loop: the next scheduled run is the retry.
- Reinstalling a plist with `RunAtLoad` starts a real run immediately (uploads included). Reinstall when a run is
  wanted, or `touch STOP.<id>` first.


## Safeguards added by the 1 Nov system audit

Details and evidence are in `reports/critical-issues-and-fixes-2026-11-01.md`.

- **Publishing only verified text.** `publish` quarantines a generated video whose narration or title differ from the
  last claim check (`claims_for`). To recover one, fix `script.json` if needed, then `rerender` it, which re-verifies.
  `publish --force` overrides the check. Use it only after checking the claims yourself.
- **Channel identity on every YouTube call.** Sync, comments, withdraw, reschedule, delete and analytics snapshots
  now refuse a token for another channel (`ChannelMismatch`), as uploads already did. Sync also refuses to mark
  videos deleted when YouTube returns none of them (`sync_suspicious` event): check the token with
  `main.py [--channel <id>] auth-youtube`.
- **Dry runs are rehearsals only.** `daily --dry-run` returns the backlog topics it used, releases the images it
  picked, and plans its videos into different slots. It still writes `dry_run` job records and today's
  `playbook.json`, and it uses real free model quota.
- **GitHub Actions scheduler is off by default.** It only runs when the repo variable `SCHEDULER` is `github`. Turn
  launchd off for that channel first.
- **Linux VM.** `deploy/linux/` has systemd templates and `requirements.lock.txt` pins the tested package versions.
  Nothing is installed. See `reports/deployment-readiness-2026-11-01.md`.

## Phase 6 safeguards (2 Nov 2026): visuals, evidence, visible text, growth, capacity

- **Inspect a video before its slot:** `python main.py [--channel clips] inspect output/<folder>` prints the claim
  ledger (status, category, citation or "model only"), the per-scene visual grounding (MATCH / CONTEXTUAL /
  MISMATCH / UNVERIFIED with the asset's title, date and reasons) and whether the text is still verified.
- **Quality report:** `python main.py quality [--count DAYS]` and the "Content quality and operations" section of
  each daily report: topics attempted/rejected, scripts verified, claim statuses, visual rates, gate failures, and
  what wasn't made split into content / infrastructure / quota / human review. A dry run exiting 0 means the run is
  safe, not that videos can be made: read the ready/scheduled counts.
- **Visual grounding:** a MISMATCH or UNVERIFIED scene never passes. Clips needs half its scenes to show the
  subject itself (`visual.min_match_share: 0.5`) and uses one image on at most 2 scenes. Expect more clips topics
  to fail at `visuals` (before render and review, so no review quota is spent): that is the gate doing the job the
  owner did by hand in 13 withdrawals.
- **Evidence:** non-numeric claims need a sentence on a fetched page; otherwise they are UNRESOLVED (quotes,
  attributions, superlatives and motives fail; at most 2 others per video). Only https pages on the
  `evidence.QUALITY` allowlist are fetched.
- **Everything visible is verified:** editing the headline, description, pinned comment, caption or a graphic in
  `script.json` makes the job unverified; `rerender` re-checks it and `publish` refuses it until then.
- **Model capacity:** `data/llm_usage.json` has requests/failures/tokens per model per Pacific day. To stop using a
  model at a known limit, set `gemini.daily_limits: {"gemini-flash-latest": 20}`. A second free AI Studio key for
  clips (`gemini.api_key_env: GEMINI_API_KEY_CLIPS` in `channels/clips.yaml`, value only in `.env`) doubles Gemini
  quota but adds no independence: verify must still be a different model family (Groq gpt-oss / qwen).
- **Deferred drafts:** a script deferred at `verify` stays in the backlog topic as `draft` for 3 days and resumes
  at the claim check; drafts are never saved by a dry run.

## Autonomous operation (2 Nov 2026)

The owner authorised fully autonomous production and publishing: no routine human approval. Every video still has
to pass every gate; a failed or missing gate quarantines it, and the next topic takes the slot.

**The daily run, per channel:** sync → performance → release decisions (anything waiting goes through the release
gates) → catch-up uploads → production (topic → write → fact-check → verify (+ 1 repair + re-check) → voice →
visuals + grounding → render → QA → review → release decision) → uploads (release gates re-checked, channel identity,
duplicate guard, reconcile) → comments → insights → instagram → notify → health alerts → retention → analytics
snapshot + YouTube Analytics (once a day) → growth → gaps → daily report.

**Commands to enable it** (from the project folder):

```bash
.venv/bin/pytest                                   # must be green
grep -n human_approval config.yaml channels/clips.yaml   # both must say false (they do since 25 Oct)
.venv/bin/python main.py daily --dry-run --force   # rehearsal: renders, QA, review, "release decision", no upload
.venv/bin/python main.py --channel clips daily --dry-run --force
# install the repo schedulers (RunAtLoad + caffeinate); this starts one real run per channel right away
for f in daily clips; do cp autopilot/com.cosmicscale.$f.plist ~/Library/LaunchAgents/; \
  launchctl bootout gui/$(id -u)/com.cosmicscale.$f 2>/dev/null; \
  launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cosmicscale.$f.plist; done
launchctl list | grep cosmicscale                  # both listed
# phone alerts: put NTFY_TOPIC=<a long random name> in .env and subscribe to it in the ntfy app
```

**Stop it:** `touch STOP` (everything) or `touch STOP.cosmic` / `touch STOP.clips`; delete the file to resume.
Never set the GitHub variable `SCHEDULER=github` while launchd runs a channel (two schedulers = double uploads).

**What it does by itself:**
- **Recovery:** it resumes saved drafts, remakes a gate failure once with the same verified script, defers on
  quota or overload (exact cause recorded), skips a failing provider for the rest of the run, reconciles an
  uncertain upload before retrying, and gives a failed topic's slot to the next topic (`max_attempts_per_run`).
- **Alerts:** at most once a day each, for:
  - no publication for 3 days,
  - 3 content failures in a row,
  - provider trips or outages,
  - upload failures,
  - missed or interrupted runs,
  - the other channel's runs going stale (26 h),
  - less than 10 GB free on disk.

  Each alert goes out as a macOS banner, and to your phone too when `NTFY_TOPIC` is set.
- **Disk:** after 14 days it deletes the rendered files of videos that will never upload. The audit records are
  kept.
