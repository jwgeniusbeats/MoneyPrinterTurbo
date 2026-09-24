# Order Fulfillment Checklist: Clip Service

Run this every time a Fiverr order comes in for the "cut your podcast or
stream into viral shorts" gig.

## 1. Collect from buyer (should already be in the order requirements)
- [ ] Source video: link (YouTube/Twitch/Drive) or uploaded file
- [ ] Timestamp(s) of the moment(s) to clip
- [ ] Hook text (or note that they want you to write one)
- [ ] Caption style / brand color preference (if any)
- [ ] Package tier: Basic (1 clip) / Standard (5, you provide timestamps) /
      Premium (5, you find the highlights)

## 2. If timestamps are missing (Premium tier, or buyer unsure)
- [ ] Download the source video
- [ ] Skim/scan for a strong self-contained moment (~30-60s): a story, a
      hot take, a punchy exchange, something that works with no context
- [ ] Confirm the pick with the buyer before editing (1-line message)

## 3. Download source video (if a link, not a file)
```bash
yt-dlp --cookies-from-browser chrome -f "bv*[height<=1080]+ba/b[height<=1080]" \
  --merge-output-format mp4 -o "orders/<order_id>/source.%(ext)s" "<url>"
```
Check the license/rights only if it's not obviously the buyer's own content.
The intake FAQ already tells them they must own the rights, but a sanity
glance avoids surprises.

## 4. Build the clip
```bash
cd ~/MoneyPrinterTurbo
.venv/bin/python automation/clip_builder.py \
  "orders/<order_id>/source.mp4" \
  <clip_start_seconds> <clip_end_seconds> \
  "<hook text>" \
  "orders/<order_id>/output.mp4" \
  --project-dir "orders/<order_id>" --project-name project
```
Keep each order's Concat project inside its own `orders/<order_id>/`
folder, not in `automation/portfolio_projects`. That folder is the public
portfolio showcase; client project files don't belong in it.
- Runs Whisper automatically on just that window, no manual transcript
  needed.
- If the buyer sent a custom color/font request, edit `word_style()` in
  `clip_builder.py` before running (or ask, most requests are simple
  enough to hand-tweak once, not worth a CLI flag yet).

## 4b. Standard/Premium (5-pack): transcribe once, not five times

If all 5 clips come from the same source video, don't call
`clip_builder.py` five separate times, each run re-loads Whisper and
re-transcribes. Instead, transcribe the full span once and slice it per
clip:
```python
from automation.clip_builder import transcribe_words, slice_words_for_clip, build_clip

clips = [(120, 150, "hook 1"), (400, 445, "hook 2")]  # (start, end, hook) per clip, seconds
range_start = min(c[0] for c in clips)
range_end = max(c[1] for c in clips)
all_words = transcribe_words(source_video, range_start, range_end)

for i, (start, end, hook) in enumerate(clips):
    words = slice_words_for_clip(all_words, range_start, start, end)
    build_clip(source_video, start, end, hook, f"orders/<order_id>/clip_{i}.mp4",
               project_dir="orders/<order_id>", project_name=f"clip_{i}", words=words)
```
Only worth it when the clips are reasonably close together in the source
(transcribing a 2-hour span to save 4 Whisper runs isn't a win). Far-apart
clips: just call `clip_builder.py` per clip as usual.

## 5. Check framing before delivering
Multi-person shots (interviews, split-screen calls, panels) can break the
default center-crop. It's happened twice already (podcast plus webinar demo
clips). Before delivering:
- [ ] Scrub the output at a few points, especially any moment with more
      than one person on screen
- [ ] If the crop cuts off faces or shows dead space, use `--offset-x` /
      `--offset-y` (single static shot) or `--crop-keyframes-json` (source
      cuts between shots mid-clip: a JSON file with a list of
      `{start, end, scale, offset_x, offset_y, transition}` windows), see
      the docstring in `clip_builder.py`

## 6. Deliver
- [ ] Upload the final MP4 to the Fiverr order
- [ ] One-line note: what's in the clip, and an invite to ask for a
      revision if the hook/timing needs adjusting
- [ ] Mark delivered

## 7. After delivery
- [ ] If a revision comes back, re-run step 4 with adjusted
      `clip_start`/`clip_end`/`hook_text` and the same `--project-name`.
      `clip_builder.py` clears and rebuilds that project folder
      automatically, so you don't need to pick a new name or clean up
      by hand
- [ ] Once the order completes, consider asking (don't require) for a
      review. Social proof matters a lot against competitors already
      sitting on dozens of reviews
