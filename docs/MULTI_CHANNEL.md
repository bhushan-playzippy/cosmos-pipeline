# Multi-channel operation: channel 1 (space) + channel 2 (true stories)

Last updated 2026-10-25. Policy facts were checked on that date against the YouTube Help Center, the Data API
reference and the YouTube Terms of Service (sources at the end).

## 1. What runs where

| | Channel 1 `cosmic` | Channel 2 `clips` |
|---|---|---|
| Profile | `config.yaml` (unchanged apart from added identity fields) | `channels/clips.yaml` (standalone, inherits nothing) |
| Niche | space facts with everyday scale comparisons | **Unbelievably True**: real, fact-checked history stories (survivals, disasters, mysteries, coincidences). Changed 2026-10-25; creator clips and YouTuber stats are retired, see below |
| Pipeline | topic → script → TTS → stock visuals → render → QA → AI review | the same pipeline with `writer.template: story` (pipeline/storygen.py): story → hook polish → history fact-check → Wikimedia Commons public-domain images → virality check → QA → AI review |
| Target | 3 confirmed/day, 09:00 / 15:00 / 20:00 IST | 5 confirmed/day, 10:00 / 13:30 / 17:00 / 20:00 / 23:00 IST |
| YouTube channel | `UCS3WDSezET11wMQjfWuc2OQ` (token verified 2026-10-25) | **not linked yet**: uploads off |
| Queue | `data/history.json` | `data/channels/clips/history.json` |
| Outputs / reports | `output/`, `reports/` | `output/channels/clips/`, `reports/clips/` |
| OAuth token | `youtube_token.json` / `$YOUTUBE_TOKEN_JSON` | `credentials/clips/youtube_token.json` / `$YOUTUBE_TOKEN_JSON_CLIPS` |
| Gemini key | `$GEMINI_API_KEY` | `$GEMINI_API_KEY_CLIPS` |
| Kill switch | `STOP` (all) or `STOP.cosmic` | `STOP` (all) or `STOP.clips` |
| Autopilot | `autopilot/com.cosmicscale.daily.plist` (installed) | `autopilot/com.cosmicscale.clips.plist` (**not installed**) |

`python main.py channels` lists the profiles and refuses to run if two of them share a YouTube channel ID, a
token file, a token variable or a data folder. Every command takes `--channel <id>`. Without it, commands
work on channel 1, so the existing launchd job is unchanged.

## 2. Isolation guarantees and how they're enforced

- **One process, one channel.** `channels.activate()` rebinds every module's paths, the token, the timezone,
  the kill switch and the log tag. One channel can't read or write another's queue, analytics, usage or ledger.
  Tested in `tests/test_channels.py`.
- **Identity check before every upload.** The `channels.list(mine=True)` call that the duplicate guard already
  makes is also used to compare the token's channel with the profile's `youtube_channel_id`, at no extra quota.
  A mismatch, or a profile without an ID, raises `ChannelMismatch`, nothing is uploaded, and the job stays
  `ready`. `auth-youtube` refuses to *save* a token that belongs to the wrong channel.
- **No duplicate uploads.** The job is set to `uploading` before `videos.insert`. If a run dies mid-upload,
  the next run reconciles with YouTube: it adopts the video if it's there, or retries only after
  `reconcile_after_minutes`. A title that matches *another* job's video is quarantined. Uploading
  under that other job's ID is not allowed.
- **No concurrent runs of one channel.** A per-channel `flock` (`data/…/.pipeline.lock`) blocks a second run;
  different channels can run in parallel.
- **No silent state loss.** JSON state is written atomically (temp file → rename, previous version kept as `.bak`).
  A damaged queue stops the run (`CorruptState`) instead of being read as empty, which would free every slot.
- **No secrets in logs.** `util.log` and the structured event log (`data/…/events.jsonl`) redact OAuth access
  and refresh tokens, Google API keys and client secrets.

## 3. The clip channel: agents and gates

```
discover ──► rights gate ──► ingest ──► segments ──► editorial ──► render ──► compliance ──► human approval ──► publish
 (NASA API,   hard: expired,   allowlisted  sentence-     original      9:16, captions,  hard checks:     main.py approve   identity check,
  inbox,      revoked, NC/ND,  hosts only;  aligned,      intro+outro;  credit on        rights, use,                       rights re-check,
  YT leads)   no evidence...   never YT/    scored,       refuses if    screen, -14      attribution,                       idempotent upload,
              soft → human     social       de-duplicated not framable  LUFS             ≥25% original,                     verify, sync
                                                                                          technical, AI review
```

The rights gate runs again at ingest, at segmentation, when production starts, in compliance, and at the
moment of publishing. Revoking a source (`revoke`) quarantines its queued Shorts and flags the live ones for
a take-down decision, which stays with a human.

**The three questions are kept separate:**
1. *May we use this source?* That's the rights gate (`pipeline/clips/rights.py`).
2. *Does the Short follow policy?* That's compliance (`pipeline/clips/compliance.py`) plus your approval.
3. *Is it monetizable?* Code can't answer this. It is tracked in reports and never assumed.

### Why the editorial narration is mandatory
YouTube's channel monetization policy defines reused content as repurposing content "without adding significant
original commentary, substantive modifications, or educational or entertainment value", and says the policy
"applies even if you have permission from the original creator". It is reviewed at channel level. The Shorts
policy excludes "compilations with no original content added". So the pipeline always adds an original
context intro and an explanation/payoff outro. Compliance requires that narration to be at least 25% of the
runtime (`compliance.min_original_share`). A clip the editor can't frame honestly is rejected. Even so,
**a channel built on other people's footage carries real monetization risk.** The narration has to add value
in substance, not just fill the 25% share.

### Length
Shorts can run up to 3 minutes, but a Short **over one minute with an active copyright claim is blocked**.
The clip channel is capped at 57 s (`video.max_seconds`), which gives a hard limit of 59 s with the gate's
tolerance.

### Remix
The Data API's `videos.insert` has no remix, source or collab fields. Shorts Remix happens only inside the
YouTube app, and the `platform_remix` pathway is hard-rejected for automation. If you want a remix, make it
by hand in the app.

### Where creator footage legally comes from
Listing a creator in `discovery.creators` (PewDiePie, MrBeast, IShowSpeed, Markiplier and jacksepticeye are
listed now) only produces **leads**: their recent uploads, read as metadata. A lead is not permission. A Short
can be made only from one of these:

1. **A clipping campaign you joined** (`pathway: clip_campaign`). Large creators pay clippers per view through
   their own Discord/Whop campaigns or marketplaces. Wikipedia reports this for MrBeast (the Vyro
   marketplace, $50 per 100k views via the Clipping agency), N3on and Clavicular, and lists Airrack's Clip Farm.
   *These reports are secondhand and unverified.* Read the actual campaign terms: platforms allowed, whether
   you can monetize on YouTube, length, required hashtags, credit and submission rules. Save a screenshot or
   PDF as the evidence, and put the campaign's rules under `requirements:`. The pipeline adds them to every
   Short and compliance checks they're there. The daily report lists published Shorts per campaign so you
   can submit them for payment.
2. **Written permission** from the creator or their management (`pathway: written_permission`), covering
   edit, publish on YouTube and monetize.
3. **The creator's own published reuse policy** (`creator_policy`), only if it really covers monetized
   re-uploads. On 2026-10-25 **no such public permission was found for PewDiePie**, and Wikipedia records
   copyright actions involving his content. Ask first.

The footage file always comes from the campaign or creator, usually as a Drive or Discord pack. Put it in
`assets/sources/clips/inbox/` with a sidecar. Add `youtube_id: <id>` to the sidecar to attach it to the
discovered lead. YouTube, Twitch, Kick, TikTok, Reddit, Streamable and similar sites are never downloaded from.
Two risks remain even with permission:
- **Game music and streamers' background music** in footage can trigger Content ID claims. Skip segments
  with licensed music.
- **The reused-content review applies to the whole channel.** The commentary has to be real every time.

## 4. Setup for channel 2 (manual steps, in order)

1. **Create or choose the second YouTube channel** (a Brand Account is fine) and decide its final name.
   Change `channel.name` in `channels/clips.yaml` to match.
2. **Gemini key:** create a key in a different Google account or project and put it in `.env` as
   `GEMINI_API_KEY_CLIPS`. Sharing channel 1's free key would cut into its 20-calls-a-day-per-model budget.
3. *(Recommended)* **Separate Google Cloud project** for channel 2's OAuth client, so each channel has its own
   10,000-unit Data API quota. Save the client as `credentials/clips/client_secret.json` and set
   `credentials.client_secret` in the profile. Otherwise both channels share `client_secret.json` and its quota.
4. **Authorize:** `python main.py --channel clips auth-youtube`. Pick the *clip* channel in Google's account
   picker. The command prints the channel ID; paste it into `channel.youtube_channel_id`. (After that, a login
   to the wrong channel is refused and nothing is saved.)
5. **Install transcription:** `.venv/bin/pip install -r requirements-clips.txt` (already done on this Mac;
   the Whisper model downloads once, about 470 MB).
6. **Get permission:** join one or more creators' clipping campaigns, or get written permission (see above).
   Save the terms or permission under the project, e.g. in `docs/permissions/` (consider adding that folder to
   `.gitignore`). For a creator whose whole campaign covers you, add a `rights:` block to their watchlist entry
   in `channels/clips.yaml`.
7. **Footage:** drop each campaign or creator file into `assets/sources/clips/inbox/` with a sidecar copied
   from `docs/templates/source.rights.example.yaml`, then run `discover`. Check `sources` and approve with
   `rights-approve '<id>' --reviewer <you>`. A source that fails a hard rule can't be approved.
8. **Produce and check:** `python main.py --channel clips make --count 3`, then watch each `video.mp4` and read
   its `post.txt`. Approve with `python main.py --channel clips approve output/channels/clips/<folder>`.
9. **Enable uploads** only after steps 4–8: set `youtube.enabled: true` (still `privacy: private`, scheduled
   with `publishAt`).
10. **Autopilot** (once you trust the output):
   `cp autopilot/com.cosmicscale.clips.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.cosmicscale.clips.plist`

## 5. Daily operation

```
python main.py --channel clips daily          # sync → reconcile/upload → discover (only if thin) → produce → upload → report
python main.py --channel clips status         # queue, free slots, Shorts awaiting approval
python main.py --channel clips sources        # registry + rights review queue
python main.py --channel clips revoke '<id>' --note "creator withdrew permission on 2026-11-20"
touch STOP.clips                              # stop channel 2 only
```

The dashboard is at `reports/clips/daily-<date>.md`. It shows confirmed publications against the 5/day
target, any shortfall, the rights funnel and rejection rate, the review queue, segment stats, upload success
and Shorts awaiting approval. The weekly report groups by creator and segment type, and adds **views per day
since publication**, because Shorts of different ages have had different exposure. Groups with fewer than
`analytics.min_sample` videos are marked inconclusive.

**If fewer than 5 Shorts pass, fewer are published.** The shortfall is recorded, and no gate is relaxed to
fill a slot. `buffer_days: 2` keeps up to 10 Shorts queued ahead to absorb bad days.

## 6. Limits and costs (free tiers; verify before relying on them)

| Resource | Limit | Per clip Short |
|---|---|---|
| Gemini free tier | ~20 requests/day per Flash model per key; Flash-Lite as fallback (weaker) | 2 calls (editorial + compliance review) |
| YouTube Data API | 10,000 units/day per Cloud project; `videos.insert` has its own bucket (~100/day) | ~1 insert + ~5 units (identity/dup check, verify, disclosure update, playlist) |
| `search.list` (optional discovery) | 100 units per call | off by default (`youtube_searches_per_run: 0`) |
| NASA Image & Video Library | no key; be polite | 1 manifest + 1 video + 1 caption download per source |
| Edge TTS | free, unofficial endpoint (can break) | 2 calls |

Five Shorts a day takes about 10 Gemini calls on channel 2's own key, which fits the free tier.
Out-of-pocket cost today is **₹0**. Log any real spend with `python main.py --channel clips finance --kind expense ...`
(each channel has its own ledger).

## 7. Audit findings (2026-10-25) and status

| # | Pri | Finding | Status |
|---|---|---|---|
| 1 | P0 | `history.json` written non-atomically; a damaged file was read as an **empty queue**, which frees every slot and leads to double-scheduling | **Fixed**: atomic writes + `.bak`; damaged queue raises |
| 2 | P1 | Uploads went to whichever account the token belonged to (`mine=True`), with no channel check | **Fixed**: identity check on every upload and at auth |
| 3 | P1 | A second channel would have shared one token, one queue and one Gemini key | **Fixed**: per-channel profiles, paths, token variable and key |
| 4 | P1 | A crash between `videos.insert` and saving the result relied only on title matching; two jobs with the same title could share one video ID | **Fixed**: `uploading` state + reconcile; collision quarantined |
| 5 | P1 | No lock: a manual run during the launchd run could race on the queue | **Fixed**: per-channel lock |
| 6 | P1 | `.github/workflows/daily.yml` and the launchd job both run `daily` for channel 1. If both were active, two separate queues would upload the same slots | **Open (latent)**: the repo has no remote, so the workflow is inactive. Use one scheduler only |
| 7 | P1 | Daily report read `data/usage.csv` directly (would mix channels) | **Fixed** |
| 8 | P2 | Analytics token refresh failure crashed the weekly report | **Fixed**: logged blocker |
| 9 | P2 | Clip folders could collide within one second (found by a test) | **Fixed** for clips; channel 1 has the same pattern but remakes are seconds apart (P3) |
| 10 | P2 | The OAuth app is in Testing mode, so refresh tokens expire every 7 days (both channels) | **Open**: publish the OAuth consent screen |
| 11 | P2 | The AI reviewers (both channels) pass weak or partly wrong content on the Flash-Lite fallback | **Mitigated**: human approval stays on for both channels |

## Sources (accessed 2026-10-25)
- Channel monetization policies (reused / inauthentic content): https://support.google.com/youtube/answer/1311392
- Shorts monetization policies: https://support.google.com/youtube/answer/12504220
- Remix: https://support.google.com/youtube/answer/10623810
- Shorts length: https://support.google.com/youtube/answer/10059070
- `videos.insert`: https://developers.google.com/youtube/v3/docs/videos/insert
- YouTube Terms of Service ("Permissions and Restrictions"): https://www.youtube.com/static?template=terms
- NASA media usage guidelines: https://www.nasa.gov/nasa-brand-center/images-and-media/

## 8. Agents on both channels (added 2026-10-25)
- **Virality check (pre-publish, pipeline/virality.py):** scores the hook (≤12 words with stakes), title, length,
  loop and hashtags. Enforces `#Shorts` plus 2–4 niche hashtags on the description's last line (the QA gate fails
  without them). The score and tips go into post.txt.
- **Gap finder (daily, pipeline/gaps.py):** per public video, views/hour, like and comment rate, plus a diagnosis
  of what's holding it back. The best performers' titles and hooks feed the next topic brainstorm.
- **Growth agent (pipeline/growth.py):** best slots, roles and types by views/day once samples are big enough.
- Retention (average % viewed) needs `python main.py [--channel clips] auth-analytics` and the YouTube Analytics
  API enabled in the Cloud project.

## 9. Channel 2 history
Creator clips (permission/campaign-only) → YouTuber stats/"Higher or Lower" (9 views, dropped) → true stories.
The clip code (pipeline/clips/) and its profile (archive/clips-creator-clips-profile-*.yaml) are kept and tested.
The 10 stats Shorts were set private (job status `withdrawn`), not deleted.
