# YouTube Policy Notes: "Cosmic Scale" Shorts channel

All sources accessed **2026-10-24** unless noted. Official = support.google.com / blog.youtube / developers.google.com. Anything else is labelled **[third-party]**.

---

## TL;DR for this channel

- **The YPP entry bar rises on 2027-02-01.** Apply before then if you can. Realistically, a channel with 0 subscribers will not reach the current bar by 2027-01-31, so plan against the **new** numbers: 1,000 subscribers + 8,000 watch hours (12 mo) **or** 20M Shorts views (90 d). [S1, S2]
- **New monthly Shorts gate from 2027-02-01.** Even inside YPP, a channel earns from the Shorts Creator Pool only in months where it has **10M qualified Shorts views over the trailing 90 days**. Falling below that pauses Shorts payouts. It does not remove the channel from YPP. [S1, S2, S3]
- **Creators keep 45%** of allocated Shorts revenue. Music lowers the pool, so use **no licensed music**, which keeps all of a Short's associated revenue in the Creator Pool. [S3]
- **The inauthentic content policy targets exactly this pipeline's risk profile.** It names templated or mass-produced AI content and image slideshows with little narrative. It is enforced at **channel** level. [S4, S5]
- **The current API docs say unverified API projects are no longer locked to private.** That contradicts the 2020 rule, which still appears in the revision history. Verify with a test upload. [S6, S7]
- **Shorts views alone never count toward the watch-hours route.** Only public long-form watch hours count. [S8]

---

## 1. YPP eligibility

### Until 2027-01-31 (current entry bar) [S8, S9]

| Tier | Subscribers | Plus one of |
|---|---|---|
| **Fan funding + Shopping** (expanded/lower tier) | 500, plus **3 valid public uploads in the last 90 days** | 3,000 public watch hours in 12 months **or** 3M public Shorts views in 90 days |
| **Ad revenue + Premium sharing** | 1,000 | 4,000 public watch hours in 12 months **or** 10M public Shorts views in 90 days |

- The lower tier unlocks memberships, Super Chat/Stickers/Thanks, Jewels/gifts and Shopping. Turning these on requires accepting the Commerce Products Module. **It includes no ad revenue.** [S9]
- **India is listed** as an eligible country for the expanded (lower-tier) YPP. [S9]

### From 2027-02-01 (announced 2026-08-10) [S1, S2]

- **New applicants, ad tier:** 1,000 subscribers + **8,000** qualified watch hours in the last 365 days **or** **20M** qualified Shorts views in the last 90 days.
- **Fan-funding / Shopping / Creator Partnerships tier:** unchanged (500 subscribers + 3,000 hours or 3M Shorts views). [S2]
- **Existing partners are grandfathered:** "If you are already in YPP, your status is not impacted by this update." [S2]
- All partners must **accept the updated terms in YouTube Studio by 2027-01-31**. Partners who don't stop earning from the affected features. [S2, S8]
- **Activity rule from 2027-02-01:** a channel counts as active with any of the following [S2]:
  - 1,000 watch hours in the past 365 days
  - 1M Shorts views in the last 90 days
  - 2 long-form uploads or 5 Shorts every 90 days

  A channel that falls below gets a 90-day window to meet the watch-hour or Shorts-view requirement.

### What counts and what doesn't [S8]

- **Watch hours:** only from **public long-form** videos.
  - Excluded: private, unlisted and deleted videos, ad campaigns, Shorts, and unlisted or deleted livestreams that weren't converted to VOD.
  - **Shorts Feed watch hours do not count** toward the 4,000/8,000-hour route.
- **Shorts views:** only views on **public Shorts in the Shorts Feed**.
  - Excluded: private, unlisted and deleted Shorts, ad campaigns, and Image Posts in the Shorts Feed.
- **View counting:** since 2025-03-31, the public Shorts view count includes every start or replay with no minimum watch time. [S7] **Unverified:** whether this public count equals the "qualified/engaged" views used for YPP and pool payouts. Treat them as different numbers.

---

## 2. Shorts monetization model [S3]

1. **The pool:** each month, ad revenue from ads between Shorts in the Shorts Feed is pooled. Part of it covers music licensing costs and the rest forms the Creator Pool.
2. **The split:** the Creator Pool is split by each creator's share of **engaged views**.
3. **The creator share:** creators keep **45%** of their allocated revenue, with or without music. Premium revenue for Shorts is shared at 45% of net allocated revenue.
4. **Music effect:**
   - One music track means half of that Short's engaged-view revenue goes to the Creator Pool. Two tracks means one third.
   - **No music means all of it goes to the pool.** Only music from YouTube's music partners (or Dream Track) triggers this.
5. **Official worked example:** a $100k pool with 20% of Shorts using one track leaves a $90k Creator Pool. A creator with 1% of engaged views is allocated $900 and earns **$405**.
6. **Views that don't earn:**
   - non-original Shorts (unedited clips or compilations)
   - artificial views
   - Shorts that violate advertiser-friendly guidelines
   - views accrued **before accepting the Shorts Monetization Module**
7. **From 2027-02-01:**
   - The 10M-views-per-90-days gate applies every month to Creator Pool and Premium earnings. [S1, S2, S3]
   - Targeted Shorts ads shown to 5 or fewer channels become eligible for direct revenue at 45%. [S3]

**Implication:** keep the background audio original or royalty-free, not from YouTube's music partners.

---

## 3. Monetization policies most relevant to AI/faceless channels [S4, S5]

### Inauthentic content (renamed from "repetitious content" on 2025-07-15) [S4]

**Not allowed:**
- Content that "looks like it's made with a template" or feels repetitive across videos.
- Repetitive content with **low educational value or minimal variation**.
- **Image slideshows, templated storylines, or scrolling text with little narrative or value.** Note that NASA stills with a Ken Burns zoom are close to this pattern.
- "AI-generated content made with generic or unoriginal templates" that appears mass-produced.
- Content that is "mass-produced, generic, repetitive, or manipulative."

**Allowed:**
- The same intro and outro with mostly different content.
- A series where each video has a distinct storyline, focus or concept.
- AI used to edit scripts, generate a background, or visualize an original idea, as long as the final video "shows creative vision and value."

### July 2026 clarification (effective 2026-07-16) [S5, third-party report of an official Creator Insider video]

Three non-monetizable categories:
1. **Generic, repetitive, template-based content** that is easy to mass-produce with AI, CGI or templates, with little variation. Tutorials that reproduce content already common on the platform may fall here too. YouTube's trust and safety chief Matt Halprin described these as "very generic" with no "narrative arc."
2. **Off-putting or distressing** engagement-bait.
3. **AI personas presented as real people or experts** on finance, legal, health or medical topics.

The Creator Insider source video is https://www.youtube.com/watch?v=14Vm0CiyUVE. I have not watched it, so it is unverified by me.

### Reused content (unchanged) [S4]

- Content repurposed without significant commentary, modification or added value is not allowed. This includes **"readings of others' materials, such as news or website text."**
- Do not have scripts paraphrase NASA or Wikipedia text closely.

### What reviewers look at [S4]

- The channel's main theme
- Its most-viewed videos
- Its newest videos
- The videos with the biggest share of watch time
- Metadata (titles, thumbnails, descriptions)
- The About section

Enforcement can remove monetization from the **entire channel**.

### Operational takeaways

- Vary the formats: comparisons, "what if", myth-busts, ISRO explainers, and different visual structures.
- Give every Short a distinct narrative arc. Use real motion graphics or diagrams instead of plain still zooms.
- Have a human review every script.
- Keep a varied, high-quality set of recent videos before applying, because reviewers sample the newest and most-viewed ones.

---

## 4. AI / synthetic content disclosure [S10]

**Disclosure is required for realistic content that viewers could mistake for real**, such as:
- a real person appearing to say or do something they didn't
- altered footage of real events or places
- realistic events that never happened, such as a fake weather event moving toward a real city
- AI-generated music

**No disclosure needed for:**
- Using generative AI "to create or improve a video outline, **script**, thumbnail, title, or infographic."
- Clearly unrealistic or animated content, beauty filters, captions, and audio repair.
- Cloning **one's own** voice for voice-overs.

**Synthetic TTS narration is not explicitly addressed.** I found no rule that a generic TTS voice requires disclosure. **Unverified either way.**

- The page does say that for **"non-music AI audio"** a label may appear in the expanded description. Keeping the "altered or synthetic" toggle on is the conservative choice and costs nothing.
- **Disclosure alone won't limit a video's audience or affect monetization eligibility.**

**Consequences of not disclosing:**
- YouTube may apply a label itself.
- Creators who consistently fail to disclose may face penalties, including content removal or **suspension from YPP**.
- Labels can also be applied automatically through C2PA metadata or detection.

**How to disclose:** the "AI use" (altered content) setting in the Studio upload flow. The API equivalent is the `status.containsSyntheticMedia` field on the `videos` resource. That field is listed among the settable `status` properties on the `videos.insert` reference [S6].

---

## 5. YouTube Data API facts for automation

**Quota** [S6, S7, S11]
- Since 2026-06-01, `videos.insert` and `search.list` each have **their own quota bucket**.
- `videos.insert` costs **1 unit in the "Video Uploads" bucket**, with a **default of 100 calls per day**.
- All other methods share a default of **10,000 units per day**. Quotas reset at midnight Pacific Time.
- History: on 2025-12-04 an upload's cost dropped from about 1,600 units to about 100. It was then moved to its own bucket on 2026-06-01.

**Unverified projects and the private lock** [S6, S7, S12]
- The current `videos.insert` reference says verbatim: *"Videos uploaded from unverified API projects are not restricted to private viewing mode."*
- **This conflicts with the 2020-07-28 rule**, which is still in the revision history. That rule says uploads from unverified projects created after that date are locked private until the project passes an audit.
- I found **no revision-history entry announcing that the rule was lifted**.
- The help page "Videos locked as private" still says such locks **cannot be appealed** and that the fix is to re-upload through a verified client [S12].
- **Treat this as unconfirmed:** do one test upload with `privacyStatus=public` and check the result before relying on unattended public posting.

**Scheduled `publishAt`**
- `status.publishAt` is a settable property, and an `invalidPublishAt` error exists [S6].
- **Unverified:** whether scheduling works for unaudited projects. Historically, a private-locked upload could not be published, and scheduling requires `privacyStatus=private`. If the doc change above is real, scheduling should work, but **test it**.

**Default privacy:** if you don't specify `privacyStatus`, uploads default to **public**, though some can default to private based on account settings [S6]. Always set it explicitly.

**Custom thumbnails for Shorts**
- The YouTube Blog (July 2026) and **[third-party]** Tubefilter (2026-07-24) report that custom Shorts thumbnails are **rolling out to YPP creators** [S13, S14].
- The help page says Shorts thumbnails appear in search, hashtag and audio pages, and on the channel page. **[third-party summary]** Coverage says they are **not shown in the Shorts feed**, where most views happen [S14, S15].
- **Unverified:** whether `thumbnails.set` works for Shorts. The thumbnails.set upload limit rose to 50 MB on 2026-09-14 [S7].
- **Implication:** at 0 subscribers (not in YPP), don't invest in Shorts thumbnails. Invest in the first second of the video.

---

## 6. YPP application requirements [S8]

1. Follow the channel monetization policies.
2. Live in a country or region where YPP is available. India is eligible [S9].
3. **Have no active Community Guidelines strikes.**
4. **Turn on 2-Step Verification** for the Google Account.
5. Have access to **advanced features** on YouTube. Phone or ID verification usually grants this.
6. **Link one active AdSense for YouTube account**, or create one in Studio.
7. **Pass a channel review.** This usually takes about one month and can involve several reviews.
   - If rejected, you can appeal within 21 days or re-apply after 30 days. After a repeat rejection, the wait is 90 days.

---

## Sources (all accessed 2026-10-24)

- **S1:** YouTube Blog, "New opportunities to earn and changes to the YouTube Partner Program" (2026-08-10). https://blog.youtube/news-and-events/youtube-partner-program-updates-2027-new-opportunities-earn/
- **S2:** YouTube Help, "Changes to the YouTube Partner Program." https://support.google.com/youtube/answer/12843009
- **S3:** YouTube Help, "YouTube Shorts monetization policies." https://support.google.com/youtube/answer/12504220
- **S4:** YouTube Help, "YouTube channel monetization policies." https://support.google.com/youtube/answer/1311392
- **S5:** **[third-party]** TechCrunch, "YouTube clarifies policies around AI slop and upsetting videos" (2026-07-20). https://techcrunch.com/2026/07/20/youtube-clarifies-policies-around-ai-slop-and-upsetting-videos/
- **S6:** YouTube Data API, `videos.insert` reference. https://developers.google.com/youtube/v3/docs/videos/insert
- **S7:** YouTube Data API revision history. https://developers.google.com/youtube/v3/revision_history
- **S8:** YouTube Help, "YouTube Partner Program overview & eligibility." https://support.google.com/youtube/answer/72851
- **S9:** YouTube Help, "Overview of the expanded YouTube Partner Program." https://support.google.com/youtube/answer/13429240
- **S10:** YouTube Help, "Disclosing use of altered or synthetic content." https://support.google.com/youtube/answer/14328491
- **S11:** YouTube Data API, "Quota costs" and "Getting started." https://developers.google.com/youtube/v3/determine_quota_cost and https://developers.google.com/youtube/v3/getting-started
- **S12:** YouTube Help, "Videos locked as private." https://support.google.com/youtube/answer/7300965
- **S13:** YouTube Blog, "New thumbnail updates in YouTube Studio." https://blog.youtube/news-and-events/youtube-studio-custom-thumbnail-updates/ (found in search results; I did not fetch it in full)
- **S14:** **[third-party]** Tubefilter (2026-07-24). https://www.tubefilter.com/?p=193294
- **S15:** YouTube Help, "Add custom thumbnails." https://support.google.com/youtube/answer/72431 (search snippet only); **[third-party]** Lindsey Gamble. https://www.lindseygamble.com/blog/youtube-rolls-out-new-tools-for-customizing-shorts-thumbnails
- **Secondary corroboration of the 2027 changes [third-party]:** Search Engine Journal (2026-08-11). https://www.searchenginejournal.com/youtube-updates-partner-program-terms-shorts-payout-rules/585331/
