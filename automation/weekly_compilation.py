"""
Bundles up to MAX_CLIPS not-yet-used shorts (oldest first, no age cutoff --
see pick_clips()) into one long-form YouTube video ("5 Psychology Facts That
Will Blow Your Mind") and uploads it as a normal (non-Shorts) video.

Why: YouTube Shorts monetization needs 10M Shorts views in 90 days — a very
high bar for a new channel. Long-form monetization needs 4,000 watch HOURS
in 12 months — much more reachable, and this channel already has the raw
footage; it just needs bundling into something long-form-shaped. A 5-7
minute compilation of already-produced shorts, back to back, gets counted as
long-form and racks up watch-hours far faster per video than any individual
short could.

Run manually with: uv run python automation/weekly_compilation.py
Intended to be invoked weekly by a scheduled task, after enough shorts exist.
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app.services import llm, youtube_api  # noqa: E402
from automation.daily_pipeline import (  # noqa: E402
    LINK_IN_BIO_CTA,
    MANUAL_REMINDER_FILE,
    _atomic_write_json,
    build_seo_tags,
)
from automation.post_log_lock import locked_post_log  # noqa: E402

POST_LOG_FILE = os.path.join(BASE_DIR, "automation", "post_log.json")
COMPILATION_LOG_FILE = os.path.join(BASE_DIR, "automation", "compilation_log.json")
COMPILATIONS_DIR = os.path.join(BASE_DIR, "Videos", "compilations")
MIN_CLIPS = 3
MAX_CLIPS = 8
# "Full Deep Dives" playlist, created 2026-09-24 -- compilations otherwise
# sit undiscovered (confirmed: both existing ones had 2-5 views days after
# posting despite same-channel Shorts getting hundreds), since YouTube
# treats Shorts and long-form as separate discovery feeds with no organic
# crossover for a channel this small.
COMPILATIONS_PLAYLIST_ID = "PLOp_76iMfnsI"


def load_post_log() -> list:
    if not os.path.exists(POST_LOG_FILE):
        return []
    with open(POST_LOG_FILE) as f:
        return json.load(f)


def load_compilation_log() -> list:
    if not os.path.exists(COMPILATION_LOG_FILE):
        return []
    with open(COMPILATION_LOG_FILE) as f:
        return json.load(f)


def save_compilation_log(entries: list):
    # Atomic write -- this script's own comment above (ffmpeg re-encode +
    # a real YouTube upload, "several minutes") is exactly the crash-mid-
    # write window a plain open(path, "w") is exposed to; see the same fix
    # applied to schedule_state.json/topic_backlog.json in daily_pipeline.py.
    _atomic_write_json(COMPILATION_LOG_FILE, entries)


def pick_clips(post_log: list, already_used: set) -> list:
    # No age cutoff: at VIDEOS_PER_RUN=4/day (~28/week) but MAX_CLIPS=8/run,
    # a rolling 7-day window would let most clips age out of every run's
    # lookback before ever being picked, silently dropping them from the
    # watch-hour compilation forever. Instead, pick from ALL not-yet-used
    # clips (already_used, from compilation_log.json, is what actually
    # prevents repeats) -- oldest first, so nothing waits indefinitely and
    # any backlog just rolls over to next week's run instead of vanishing.
    candidates = []
    for entry in post_log:
        if entry["task_id"] in already_used:
            continue
        video_path = entry.get("video_path")
        if not video_path or not os.path.exists(video_path):
            continue
        candidates.append(entry)
    candidates.sort(key=lambda e: e["posted_at"])
    return candidates[:MAX_CLIPS]


def concat_videos(video_paths: list, output_path: str):
    """Re-encode + concatenate via ffmpeg's concat filter (robust to minor
    encoding differences between clips, unlike the stream-copy concat
    demuxer)."""
    filter_inputs = "".join(f"-i {path!r} " for path in video_paths)
    n = len(video_paths)
    filter_complex = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n)) + f"concat=n={n}:v=1:a=1[outv][outa]"
    cmd = (
        f"ffmpeg -y {filter_inputs} "
        f'-filter_complex "{filter_complex}" -map "[outv]" -map "[outa]" '
        f"-c:v libx264 -preset medium -crf 20 -c:a aac -b:a 128k {output_path!r}"
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=900)
    if result.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(f"ffmpeg concat failed: {result.stderr[-1000:]}")


THUMB_FONT = os.path.join(BASE_DIR, "resource", "fonts", "Anton-Regular.ttf")
MAX_TITLE_LEN = 60
MAX_THUMB_WORDS = 5


def generate_compilation_metadata(clip_titles: list) -> dict:
    n = len(clip_titles)
    prompt = (
        f"Write a YouTube title, thumbnail text and description for a compilation "
        f"video bundling {n} short psychology facts back to back. The individual "
        f"clip titles, in order, are:\n"
        + "\n".join(f"{i+1}. {t}" for i, t in enumerate(clip_titles))
        + "\n\nRules for TITLE:\n"
        f"- {MAX_TITLE_LEN} characters or fewer (longer titles get cut off in search).\n"
        "- Build curiosity around the single strongest or most surprising fact above, "
        "not a generic summary of the whole list. Name the specific thing (the "
        "behaviour, the effect) so the viewer wants to know the answer.\n"
        f"- You may start with the number {n}, but only if it still reads naturally.\n"
        "- Do NOT use: Mind-Blowing, Blow Your Mind, Blow Your Brain, Explain, "
        "Will Shock You, Insane, Crazy. Do NOT promise anything the clips do not show.\n"
        "Rules for THUMB:\n"
        f"- {MAX_THUMB_WORDS} words or fewer, ALL CAPS, a bold claim or question that "
        "completes the title instead of repeating it (e.g. \"YOUR BRAIN LIES TO YOU\").\n"
        "Rules for DESCRIPTION: 2-3 sentences summarizing the compilation, then a "
        "numbered list of the clip titles, then relevant hashtags.\n"
        "Reply in exactly this format, nothing else:\n"
        "TITLE: ...\nTHUMB: ...\nDESCRIPTION: ..."
    )
    try:
        response = llm._generate_response(prompt=prompt)
        title, thumb, description = "", "", ""
        if "TITLE:" in response and "THUMB:" in response and "DESCRIPTION:" in response:
            title = response.split("TITLE:")[1].split("THUMB:")[0].strip()
            thumb = response.split("THUMB:")[1].split("DESCRIPTION:")[0].strip()
            description = response.split("DESCRIPTION:")[1].strip()
        if title and thumb and description:
            return {
                "title": title[:MAX_TITLE_LEN],
                "thumb_text": " ".join(thumb.upper().split()[:MAX_THUMB_WORDS]),
                "description": description[:5000],
            }
    except Exception as e:
        print(f"compilation metadata generation failed, using fallback: {e}")
    return {
        "title": f"{n} Psychology Facts About How Your Brain Works"[:MAX_TITLE_LEN],
        "thumb_text": "YOUR BRAIN LIES TO YOU",
        "description": "This week's psychology facts, back to back:\n"
        + "\n".join(f"{i+1}. {t}" for i, t in enumerate(clip_titles))
        + "\n\n#psychology #mindfacts #shorts",
    }


def _wrap_to_width(draw, text, font, max_width):
    lines, current = [], ""
    for word in text.split():
        trial = f"{current} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not current:
            current = trial
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def make_compilation_thumbnail(video_path: str, thumb_text: str, n_facts: int, out_path: str):
    """1280x720 thumbnail: a text-free frame from the compilation as background,
    darkened toward the left, with a "{n} FACTS" badge and big hook text. The old
    approach reused a vertical 1080x1920 frame of one clip's title card, which
    YouTube crops badly and which shows an unrelated clip's headline."""
    from PIL import Image, ImageDraw, ImageFont

    from PIL import ImageStat

    W, H = 1280, 720
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", video_path],
        capture_output=True, text=True, timeout=30, check=True,
    )
    duration = float(probe.stdout.strip())

    # Burned-in captions sit at ~70% of the frame height, so crop a 16:9 band
    # that ends above them. Sample several moments (skipping the title card at
    # the start and the end card) and keep the frame with the most detail at a
    # mid brightness, instead of whatever happens to be on screen at one fixed
    # timestamp (often dark or an abstract texture).
    best, best_score = None, None
    for i, frac in enumerate((0.15, 0.30, 0.45, 0.60, 0.75)):
        frame_path = f"{out_path}.frame{i}.jpg"
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-ss", f"{duration * frac:.2f}", "-i", video_path,
                 "-frames:v", "1", "-q:v", "2", frame_path],
                capture_output=True, timeout=30, check=True,
            )
            frame = Image.open(frame_path).convert("RGB")
            fw, fh = frame.size
            crop_h = round(fw * H / W)
            top = min(max(0, round(fh * 0.30)), fh - crop_h)
            band = frame.crop((0, top, fw, top + crop_h)).resize((W, H), Image.LANCZOS)
            stat = ImageStat.Stat(band.convert("L"))
            score = stat.stddev[0] - abs(stat.mean[0] - 110) * 0.3
            if best_score is None or score > best_score:
                best, best_score = band, score
        except Exception:
            continue
        finally:
            if os.path.exists(frame_path):
                os.remove(frame_path)
    if best is None:
        raise RuntimeError("could not extract any frame for the thumbnail")
    bg = best

    shade = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shade)
    for x in range(W):
        sd.line([(x, 0), (x, H)], fill=(0, 0, 0, int(215 * (1 - x / W) ** 1.1) + 35))
    img = Image.alpha_composite(bg.convert("RGBA"), shade)
    draw = ImageDraw.Draw(img)

    badge_font = ImageFont.truetype(THUMB_FONT, 62)
    badge_text = f"{n_facts} PSYCHOLOGY FACTS"
    bw = int(draw.textlength(badge_text, font=badge_font))
    draw.rounded_rectangle((48, 44, 48 + bw + 56, 44 + 96), radius=18, fill=(255, 205, 40))
    draw.text((48 + 28, 44 + 10), badge_text, font=badge_font, fill=(20, 20, 20))

    max_w, size = 760, 190
    while True:
        font = ImageFont.truetype(THUMB_FONT, size)
        lines = _wrap_to_width(draw, thumb_text, font, max_w)
        line_h = int(size * 1.08)
        if (len(lines) <= 3 and len(lines) * line_h <= 470) or size <= 70:
            break
        size -= 8
    y = 190 + (470 - len(lines) * line_h) // 2
    for i, line in enumerate(lines):
        color = (255, 205, 40) if i == len(lines) - 1 else (255, 255, 255)
        draw.text((52, y), line, font=font, fill=color, stroke_width=8, stroke_fill=(0, 0, 0))
        y += line_h

    img.convert("RGB").save(out_path, "JPEG", quality=90)


def main():
    post_log = load_post_log()
    compilation_log = load_compilation_log()
    already_used = {tid for entry in compilation_log for tid in entry["task_ids"]}

    clips = pick_clips(post_log, already_used)
    if len(clips) < MIN_CLIPS:
        print(f"Only {len(clips)} unused clip(s) total "
              f"(need {MIN_CLIPS}+), skipping this week's compilation.")
        return

    video_paths = [c["video_path"] for c in clips]
    titles = [c.get("title") or c["subject"] for c in clips]

    tmp_output_path = os.path.join(tempfile.gettempdir(), f"compilation_{datetime.now():%Y%m%d}.mp4")
    print(f"Concatenating {len(clips)} clips...")
    concat_videos(video_paths, tmp_output_path)

    # Moved out of tempfile into a permanent Videos/ location (not deleted
    # at the end, unlike the old tmp_output_path) -- tiktok-daily-post needs
    # the file to still exist whenever it eventually processes the queue
    # entry appended below, which can be hours or days after this run.
    os.makedirs(COMPILATIONS_DIR, exist_ok=True)
    output_path = os.path.join(COMPILATIONS_DIR, os.path.basename(tmp_output_path))
    os.replace(tmp_output_path, output_path)

    meta = generate_compilation_metadata(titles)
    description = meta["description"] + LINK_IN_BIO_CTA
    categories = [c.get("category") for c in clips if c.get("category")]
    top_category = max(set(categories), key=categories.count) if categories else "general"
    seo_tags = build_seo_tags(
        subject=" ".join(titles)[:100],
        title=meta["title"],
        category=top_category,
        hashtags=["#psychology", "#compilation"],
        is_short=False,
    )
    print(f"Uploading compilation: {meta['title']!r}")
    yt_result = youtube_api.upload_video(
        video_path=output_path,
        title=meta["title"],
        description=description,
        tags=seo_tags,
        privacy_status="public",
    )
    video_id = yt_result.get("id")
    print(f"Compilation uploaded: {video_id}")

    thumb_path = os.path.join(tempfile.gettempdir(), f"thumb_{video_id}.jpg")
    try:
        make_compilation_thumbnail(output_path, meta["thumb_text"], len(clips), thumb_path)
        youtube_api.set_thumbnail(video_id, thumb_path)
    except Exception as e:
        print(f"Thumbnail set failed (non-fatal): {e}")
    finally:
        if os.path.exists(thumb_path):
            os.remove(thumb_path)

    try:
        youtube_api.add_video_to_playlist(COMPILATIONS_PLAYLIST_ID, video_id)
        print(f"Added to Full Deep Dives playlist: {video_id}")
    except Exception as e:
        print(f"Add to playlist failed (non-fatal): {e}")

    compilation_log.append({
        "video_id": video_id,
        "title": meta["title"],
        "task_ids": [c["task_id"] for c in clips],
        "clip_count": len(clips),
        "posted_at": datetime.now(timezone.utc).isoformat(),
    })
    save_compilation_log(compilation_log)

    # Queue for TikTok too, via the same manual_post_queue.txt /
    # tiktok-daily-post flow the daily shorts already use -- TikTok's
    # Creator Rewards Program needs videos over 1 minute, which none of the
    # daily shorts (25-48s, tuned for short-form completion rate) qualify
    # for, but this 5-7 minute compilation already does, at no extra
    # content-creation cost.
    #
    # This compilation also needs its own post_log.json entry, the same way
    # every daily short gets one before being queued (see daily_pipeline.py's
    # run_batch): mark_tiktok_posted.py and update_tiktok_stats.py both only
    # know about videos that have a post_log entry with a tiktok field.
    # Without one, tiktok-daily-post posts this file, mark_tiktok_posted.py
    # can't find any matching video_path so the entry never flips to "live",
    # yet the video *is* now on TikTok Studio's real content list --
    # permanently shifting update_tiktok_stats.py's newest-first order
    # pairing by one for every video posted after it, silently misattributing
    # stats (the exact class of bug fixed for regular posts on 2026-09-15,
    # reopened here because this path skipped that fix).
    compilation_task_id = f"compilation_{video_id}"
    with locked_post_log() as post_log_entries:
        post_log_entries.append({
            "task_id": compilation_task_id,
            "subject": meta["title"],
            "title": meta["title"],
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "category": top_category,
            "video_path": output_path,
            "description": description,
            "seo_tags": seo_tags,
            "platforms": {
                "youtube": {"id": video_id, "status": "live"},
                "tiktok": {"status": "pending_manual"},
            },
        })

    with open(MANUAL_REMINDER_FILE, "a") as f:
        f.write(f"\n--- Weekly compilation {datetime.now(timezone.utc).isoformat()} ---\n")
        f.write(
            f"=== {meta['title']} ===\nFile: {output_path}\nCaption:\n{description}\n"
            f"Suggested time: {datetime.now(timezone.utc).isoformat()} (UTC) -> post manually on TikTok\n\n"
        )
    print(f"Queued for TikTok: {output_path}")

    print(f"Done: {len(clips)} clips bundled into {video_id}.")


if __name__ == "__main__":
    main()
