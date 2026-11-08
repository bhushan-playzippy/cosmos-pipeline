# Cosmic Scale: free faceless Shorts and Reels pipeline

> **Operating the 3-a-day system (Mac).**
> - **Daily run:** `autopilot/` runs `.venv/bin/python main.py daily` at 13:00.
> - **Check the queue:** `.venv/bin/python main.py status`
> - **Emergency stop:** `touch STOP`
> - **Rehearsal:** `.venv/bin/python main.py daily --dry-run`
> - **Tests:** `.venv/bin/pytest`
>
> Architecture, runbook, risks, monetization and money tracking are in [`docs/`](docs/). Start with
> [`docs/PIPELINE_ARCHITECTURE.md`](docs/PIPELINE_ARCHITECTURE.md) and [`docs/COST_CONTROL_AND_OPERATIONS.md`](docs/COST_CONTROL_AND_OPERATIONS.md).
> The setup steps below are the original Windows guide. On a Mac, use `.venv/bin/python` wherever it says `python`.

This pipeline turns one command into a finished 9:16 video with voiceover, footage, word-by-word captions, a progress bar, music, title, description, hashtags and fact-check sources. It can also post to YouTube and Instagram on a daily schedule.

```
Gemini (free API)  →  topic + script + metadata + Veo prompt
Edge TTS (free)    →  natural voiceover + exact word timings
Veo (your Gemini)  →  cinematic hook clip (optional)
Pexels + NASA      →  footage for every scene (free)
FFmpeg             →  1080×1920 video, captions, progress bar, music, loudness
YouTube / IG APIs  →  auto-post (optional)
GitHub Actions     →  runs daily at 12:00 PM IST (free)
```

---

## Two channels

This project now runs two independent YouTube channels: `cosmic` (this README, `config.yaml`) and `clips`
(Shorts from authorized source videos with original commentary, `channels/clips.yaml`). Commands default to
`cosmic`; add `--channel clips` for the second one. Setup, the rights workflow, limits and the audit are in
**[docs/MULTI_CHANNEL.md](docs/MULTI_CHANNEL.md)**.

> Only one scheduler should run channel 1: the Mac launchd job (`autopilot/`) **or** GitHub Actions (Step 7),
> never both. They keep separate queues and would upload the same slots twice.

## The channel strategy (already decided for you)

**Niche: space and the universe.** It has endless topics, it never goes out of date, it's popular worldwide, it suits faceless videos well, NASA footage is free, and advertisers pay better for science than for entertainment.

**Signature format: "Cosmic Scale".** Every video explains one mind-blowing space fact through an everyday comparison: *"Shrink the Sun to a cricket ball. Earth is a grain of sand eight metres away. The nearest star? A pea in Chennai."* This angle is what makes your channel *original* rather than generic AI content, which is exactly what YouTube now checks for.

**Rules that keep you monetizable:**
1. **Post 1 video a day, at the same time.** Don't post 10 a day. Mass posting is the #1 trigger for YouTube's "inauthentic content" demonetization.
2. **Watch every video and check `post.txt` → sources before posting.** It takes 2 minutes. A wrong fact in a science channel kills trust.
3. **Reply to comments** in the first hour. The algorithm notices.
4. If you use a realistic Veo clip, the pipeline sets YouTube's AI-disclosure flag automatically. On manual uploads, turn on "Altered or synthetic content" yourself.
5. Later, the strongest upgrade is recording the narration in **your own voice**. Real voices grow faster and are safest for monetization.

Want Hindi instead? Set `language: "hi"` in `config.yaml`. Scripts, voice (hi-IN-MadhurNeural) and Devanagari captions all switch.

---

## Step 1: Install (Windows, about 10 minutes)

1. Install **Python 3.12** from python.org. On the first installer screen, tick **"Add python.exe to PATH"**.
2. Open **Command Prompt** and install FFmpeg:
   ```
   winget install Gyan.FFmpeg
   ```
   Close and reopen Command Prompt afterwards.
3. Unzip this project (e.g. to `C:\cosmos-pipeline`), then:
   ```
   cd C:\cosmos-pipeline
   pip install -r requirements.txt
   ```

## Step 2: Free API keys (5 minutes)

1. **Gemini**: open https://aistudio.google.com/apikey, sign in with your Google account and click *Create API key*. The free tier is enough for this pipeline. Your Gemini subscription is not used here.
2. **Pexels**: sign up at https://www.pexels.com/api/ and copy your key.
3. Copy `.env.example` to a new file named `.env` and paste both keys in.

## Step 3: Make your first video

```
python main.py ideas           # brainstorms 25 topics into data/topics.json
python main.py make            # makes the next topic into output/<date>_<topic>/
```
Each output folder contains:
- `video.mp4`: the finished Short/Reel
- `post.txt`: YouTube title, description and tags, the Instagram caption and hashtags, **sources to fact-check**, and the narration
- `script.json`: everything in machine-readable form

Want a specific topic? `python main.py make --topic "Why Saturn could float in water"`

### Use your Gemini subscription for cinematic hooks (optional, recommended)
```
python main.py veo-prompts
```
This prints a Veo prompt and a filename for each of your next 7 topics. In the **Gemini app → Video (Veo)**, paste a prompt, ask for **vertical 9:16**, download the clip and save it in `assets/veo/` under the exact filename shown. That topic's video then opens with your Veo clip. Topics without a clip use stock footage automatically.

### Add music (optional)
Put a few ambient/cinematic `.mp3` files in `assets/music/`. Use YouTube Studio → Audio Library ("attribution not required") or Pixabay Music.

---

## Step 4: Weekly routine for now (about 30 minutes a week)

Until YouTube approves auto-upload (step 5), the simplest routine is batching:

1. **Sunday:** `python main.py veo-prompts` → make the Veo clips → `python main.py make --count 7`
2. Watch each video and check the sources in `post.txt`.
3. **YouTube Studio** (on desktop): upload all 7 and **schedule** one per day at the same time. Copy the text from `post.txt`.
4. **Meta Business Suite** (business.facebook.com, free): schedule the same 7 as Instagram Reels.

That's a fully scheduled week with zero daily effort.

---

## Step 5: YouTube auto-upload (one-time setup, then a Google review)

1. Go to https://console.cloud.google.com, create a project and enable **YouTube Data API v3**.
2. Under **OAuth consent screen**, choose External, fill in the app name and your email, and add yourself as a test user. Then click **Publish app → In production**. If you skip this, your login expires every 7 days.
3. Under **Credentials → Create credentials → OAuth client ID → Desktop app**, download the JSON and save it in this folder as `client_secret.json`.
4. Run `python main.py auth-youtube` and log in with **your channel's** Google account. The browser will say "unverified app" because it's your own app; click Advanced → continue.
5. **Important:** Google locks videos uploaded by new, unaudited API projects to *private*. To lift this, submit the free **YouTube API Services – Audit and Quota Extension Form** (search that name; it's on Google's support site). Describe the app honestly: "personal tool that uploads my own channel's videos." Approval can take a few weeks.
6. Once approved, set `youtube: enabled: true` in `config.yaml`. From then on, `python main.py publish output/<folder>` uploads, and `python main.py auto` makes and uploads.

The free quota allows about 6 uploads a day, which is plenty.

## Step 6: Instagram auto-posting (optional; setup is fiddly)

1. In the Instagram app, go to Settings → Account type → switch to a **Creator** (or Business) account.
2. At https://developers.facebook.com, create an app and add the **Instagram API (Instagram Login)** product. Add your Instagram account as a tester and generate an **access token** with the `instagram_business_content_publish` permission.
3. Find your user ID by opening this URL (with your token) in a browser:
   `https://graph.instagram.com/me?fields=user_id,username&access_token=YOUR_TOKEN`
4. Put `IG_USER_ID` and `IG_ACCESS_TOKEN` in `.env`. In `config.yaml`, set `instagram: enabled: true` and `graph_host: "https://graph.instagram.com"`.
5. Tokens last about 60 days. Every ~50 days, run `python main.py refresh-instagram` and save the new token.

If this feels like too much, scheduling Reels in Meta Business Suite (step 4) works fine indefinitely.

---

## Step 7: Full autopilot with GitHub Actions (free, runs even when your PC is off)

1. Create a **private** repository at github.com and upload all project files **except** `.env`, `client_secret.json` and `youtube_token.json`.
2. In the repo, go to Settings → Secrets and variables → Actions → New repository secret, and add:
   - `GEMINI_API_KEY`, `PEXELS_API_KEY`
   - `YOUTUBE_TOKEN_JSON`: the entire contents of `youtube_token.json`
   - `IG_USER_ID`, `IG_ACCESS_TOKEN` (if using Instagram)
3. Done. Every day at **12:00 PM IST**, GitHub makes one video and posts it to whatever is enabled in `config.yaml`. You can also run it by hand from the **Actions** tab → *Daily video* → *Run workflow*.
4. Each run's video is kept for 7 days under Actions → the run → *Artifacts*.
5. To use Veo hooks on autopilot, upload the clips into `assets/veo/` through GitHub's web upload.

Tip: start autopilot with `privacy: "private"`. Watch a week of uploads in YouTube Studio, then switch to `"public"`.

---

## Settings you might change (`config.yaml`)
| Setting | What it does |
|---|---|
| `channel.name` / `signature` | Your channel name and signature angle (Gemini follows it) |
| `channel.language` | `en` or `hi` |
| `voice.en` / `voice.hi` | Voice (options listed in the file) |
| `captions.size`, `highlight` | Caption look |
| `video.sources` | Order of visual sources |
| `schedule.videos_per_run` | Videos per daily run (keep it at 1–2) |

## Troubleshooting
- **"GEMINI_API_KEY missing"**: the `.env` file isn't in the project folder, or it's still named `.env.example`.
- **Gemini 429 errors**: you've hit the free rate limit. The pipeline waits and retries automatically. Make batches of about 7, not 50.
- **Model not found**: Google renamed its models. Put the current Flash model name first under `gemini.models` (you can see names in AI Studio).
- **Scenes show a starfield instead of footage**: no Pexels key, or nothing matched. Footage is better, so add the key.
- **Video longer than 60s**: rerun `make`. Scripts are designed for 40–55 seconds.
