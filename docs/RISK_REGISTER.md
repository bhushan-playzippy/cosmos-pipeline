# Risk register

Likelihood and impact are each rated L/M/H. **Owner** is who has to act: **sys** = the pipeline does it automatically, **you** = needs a human.

| # | Risk | L | I | Mitigation in place | Next step | Owner |
|---|---|---|---|---|---|---|
| R1 | **Channel fails YPP review as "inauthentic"** (AI slideshow, mass-produced) | H | H | Original to-scale graphics; 3 distinct series and roles; originality screen; fact-checked sources in every description; AI label | Your own voice on some or all videos; moving footage (Pixabay key); review cadence after 30 days | you + sys |
| R2 | **A factual error goes public** (it happened twice today before deletion) | M | H | Fact-check pass with arithmetic; reviewer with a hard accuracy ≥ 8 rule; dashboard lists everything scheduled | Glance at `post.txt` before each slot; consider paid Gemini for the writing and checking steps | you |
| R3 | Gemini free quota runs out, so the weaker Flash-Lite model writes | H | M | Exhausted models skipped; budget caps; NASA image checks cached | Optional paid tier (needs approval) | you |
| R4 | Mismatched or poor visuals (Mars image for the Moon, a crane, Jupiter) | M | M | Vision filter (text, labels, people), copyright filter, reviewer `visual_match`, fallback cap | Pixabay key; human check of frames | sys + you |
| R5 | Edge TTS (unofficial endpoint) breaks or is blocked | M | H | Retries with backoff; job fails cleanly | A fallback voice (your recording, or a paid TTS if approved) | you |
| R6 | Mac off or asleep, so no production | M | M | 2-day buffer; launchd runs on wake | GitHub Actions as a backup runner (`docs/COST_CONTROL_AND_OPERATIONS.md`) | you |
| R7 | **YouTube login expires in 7 days.** The OAuth app is in *Testing* mode (the consent screen said "currently being tested"), so Google expires refresh tokens after 7 days and the autopilot would stop around 2026-10-31 | **H** | H | Clear error with the fix command | **Google Cloud Console → Google Auth Platform → Audience → Publish app (In production)**, then run `python main.py auth-youtube` once more | you |
| R8 | Duplicate upload after a crash | L | M | Job state machine plus title check against the latest 50 uploads | — | sys |
| R9 | Third-party copyrighted image from the NASA library | L | H | Copyright/© filter; rights recorded per scene in `post.txt` | Spot-check credits on any image you're unsure of | sys + you |
| R10 | Wrong AI disclosure | L | M | Flag set after upload and read back; description line; `#AIgenerated` | — | sys |
| R11 | Real person's likeness in an AI video | L | M | Vision filter rejects recognisable people | — | sys |
| R12 | Secrets leak (API key pasted in chat earlier; tokens on disk) | M | H | `.env`, tokens and ledger git-ignored; no secrets in code (scanned 2026-10-24) | **Rotate the Gemini key** in AI Studio, since it was pasted in a chat transcript | you |
| R13 | Account access: the Google account has full YouTube management scope | L | H | Token stored locally only | Turn on 2-step verification (also required for YPP) | you |
| R14 | Debt pressure leads to spending on growth | M | H | No spending without explicit approval; ledger separates profit from cash available for debt | Keep the reserve setting honest | you |
| R15 | 2027-02-01 YPP rules (20M Shorts views, monthly gate) raise the bar | H | M | Long-form compilations noted as the second route | Build the long-form assembler after the first 30 days of data | sys |
| R16 | Prompt injection through researched content | L | M | No web content is fed into prompts yet (sources are model-cited) | Treat fetched pages as data only if retrieval is added | sys |
| R17 | Name clash: "Cosmic Scale" and `@CosmicScale` are taken (about 10 similar names) | H | L | — | Pick a distinctive name when you rename in Studio | you |
