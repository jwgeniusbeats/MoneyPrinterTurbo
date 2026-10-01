# Animation Service: Hand-drawn-style explainer shorts (draft)

Status: DRAFT, untested. No order has been placed for this offer yet.
Prices below are a launch guess meant to undercut the market while there are
zero reviews. Raise them after the first 3 reviews.

## Why this and not more channel content (researched 2026-10-01)
- YouTube Partner Program via Shorts needs 3M (fan funding) / 10M (ads) Shorts
  views in 90 days on top of subscribers, and the bar doubles for new
  applicants from 2027-02-01. Shorts RPM is roughly $0.03-0.10 per 1,000
  views. At 10M views that is a few hundred dollars per quarter.
- TikTok Creator Rewards is not available in the Netherlands.
- Freelance whiteboard/sketch explainers sell for roughly $500-2,000 each
  (agencies $3,000-8,000). The clip service sells at $10 per clip. The same
  skills, aimed at the explainer product, are worth much more per hour.

## Product
30-45 second vertical (9:16) animated explainer in a hand-drawn "boil" style,
built from code (canvas + Hyperframes), with voice-over and word-by-word
captions. Customer supplies the message; we deliver MP4 plus the editable HTML.

## Launch pricing (guess)
| | Starter | Standard | Pro |
|---|---|---|---|
| Length | 20 s | 30-45 s | 45-60 s |
| Revisions | 1 | 2 | 3 |
| Price | $60 | $120 | $200 |

## Target customers (to validate, not assumed)
- Coaches, course creators and small SaaS who need a 30 s "how it works" clip
- Educators and channels that want a timeline or comparison animation
- Existing podcast/streamer leads from outreach_targets.md who asked about
  animation (lead #3 was skipped earlier because we had no offer for it)

## Validation before building more
1. Make 2 sample animations in different niches (not dinosaurs).
2. Offer 1 free sample to each of 5 leads. Count replies.
3. If fewer than 1 in 5 replies, stop. The market for this offer is not there.

---

# Setup (added 2026-10-01)

## What exists now
- `automation/animation/template.html`: data-driven hand-drawn explainer
  (scene kinds: `hook`, `steps`, `compare`, `stat`, `cta`), word-by-word
  captions, 9:16, deterministic `window.seek(t)`.
- `automation/animation/render.mjs`: renders a script JSON to MP4
  (1080x1920, 30 fps). Tested: 31.5 s clip renders in ~52 s.
- `automation/animation/sample_coach.json`: sample for a coaching niche.
  The sample contains advice, not statistics. Do not put invented numbers in
  customer samples.

Render command (needs `playwright` for Node, ffmpeg, Chromium):
```
node automation/animation/render.mjs automation/animation/sample_coach.json out.mp4
# silent. With voice and music: see the voice section below
```
On the Mac: `npm i playwright && npx playwright install chromium` once, and
ffmpeg is already there for the pipeline. If Chromium lives elsewhere, set
`CHROMIUM_PATH`.

## Voice-over, music, cloned voice (added 2026-10-01)
`automation/animation/build_audio.py` makes the audio and times the scenes
from the real audio length, so nothing is timed by hand any more:
```
python automation/animation/build_audio.py script.json --out-dir build/ --music generate
node automation/animation/render.mjs build/script.timed.json out.mp4 --audio build/mix.wav
```
- **Standard voice:** the local Kokoro server (port 8880, already used by the
  pipeline). Tested in the cloud container with real Kokoro (24 s clip,
  4 scenes). Voices: `--voice af_heart`, `bf_emma`, `am_adam`, ...
- **Customer's own voice, route 1 (recommended): they record it.** Ask for
  one clip per scene, name them `scene_01.m4a`, `scene_02.m4a` ... and use
  `--voice-dir`. No cloning, no consent problems. Not run with real customer
  files yet; the code converts any audio format through ffmpeg.
- **Customer's own voice, route 2: cloned.** Needs a Chatterbox server
  (already supported in `app/services/voice.py`, open source, zero-shot
  cloning from a short reference clip). NOT tested: Chatterbox is not
  installed in the cloud container and I have not run it on the Mac. The
  voice name is whatever the Chatterbox server calls the uploaded reference;
  check that server's README for how to upload one, then pass it as
  `--base-url http://127.0.0.1:4123/v1 --voice <name> --model chatterbox`.
  Expect quality to depend heavily on the reference recording (guessing).
- **Cloning rules (non-negotiable):** only the voice of the person ordering,
  with their written OK in the Fiverr chat. Never a third party's voice, never
  a public figure. Check Fiverr's current rules on AI-generated voice before
  offering this as a paid add-on.
- **Music:** `--music generate` makes a plain licence-free ambient pad
  (synthesised, so no copyright question) and `--music track.mp3` mixes a
  real track under the voice; the music ducks automatically when the voice
  speaks. Do NOT use `resource/songs/` for customers: where those files come
  from and under what licence is unknown to me. Use a track whose licence
  allows commercial use (check the licence per track) or one the customer
  supplies.

## Known limits (be honest with customers)
- Item reveals inside a scene are spread over the scene, not tied to the exact
  word being spoken. Captions follow the voice by word length, not by true
  word timing (Whisper could improve that later).
- Style is clean cartoon/whiteboard, not custom illustration. Customers who
  want branded characters or logos animated need extra work.
- Only 5 scene types. A new layout means editing `template.html`.
- The generated music bed is basic. A real track sounds better.

## Fiverr gig (copy-paste)
**Title:** I will create a hand-drawn style animated explainer short for your business

**Category:** Video & Animation > Explainer Videos (or Short Video Ads)

**Description:**
> Need a 20-45 second vertical explainer for Reels, TikTok or YouTube Shorts,
> but no animator or budget for an agency? I turn your message into a clean
> hand-drawn style animation with big headlines, step-by-step reveals,
> before/after comparisons and word-by-word captions.
>
> **You send:** the idea or a few bullet points (or I draft the script from
> your topic and you approve it).
> **You get:** a ready-to-upload 1080x1920 MP4, captions included.
>
> Great for coaches, course creators, small SaaS tools, consultants and
> educators who explain things and want people to actually finish the video.
>
> **Not included (ask first):** custom characters, logo animation, voice
> cloning.

**Tags:** explainer video, animated explainer, whiteboard animation, shorts, reels, tiktok, captions
**Packages:** see launch pricing above. Add one no-risk extra: "Script writing +$20".

## Outreach template (animation)
Use only in threads or DMs where the person already explains something and
posts no short-form video.

> Hey [name], saw your [post/video about X]. I make short hand-drawn style
> animated explainers (30 s, vertical, captions). I made a free sample on
> your topic so you can judge it before spending anything: [link]. If it's
> useful I do these for $[price]. If not, no worries.

Rules: make the sample on THEIR topic (30-60 min with the template), send
one message, one follow-up after 4 days, then stop.

## Fulfilling an order
1. Get the message in 3-5 bullets. Confirm length and the call to action.
2. Copy `sample_coach.json`, rewrite titles, items and `say` lines. `say` is
   what the captions show and what the voice-over will read.
3. Render, watch the whole clip (not just stills), fix text that overflows.
4. Deliver the MP4 plus the JSON. Ask for the review after delivery.
5. Log the order and what the customer asked to change here, so the template
   can learn from real requests.

## Order of the next actions
1. Create the Fiverr gig (copy above) and the Contra profile.
2. Find 5 leads with an actual topic, make 5 samples on their topics.
3. Send the 5 messages. Count replies after 7 days. Stop rule stays: fewer
   than 1 reply in 5 means this offer is not working.
