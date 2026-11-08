# CLAUDE.md

Automated faceless YouTube Shorts pipeline running two independent channels from one codebase. It is a single
Python process with no server and no database. Durable state is JSON/CSV in `data/`. macOS launchd runs it daily.

| id | Profile | Channel | Content | Target |
|---|---|---|---|---|
| `cosmic` (default) | `config.yaml` | "Universe in a Teaspoon" (`UCS3WDSezET11wMQjfWuc2OQ`) | space facts via everyday scale comparisons | 2/day, 20:00 / 23:30 IST |
| `clips` | `channels/clips.yaml` | "Unbelievably True" (`UCPzixXDunk2uJUqLp-ndLVA`, @peakmomentsworld) | real, fact-checked history stories (`writer.template: story`) | 2/day, 20:00 / 23:30 IST |

Cadence is 2/day per channel by owner decision (2026-10-29; was 3 and 5). Slots (owner 8 Nov) are 20:00 IST
(India evening, UK afternoon, US morning) and 23:30 IST (European evening + US lunch); 05:30 was the weakest slot. Roles rotate across days (`jobs.role_for`), so all series still run.
`main.py reschedule --over-cap` moves scheduled private uploads beyond the daily target to free slots.

Despite its id, `clips` is now `type: generated` and runs the same pipeline as `cosmic`. The creator-clip code in
`pipeline/clips/` (rights gate, ingest, segments, compliance) is tested but **unused**. Its old profile is in `archive/`.

## Commands

Always use the venv: `.venv/bin/python`, `.venv/bin/pytest`. FFmpeg comes from Homebrew.

```bash
.venv/bin/pytest                                   # full suite (~30 s); must stay green
.venv/bin/pytest tests/test_quality_gate.py -k name  # single test
.venv/bin/python main.py status                    # queue, free slots, alerts
.venv/bin/python main.py daily --dry-run           # full run without uploads/deletes/comments
.venv/bin/python main.py --channel clips status    # any command on channel 2
.venv/bin/python main.py channels                  # list profiles + isolation check
.venv/bin/python main.py quality                   # gate outcomes by class, claim/visual rates, model usage
.venv/bin/python main.py inspect output/<folder>   # one video's ledger, citations, grounding, verification state
```

`main.py`'s module docstring is the authoritative command reference. `touch STOP` halts everything, and
`touch STOP.<id>` halts one channel.

Schedulers: `autopilot/com.cosmicscale.daily.plist` and `autopilot/com.cosmicscale.clips.plist` are both installed
in launchd. They run `daily` at 14:20 and 21:00 (cosmic) and at 15:05 and 21:40 (clips); the second run is a catch-up
that fills gaps and retries uploads. The afternoon runs sit about 50 minutes after Gemini's daily reset (moved 2 Nov: right at the reset every strong call returned 503), which is midnight
Pacific: 13:30 IST in US winter time, 12:30 IST in summer. A run before the reset still sees yesterday's
exhausted models and makes nothing. Logs go to `logs/daily.log` and `logs/clips.log`. The plists in `autopilot/`
wrap the run in `caffeinate -i -s` (the Mac is a laptop that idle-sleeps; a run once stalled 13 h across sleep in
Edge TTS), and `daily` has a watchdog (`control.max_run_minutes`, default 180) that dumps stacks and exits.
`runs.py` (run continuity, `runs.json` next to `history.json`) handles three cases:
- A previous run that never finished is logged as interrupted, and its placeholder jobs get a reason.
- Scheduled runs that never started (`control.run_times_ist`) are logged as missed.
- With no network, the run checks a few times (`network_checks`) and then skips instead of failing step by step.

The repo plists have `RunAtLoad` (a run after a restart or login), and launchd itself runs a calendar job missed
during sleep once on wake. A catch-up within `min_minutes_between_runs` of a completed run is skipped
(`daily --force` overrides).
After editing a plist, reinstall it (`cp` to `~/Library/LaunchAgents/`, then `launchctl bootout`/`bootstrap`). `autopilot/README.txt` is
outdated (it still describes 1 video/day). `.github/workflows/daily.yml` is an alternative scheduler for channel 1. It is
off unless the repo variable `SCHEDULER` is `github`. **Never run both** for the same channel, because they keep separate
queues and would double-upload. `deploy/linux/` holds systemd templates for a future Linux VM. They are not installed
anywhere; see `reports/deployment-readiness-2026-11-01.md`.

## Architecture

`main.py` parses the args, then `channels.load()` → `channels.activate(cfg)`. It takes a per-channel `flock` for
mutating commands and dispatches to `pipeline/orchestrator.py`.

**`orchestrator.daily`** runs these steps in order, each isolated in try/except (one failure must not block the
rest, and `daily` never raises): sync → performance review → catch-up uploads → production → uploads → comments
(posts our pinned-comment text) → comment insights → instagram → notify, then analytics snapshot → growth → gaps →
daily report (`reports/[<id>/]daily-YYYY-MM-DD.md`).

The production chain for one video lives in `orchestrator.make_video`:
`ideas` (scored topic backlog) → `scriptgen` or `storygen` (script and a 2nd-pass fact-check by Gemini) →
`youtuber` (title/description/tags) → `verify` (independent claim check, below) → `virality` (hook/title/length/hashtag checks) → `voice` (Edge TTS + word
timings) → `visuals` (tried in the profile's `video.sources` order: Veo/Pexels/NASA/Pixabay for cosmic,
Wikimedia Commons public-domain images first for stories, and a generated starfield (`stars`) as the last resort.
For stories, `make_video` skips topics that have no Commons images before it spends any model calls, and every
Commons file must mention the writer's `subject_query`) → `captions` + `render` (FFmpeg, 1080×1920, background
music from `video.music_dir` ducked under the voice) → `qa.gate` (deterministic checks, including "background
music audible") → `review` (Gemini reviewer: frames plus the final audio mix) → `publish_youtube`. `voice` retries a TTS stream
that stopped before the scene's last words (`voice.complete`), and QA fails a video whose audio ends before the
picture ("audio covers video").

The writers ask for an ending that echoes the hook. `storygen.polish` (shared by both writers) repairs long,
date-first or verbatim-echo hooks and endings, and `virality.drop_repeats` is the last safety net. A verbatim echo
fails QA's "no repeated lines" check, and before this fix that caused about half of all quarantines.

Music: `tools/make_music.py` synthesises the original tracks in `assets/music/space/` and `assets/music/stories/`.
They are royalty-free and can't be claimed by Content ID. `main.py add-music` mixes music under videos made
without it, and `--replace` swaps scheduled private uploads.

Feedback agents write per-channel JSON that the prompts read back. `performance.py` writes `playbook.json` (it
ranks public Shorts by views/hour and drives `ideas`, the writers and `youtuber`). `comments.py` reads viewer
comments and writes `comments.json`; its insights reach the same prompts through `performance.guidance()`. It is
read-only, makes one Gemini call only when new comments arrive, and treats comment text as untrusted data.
`gaps.py` writes `gaps.json` and `growth.py` writes `growth.json`. The weekly report and the growth agent count only
videos that are live now (`analytics.latest_rows`), so a withdrawn video's old "public" row no longer counts. Group
effects are shrunk toward 1.0 by sample size (`growth.shrink`). Live topic selection (`ideas.next_topic`) ranks the
backlog by score × `growth.topic_multiplier`: the playbook's pillar/role weight × the growth agent's conclusive
hook-feature effects (`feature_boost`: question / number / big-number / long hook, each side needing
`analytics.min_sample` videos, product bounded 0.7–1.4) × a repeat-pillar penalty. The slot's role still picks the
pool (so authority content isn't dropped on a small sample). Each pick records `selection` (parts, final score,
runners-up) on the job and a `topic_selected` event. `type_boost` is still only read by the unused clip picker.

Instagram (`publish_instagram.py`) is a separate daily step, `orchestrator.crosspost`. It posts a Reel only after
YouTube has made the Short public, never inside the YouTube upload. Each channel has its own credentials:
`IG_USER_ID`/`IG_ACCESS_TOKEN` for cosmic, `IG_USER_ID_<ID>`/`IG_ACCESS_TOKEN_<ID>` for the others. It is off
until `instagram.enabled: true`.

`verify.py` is a blind second opinion: a strong model that never sees the writer's fact-check lists every material
claim (one entry per number) with the narrated value and its own value + source, and Python decides each claim's
status (VERIFIED / APPROXIMATE / CONFLICTING_SOURCES / UNSUPPORTED / REJECTED / UNRESOLVED / RHETORICAL). It
also requires these:
- **Coverage.** Every number and superlative in the narration, title and on-screen headline has a ledger entry, and
  so does every number a to-scale graphic prints.
- **Independence.** The checker can't be a model *family* that wrote, fact-checked or repaired the script
  (`provenance` in script.json, `llm.ask_json(avoid=)`, `llm.family`): `gemini-flash-latest` is an alias whose
  target the API doesn't reveal, so all Gemini ids count as one family. With no other family available it defers.
  `script.json` → `independence` records who verified and which families were excluded.
- **Documents.** `evidence.py` checks each narrated quantity against the subject's Wikipedia article. A
  contradicting same-unit value fails the claim, and a matching sentence is cited. Non-numeric claims (person,
  event, cause, motive, quote, superlative…) carry `key_terms` (which must appear in the line) or the exact quote,
  and are VERIFIED only by a sentence on a fetched page (Wikipedia, or a model-proposed https URL on the
  `evidence.QUALITY` allowlist). Without one they are UNRESOLVED: a fail for quote/attribution/superlative/motive,
  and at most `verify.max_unresolved` (2) per video otherwise. A model-named source is `proposed_source`, never
  `citation`; `citation` holds title, publisher, URL, passage, quality (primary/scholarly/secondary/tertiary) and
  direct/indirect.
- **Honest visible text.** Title, headline, description body, pinned comment and Instagram caption can't state a
  number, superlative or magnitude the narration doesn't (`verify.visible_issues`).

A failing claim gets one targeted repair and a full re-check, else the video fails at stage `verify`. The ledger is
saved as `script.json` → `claims` and `claims_for`. The fingerprint (v2, `"v2:…"`) covers the narration and every
visible text (`facts.visible`: title, headline, description body, comment, caption, graphic labels/values; the
hashtags and AI label added after the check are excluded), so `rerender` of any hand edit verifies again.
`verify.verified(script)` is the one test both `publish` and `rerender` use; a v1 ledger (before 2 Nov, narration +
title) stays valid only while its other visible text passes the deterministic checks (`inspect` shows which). `facts.py` holds the deterministic number/unit/superlative parsing. The hook
polish may not add a number, intensifier or name (`facts.new_names`), and `fact_trail` records every number a stage added or removed. All of
this exists because the Hunley Short (1 Nov) passed with a "five-foot spar" and "80 pounds" (see
`reports/factual-verification-hardening-2026-11-01.md`).

`llm.py` is the only Gemini entry point (`ask_json`). It tries `gemini.models` in order. Two tiers: calls with
`strong=True` (write, fact_check, verify, review) never use `gemini.weak_models` (Flash-Lite); with no strong quota
they raise `llm.Deferred`, which stops production for the run, deletes the half-made job and returns its topic to the
backlog. Image yes/no checks use only weak models. Exhausted models are remembered for the Pacific day in
`data/gemini_quota.json`, shared by both channels on purpose (the quota belongs to the key; keyed by a fingerprint,
never the key). Each call logs an `llm_call` event (purpose, model, seconds, tokens) and is tallied per model and
Pacific day in `data/llm_usage.json` (requests, failures, tokens; shared like the quota file). Optional
`gemini.daily_limits {model: n}` stops using a model at n requests instead of probing it. A second API key adds
quota, never independence (same family). A script deferred at `verify` is saved in the backlog topic (`draft`,
3 days; never in a dry run) and the next run resumes at the claim check; a remake after a visual/technical gate
failure reuses the verified script. **Stage recovery** (3 Nov) covers four cases:
- **Verified record.** A script the claim check passed is written once to `output/<job>/verified.json`, read-only
  and keyed by fingerprint. A changed text adds a new version, `verified-<fp>.json`. Retention never prunes it.
- **Scene replacement.** A pre-render grounding MISMATCH re-gathers only that scene (`visuals.gather(only=)`,
  `visual.scene_retries` 2) and re-judges all scenes. NASA candidates are judged on their metadata before
  download.
- **Resume next run.** A failure at voice/visuals/render saves `script.json` with `pipeline_state` and puts the
  topic back with the verified draft (`draft_stage`, at most `review.stage_resumes` 2). That job is marked
  `resumable`, so `done_topics` doesn't count it, and the next run starts at voice. A changed fingerprint is
  verified again.
- **Deferred draft.** A resumed draft that defers again (not for quota) is kept, and a fresh topic gets the slot.
  Before this, both 3 Nov drafts needed the tripped Gemini and stopped every run.

Stories research first (`storygen.research`): numbered Wikipedia notes, a timeline, and a "notes only" rule. A
topic whose premise the notes don't support fails at stage `research` before any check, and that doesn't count as
a script failure. The fact-check also gets a deterministic list of narrated numbers no note states. The
independent verifier never sees the notes. The Commons pre-check records each asset's licence evidence on the
job (`visual_precheck`).
`fallback.providers` adds free OpenAI-compatible providers (Groq: `GROQ_API_KEY`, off while unset) for text-only
calls: `fallback.prefer_for` (now only `verify`) goes there first. **Authoring stays in one family** (2 Nov, R1):
the writer's own `fact_check` and the `verify_repair` run with `prefer_family` = the writer's family, so the
independent verify and re-check always have 2 other families; before, gemini-write + openai-fact-check + qwen-repair
left no family to re-check (clips deferred forever). The repair sends only the visible text (`verify.REPAIR_KEYS`)
and merges it back, so it fits qwen's per-minute limit. `llm.Deferred.cause` says why (quota / overload /
independence / request_size / timeout, joined with `+`), logged in `deferred`/`deferral_cause` events and the
quality report. Per-run circuit breaker (`llm.TRIPPED`, `llm.breaker_failures`, default 2): a provider with
consecutive timeouts/5xx is skipped for the run; the reviewer's overload wait is the half-open probe. Gemini's
breaker is per model (`gemini:<model>`, tripped after one 503, since 503s also use up free daily quota), and
"gemini" trips only when every allowed model has (3 Nov: 3.6-flash answered while the first two were 503).
`gemini.models` has 4 strong Flash models (flash-latest, 3.8, 3.6, 3.5), each with its own free daily bucket. Backoff
waits have ±25% jitter; a provider's own "try again in Xs" is used exactly. Strong-capable families (measured 2 Nov): gemini, openai (Groq gpt-oss)
and, as owner-approved last resort, **nemotron** (`openrouter-text`: NVIDIA Nemotron-3 Ultra on the free OpenRouter
account, `daily_requests: 25` so the vision reviewer keeps its share). Groq qwen is cheap-calls only
(`output_limits`: 1,000 output tokens/min).
Other text calls use them only when Gemini is out or overloaded, before deferring. A per-minute 429 moves to the
provider's next model (each has its own bucket). Exception (3 Nov): a call that must keep the writer's family (fact_check, verify_repair)
waits out the limit (hint, ≤60 s) instead of crossing to another family, which left verify only the tripped Gemini. The reviewer falls back to a `vision` provider (OpenRouter free, frames only, `_no_audio` drops the audio score)
and, with none configured, waits out Gemini overload spikes (`gemini.overload_retries` × `overload_wait_seconds`) before deferring, because a deferral at review throws away
a finished render. Stories (`qa.min_archive_share`) fail at `visuals` when most scenes are stock footage, not archive images of the subject.

The job lifecycle is in `data/history.json`: `failed` / `quarantined` / `dry_run`, or
`ready` → `uploading` → `scheduled` → `published`. Other statuses are `awaiting_approval` (only when
`review.human_approval` is on), `rejected` (`main.py reject`), `deleted`, and `withdrawn` (set by hand in the data
and not handled anywhere in the code). A gate failure is remade up to `review.attempts` times, after which the
video is quarantined. Only `published` counts toward
the daily target. See the diagram in `docs/PIPELINE_ARCHITECTURE.md`.

### Channel isolation: the key pattern

Each module keeps its paths and settings as **module-level globals** (`jobs.HISTORY`, `jobs.TZ`,
`analytics.REPORTS`, `publish_youtube.TOKEN`, `util.EVENTS`, …). `channels.activate()` rebinds them for the chosen
channel. Channel 1 keeps the original layout (`data/`, `output/`, `reports/`, `youtube_token.json`). Other channels
use `data/channels/<id>/`, `output/channels/<id>/`, `reports/<id>/` and `credentials/<id>/`.

**If you add per-channel state to a module**, do three things: add the global, rebind it in
`channels.activate()`, and redirect it in the autouse `sandbox` fixture in `tests/conftest.py`. Otherwise one
channel will leak into another, or tests will write to real data. Paths derived from `jobs.HISTORY.parent` (as in
`performance`, `gaps` and `growth`) follow the active channel automatically.

## Owner business and finance (Phase 7)

`pipeline/business/` is owner-level, not per channel: `store.BASE` = `data/business/` (git-ignored, like
`reports/business/`) does not move with `channels.activate`, and the test sandbox redirects it. `store.py` is an
event ledger: integer paise with an explicit currency (never summed across currencies), stable ids, idempotency
keys (same key + same content = no-op, different content = refused), atomic writes + `.bak` under its own flock,
strict reads (a damaged or missing-with-.bak file raises `CorruptState`), and `reconcile()` that reports malformed
events and excludes them from every total. `money.py` keeps two books that never mix: **personal** (salary,
essentials, discretionary, personal debt, friend/family borrowing, balance snapshots) and **business** (cleared
receipts, refunds, direct costs, paid operating expenses, tax paid, owner withdrawals, debt repayments). Every
entry needs `confirmed` and an external `ref`. Nothing pending (invoice, commission, views, forecast) is ever an
event: forecasts are `plan.json` items, shown apart from actuals. Net available business cash = receipts − refunds
− direct − opex − withdrawals − repayments − tax still reserved (30% provisional) − ₹5,000 operating reserve; no
extra repayment or discretionary spend is suggested while the conservative personal 30-day forecast is below the
personal reserve. `debt.py` (phase C) holds debt accounts in `debts.json`: only entered or statement values,
unknown stays `None` (never estimated), account numbers masked to the last 4, every edit kept in `changes`. Payments
are ledger events (`debt_payment`/`debt_repayment`, account must exist); one external reference = one payment
(`money._ref_reused`), a mistaken entry is undone by a `reversal` event (both kept, neither counts); instalment
status (scheduled/due/paid/partially_paid/overdue/reversed) comes from EMI + next due date; lender statements
verify an account and `debt.reconcile` lists discrepancies without correcting anything. The aggregate estimate
stands in for accounts marked `in_aggregate` (never added twice). Avalanche/snowball interest impact only with
verified balance, rate (reducing) and EMI on every account. The forecast has conservative, best and
delayed-salary scenarios; `optional` items (an unconfirmed foreclosure quote) stay out of them, and EMIs enter it
only when their due dates are known. Owner money never appears in committed files: channel daily reports print no figures
(`finance.summary_lines`); the old per-channel `finance` command is retired. `python main.py money` shows it in
the terminal only.

## Conventions and invariants

- Read and write state with `util.read_json` and `util.write_json`. Writes are atomic and keep a `.bak`. Pass
  `strict=True` for the queue so that corruption raises `CorruptState` instead of being read as empty.
- Log with `util.log` and `util.event`, which redact secrets. Never print tokens or keys.
- Respect `control`: `killed()`, `DRY_RUN`, `uploads_left()`, `gemini_budget_left()`. Any new outward action
  (upload, delete, comment, channel edit) must be skipped under `--dry-run` and counted against limits. A dry run
  must not consume real state either: it returns its backlog topics when production ends, releases its images, and
  reserves its slots only in memory (`orchestrator._DRY_RESERVED`/`_DRY_TOPICS`). A rehearsal's failed or
  quarantined job is not recorded either (history marks a topic done, so it would burn the topic).
- Uploads must stay idempotent: the job is set to `uploading` before `videos.insert`, a duplicate-title check runs
  against recent uploads, and the token's channel must match `youtube_channel_id` (`ChannelMismatch`). Every other
  YouTube call goes through `channel._yt()`, which checks the same identity once per process (sync, comments,
  withdraw, delete, snapshots). `sync_status` never marks videos deleted when YouTube returns none of them.
- `publish()` refuses (quarantines) a generated video for which `verify.verified(script)` is false: only text the
  claim check passed can go out. `--force` overrides.
- **Automated release gates** (`release.py`, owner authorised autonomous publishing 2 Nov): verified text, claim
  ledger, QA ran and passed, review passed, per-scene grounding recorded and passing, provenance on every scene,
  metadata + AI disclosure, video file, no live duplicate title. Decided at production (`script.json` → `release`,
  shown in dry runs as "release decision: PUBLISH/BLOCKED"), re-checked in `publish()`, and applied by the
  `release decisions` daily step to anything in `awaiting_approval` (pass → ready, fail → quarantined). Fails
  closed: a missing record (e.g. a video made before grounding existed) blocks. Channel identity is checked at
  upload. Test helper `add_job(..., complete=True)` writes a gate-passing record (`conftest.gate_passed`).
- `health.py` (daily steps `health`, `retention`): alerts once a day per condition (no publication for
  `control.alert_no_publish_days`, a content-failure streak, provider trips/overload, upload failures, missed or
  interrupted runs, another channel's stale `runs.json`, low disk), and prunes rendered files of failed /
  quarantined / rejected / deleted / dry-run jobs older than `control.retention_days` (script.json, post.txt and
  error.txt are kept; ready/waiting/uploading/scheduled/published are never touched). Dry runs neither send nor
  delete.
- Analytics v2 (`analytics.fetch_video_analytics`, daily once): identity-checked analytics token, upserts by
  (date, video) (`_upsert`; the snapshot CSVs too), extended metrics with `engagedViews` optional, traffic sources
  (`traffic.csv`) and retention curves (`retention.json`) for the 20 latest public videos; impressions and
  viewed-vs-swiped are not in the API and stay blank. Growth combines views/day, retention and subscribers per
  1,000 views (`feature_boost_*`, geometric mean of the conclusive ones) and writes a repeat/stop/test playbook.
- Visual grounding (`grounding.py`): every scene gets a record (narration, subject, asset + provenance + metadata,
  decision MATCH / CONTEXTUAL / MISMATCH / UNVERIFIED, reasons, reviewer view), saved as `script.json` →
  `visual_grounding`. Deterministic rules come first (missing provenance, wrong period, a later photo/artifact/replica
  under event narration, another celestial body); `visuals.gather` skips Commons candidates they call MISMATCH and
  caps reuse (`visual.max_image_uses`); a MISMATCH left before render fails at `visuals`. The reviewer judges every
  scene with its metadata; an AI "MATCH" can't upgrade metadata that doesn't name the subject. MISMATCH or
  UNVERIFIED, or a MATCH share under `visual.min_match_share` (clips 0.5), fails the review.
- The reviewer must score every criterion (`review.CRITERIA`, plus audio when it heard the mix). A missing score
  fails the review, and string scores ("8/10") are parsed.
- Tests never call real APIs. The `sandbox` fixture redirects all state to `tmp_path`, and `fixed_now` freezes
  the clock. Use the `make_script` and `add_job` helpers. Add a test for every safety-relevant change.
- Code style: compact, dense modules with explanatory module docstrings. Comments explain *why*. Match that style.

## Operational facts and constraints

- **Gemini free tier:** about 20 requests/day per Flash model, per key. Since 3 Nov each channel has its own free
  AI Studio key: cosmic `GEMINI_API_KEY_3`, clips `GEMINI_API_KEY_2` (`gemini.api_key_env`; `GEMINI_API_KEY`
  is a spare); the quota file is
  keyed by key fingerprint, so one channel's exhaustion doesn't block the other. Failed 503 calls also count
  against the free quota. Flash-Lite is higher but is only used for cheap calls. A made video needs ~4–6 strong
  calls (more with a repair or remake), so 2+2/day fits and the old 3+5/day did not (it silently ran checks on
  Flash-Lite). Check live limits in AI Studio.
- **YouTube quota:** `videos.insert` has its own bucket. Other calls share 10,000 units/day, and quotas reset at
  midnight Pacific. `containsSyntheticMedia` doesn't stick on insert, so it is re-set via `videos.update`.
- The API can't change a channel's name or profile picture. The owner does that in Studio.
- `review.human_approval` is `false` on both channels by owner decision: the gates approve and upload. When you
  work in a session, still read the new narrations and look at the frames, because the AI reviewer has passed
  factual errors and mismatched images. Reject bad videos (`main.py reject`). `reject` only changes local state, so use `main.py withdraw` for a video already scheduled on YouTube: it stays private with no publish time, which is reversible. Don't turn approval back on unless
  asked.
- **Never add downloading/ripping of other creators' YouTube videos.** Third-party footage needs a documented
  rights pathway (see `docs/MULTI_CHANNEL.md`).
- `data/` and `reports/` are committed and change on every run, so a dirty tree there is normal. Secrets
  (`.env`, `client_secret.json`, `youtube_token.json`, `credentials/`) and `output/` are git-ignored. Never
  commit them.

## Owner standing instructions (set 6 Nov 2026)

- Talk to the owner in English only.
- Never share the owner's data with anyone — anyone means anyone.
- The owner has enabled the advanced feature and gives permission to access and drive their Chrome
  (YouTube Studio, Meta Business Suite) for this operation.
- Keep working until all videos are made (the 15-day buffer filled); do not stop early.

## Docs

- `docs/PIPELINE_ARCHITECTURE.md`: agents, stages, lifecycle, safety properties, file map.
- `docs/MULTI_CHANNEL.md`: isolation guarantees, the channel-2 history, and the rights/compliance design.
- `docs/COST_CONTROL_AND_OPERATIONS.md`: runbook. Other `docs/*.md` cover strategy, risks and monetization.
- `docs/GROWTH_30_DAY.md`: baseline, per-channel growth strategy, experiments and the 30-day calendar.
- `docs/VIRALITY_AND_LONGFORM.md`: why Shorts stall (seed test, first second, familiarity), the hook headline and
  familiarity score added for it, and the free-stack research for 3–5 minute long-form.
- `README.md`: original Windows/1-a-day setup guide. Parts of it are outdated (current cadence is 2/day per channel
  on a Mac). Trust the code and `config.yaml` over it.
