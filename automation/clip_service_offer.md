# Clip Service: Fiverr Gig Copy, Pricing & Intake

## Gig title
**I will turn your podcast, stream or webinar into viral vertical shorts with word-by-word captions**

## Gig subtitle / search tags
Podcast clipping, TikTok shorts, Instagram Reels, YouTube Shorts, word-by-word captions, video repurposing, streamer highlights

## Gig description

> Got a podcast, Twitch VOD, webinar or long-form video sitting there doing nothing? I'll cut it into scroll-stopping vertical shorts, ready to post on TikTok, Reels and YouTube Shorts.
>
> **What you get:**
> - Highlight cut from your long video (you point me to the moment, or I find it)
> - Reframed to 9:16 vertical, cover-cropped so nothing important gets cut off
> - Bold, word-by-word animated captions synced to speech (the MrBeast/Hormozi style that keeps people watching)
> - A punchy hook line on screen for the first 1 to 2 seconds to stop the scroll
> - Delivered as a ready-to-upload MP4, 1080x1920
>
> **Great for:** podcasters, streamers, coaches/course creators repurposing webinars, YouTubers, business owners turning talks into content.
>
> Send me a link (or upload the file) and tell me what moment you want. I'll handle the rest. Turnaround 24 to 72h depending on package.

## Pricing / packages (live prices, updated after competitor-based price drop)

| | **Basic** | **Standard** | **Premium** |
|---|---|---|---|
| Clips | 1 clip | 5-pack | 5-pack |
| Price | **$10** ($10/clip) | **$50** ($10/clip) | **$85** ($17/clip) |
| Length per clip | up to 60s | up to 60s | up to 90s |
| Captions | Word-by-word, standard style | Word-by-word, standard style | Word-by-word, **custom font/color/branding** |
| Hook line | Included | Included | Included, custom-written if needed |
| Highlight selection | You provide timestamps | You provide timestamps, or I suggest 1 to 2 | I find and suggest all highlights |
| Revisions | 1 | 2 | 3 |
| Delivery | 48h | 72h | 96h |

Add-ons: extra clip from the same source, Basic/Standard +$10, Premium +$15.

**Anchor pricing note:** priced to compete with the cheapest top-rated competitor found (around $8.50 to $9/clip), while keeping the 5-pack at the same $10/clip as a single clip so the pack doesn't feel like a worse deal. Premium stays higher because it includes highlight-finding, real editorial time, not just export.

## Intake format (what the client must send)

1. **Source video**: a public link (YouTube, Twitch VOD, Google Drive/Dropbox with view access) or a direct file upload. Must be content they own or have rights to repurpose.
2. **Number of clips** wanted (1, 3, 5, etc).
3. **Timestamps** of the moment(s) to cut, if they know them (`mm:ss to mm:ss`). If not provided, note that highlight-finding is a Premium/add-on service.
4. **Hook text** per clip, or "you write it". If the latter, ask for 1 sentence of context on why the clip matters.
5. **Style preference**: caption color/font (default: white text, black stroke, bold), any brand colors/logo to match.
6. **Platform target**: TikTok / Reels / Shorts (doesn't change the export, but useful for hashtag/caption tone if they want that too).
7. **Anything to avoid**: sensitive topics, words to bleep/skip, competitor mentions, etc.

Keep this as a fixed set of Fiverr requirement questions on the gig itself so it's collected automatically at order time.

## Positioning strategy: don't just race on price

Matching the cheapest competitor's price gets the gig noticed, but price
alone isn't a moat: someone can always undercut $10 next month. The actual
structural edge here is the automated pipeline (`clip_builder.py`, Whisper
transcription, keyframe-based crop fixes), which two things fall out of
naturally and should show up in the gig copy and buyer messages, not just
the price:

- **Turnaround speed.** A manual editor doing this in Premiere/CapCut
  takes real hours per clip. The pipeline doesn't. Once a track record
  exists, "delivered same day" or "fastest turnaround in this category"
  is a claim worth making, and it's true, not marketing fluff.
- **5-packs are where the edge compounds.** With shared transcription
  (see order_fulfillment_checklist.md 4b), a 5-clip order from one source
  costs barely more time than a 2-clip one. That's a real reason Standard
  and Premium should be pushed harder than Basic, both for margin and
  because it's a better answer to "why you over the cheaper single-clip
  seller."
- **Consistency.** Word-by-word captions done by hand drift in timing and
  style across a batch. The pipeline doesn't get tired on clip 5 of 5.

Once there's a small review base (3 to 5), the highest-leverage move is
shifting effort from manual Reddit outreach back to organic Fiverr
discovery: reviews and response rate drive Fiverr's own ranking far more
than being the cheapest listing. Outreach is a bootstrap tool for the
first reviews, not the long-term acquisition channel.
