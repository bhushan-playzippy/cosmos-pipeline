# Quality and compliance checklist

Each video's results are written to its `output/<job>/post.txt`. **Hard fail** means quarantine regardless of other scores.

## Automated (`pipeline/qa.py`, `pipeline/review.py`, `pipeline/publish_youtube.py`)

| Gate | Check | Rule | Hard fail |
|---|---|---|---|
| Media | Video and audio streams present, file playable | ffprobe | yes |
| Media | 1080×1920 at 30 fps | exact | yes |
| Media | Duration | 20 s to `max_seconds`+2 | yes |
| Media | Loudness | −18 to −11 LUFS | yes |
| Media | No silence longer than 1.5 s | silencedetect at −40 dB | yes |
| Media | No black segments longer than 0.5 s | blackdetect | yes |
| Media | Generated-starfield scenes ≤ 2 | rights record | yes |
| Media | No text, labels, logos or people in B-roll | Gemini vision (cached) and metadata | asset skipped |
| Rights | NASA items marked © or "copyright" are skipped; source and licence recorded per scene | metadata | asset skipped |
| Editorial | 90–165 words, no repeated lines | count | yes |
| Editorial | Every claim fact-checked with values and arithmetic; misleading implications and wording (radius vs diameter) checked | Gemini, 2nd pass | rewritten |
| Editorial | Accuracy score ≥ 8, all other scores ≥ 6 | AI reviewer | yes |
| Originality | ≤ 25% 5-word overlap with any earlier script; topic not a near-duplicate | shingles, token overlap | yes, or topic skipped |
| Packaging | Title 10–100 chars, description ≤ 4,900, tags ≤ 480 chars, sources listed | count | yes |
| Compliance | AI disclosure line in the description | text | yes |
| Publishing | Uploaded, `publishAt` set, `containsSyntheticMedia` true, read back from YouTube | API | flagged in job |
| Publishing | Not already on the channel (title) | API | upload skipped |
| Publishing | Made-for-kids = no; category = Science & Technology | config | — |

## Human checks (2 minutes per video; enforced: new videos wait as `awaiting_approval`)

Your Mac shows a notification after each daily run when videos are waiting. `python main.py status` lists them,
and `reports/daily-*.md` shows each one's narration. Then run `approve output/<folder>` (or `--all`) or `reject ... --note`.

- [ ] Read the narration in `post.txt`. Does any number look off? Check it against the FACT-CHECK LOG.
- [ ] Skim the video at 2× speed. Does each picture match what's being said?
- [ ] Is the title honest about what the video delivers?

If anything's wrong, set the video to private in Studio, then run `python main.py sync`.

## Never

- Bought views, subscribers or comments; engagement exchanges; bots.
- Copyrighted music or footage; music from YouTube's music partners (it shrinks the Shorts revenue pool).
- Speculation presented as fact; fabricated sources.
- Imitating another creator; real people's likeness in AI content.
- Sponsorship or affiliate links without disclosure.
