# Revenue and unit economics

## Actual numbers (as of 2026-10-24)

| | Value | Source |
|---|---|---|
| Revenue received | ₹0 | `data/finance/ledger.csv` (empty) |
| Cash spent | ₹0 | every service in use is on a free tier |
| Operating profit | ₹0 | `python main.py finance` |
| Cash available for debt repayment | ₹0 | same |

The channel isn't monetized, so nothing it earns today can go toward debt. That stays true until YPP approval **and** a received AdSense payment.

## Cost per video (measured)

Every input is free today, so the **cash** cost per video is ₹0. The real constraint is free-tier quota, measured per run in `data/usage.csv`:

| Resource | Per video (measured) | Daily free limit | 3 videos a day uses |
|---|---|---|---|
| Gemini text calls | about 4–6 (script, fact-check, packaging, review, and rewrites if too short) | 20 per Flash model per day; Flash-Lite higher | 12–18 |
| Gemini vision calls | 0–20 (cached; falls as the cache fills) | Flash-Lite | 0–60 |
| Edge TTS | 8–9 requests | unofficial, no stated limit | about 27 |
| YouTube `videos.insert` | 1 | about 100 a day (separate bucket since 2026-06) | 3 |
| Other YouTube API calls | about 60 units (verify, flag, playlist, sync) | 10,000 units a day | about 200 |

**Measured on 2026-10-24** (`data/usage.csv`, run `e2e-produce-1`, 2 render attempts with a cold cache): 8 text calls, 40 vision calls and 15 TTS calls, so **about 4 text and 20 vision calls per attempt**. The vision count drops as `data/vision_cache.json` fills. Flash-Lite's free daily limit isn't published; if image checks start failing, the pipeline accepts the image ("fails open") and relies on the reviewer, so watch for that in the logs.

So 3 videos a day fits the free tier for text. Vision is the item to watch while the cache is cold. The weak point is quality, not money: when Flash quota runs out, Flash-Lite does the writing.

**Optional paid upgrade (needs your approval; nothing has been bought).** Gemini paid tier for the script and fact-check steps only. Check the current Flash price on ai.google.dev/pricing before deciding. At about 15 text calls a day with a few thousand tokens each, the monthly cost should be small. The benefit is consistently using the stronger model, which is where today's errors came from.

## Scenario model (hypothetical; replace with real data after 30–60 days)

Formula: `Shorts revenue per month = monthly monetized Shorts views ÷ 1000 × RPM` (RPM already includes the 45% creator share).

All inputs are **assumptions**. RPM uses the third-party estimates in `AUDIENCE_AND_NICHE_RESEARCH.md`, which conflict with each other.

| Scenario | Assumed avg views per Short | Shorts per month | Monthly views | Assumed RPM (₹ per 1,000) | Monthly revenue if monetized |
|---|---|---|---|---|---|
| Conservative | 300 | 90 | 27,000 | 5 | ₹135 |
| Base | 3,000 | 90 | 270,000 | 15 | ₹4,050 |
| Upside | 40,000 | 90 | 3,600,000 | 30 | ₹108,000 |

- Only the upside scenario crosses the 10M-views-per-90-days monetization bar (3.6M a month is about 10.8M per 90 days). The conservative and base scenarios never reach monetization on Shorts alone, so their revenue is effectively ₹0.
- Before approval, revenue is ₹0 in every scenario.
- After the formula, apply a tax provision (`finance.tax_rate`, 30% placeholder; confirm with a tax professional) and any reserve, then subtract expenses. What's left is the debt-repayment figure, and only once it has been **received**.

## Break-even

Cash costs are ₹0, so any received revenue is profit before tax. If you approve paid Gemini, break-even is when monthly received revenue covers that bill. The daily dashboard shows both lines.

## Recording money

```
python main.py finance --kind revenue --category adsense --amount 1234.50 --status received --note "Nov payout"
python main.py finance --kind expense --category gemini --amount 450 --status paid --note "Nov API bill"
python main.py finance        # operating profit vs cash available for debt repayment
```
Revenue goes in as `earned` when it appears in YouTube Analytics and `received` when it reaches your bank account. Only `received` counts toward debt repayment.
