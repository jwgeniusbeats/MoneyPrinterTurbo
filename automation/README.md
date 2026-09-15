# Nognize daily automation

Everything needed for a fully unattended daily pipeline is built and tested,
but the two steps that make it actually *run on its own* are blocked by
Claude Code's safety classifier and need a one-time manual go-ahead from
Jaimy (not something Claude can push through automatically):

1. **Enable the background schedule** (`launchctl load`). The launchd job
   file is ready at `automation/com.nognize.dailypipeline.plist` and already
   copied to `~/Library/LaunchAgents/`. Someone just needs to run:
   ```
   launchctl load ~/Library/LaunchAgents/com.nognize.dailypipeline.plist
   ```
   Runs daily at 06:00 local time.

2. **Confirm running the pipeline itself** — each run generates 2 new
   videos and publishes/schedules them live on YouTube + Facebook, so the
   classifier treats kicking it off as a publish action needing a human in
   the loop, same as any other live-post action in this session.

## What it does when run (`uv run python automation/daily_pipeline.py`)

1. Pops the next 2 topics off `topic_backlog.json` (30 topics queued = ~15 days)
2. Generates the videos via the normal CLI batch pipeline
3. Generates title/caption/hashtags per video (Gemini)
4. Uploads + natively schedules on **YouTube** and **Facebook** at the next
   free 13:00/20:00 CET slot (tracked in `schedule_state.json`, alternates
   automatically, never double-books a slot)
5. Publishes to **Instagram** immediately (its API has no native scheduling,
   unlike YouTube/Facebook) via `instagram_api.upload_reel_via_url()`: briefly
   hosts the video on Google Cloud Storage (public-read), points Instagram's
   media container at that URL, waits for processing, publishes, then
   deletes the temp GCS object.
6. Appends a TikTok manual-post reminder (file path + caption + suggested
   time) to `manual_post_queue.txt` — TikTok's Content Posting API is
   restricted to private-only visibility for unaudited apps, so a 1-click
   manual upload is still needed there until either the app passes TikTok's
   audit or upload-post.com's paid plan gets turned on.

## To run it once manually right now
```
cd ~/MoneyPrinterTurbo
uv run python automation/daily_pipeline.py
```
