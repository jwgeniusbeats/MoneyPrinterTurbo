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
    _atomic_write_json,
    build_seo_tags,
    extract_and_set_thumbnail,
)

POST_LOG_FILE = os.path.join(BASE_DIR, "automation", "post_log.json")
COMPILATION_LOG_FILE = os.path.join(BASE_DIR, "automation", "compilation_log.json")
MIN_CLIPS = 3
MAX_CLIPS = 8


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


def generate_compilation_metadata(clip_titles: list) -> dict:
    n = len(clip_titles)
    prompt = (
        f"Write a YouTube title and description for a compilation video bundling "
        f"{n} short psychology/mind-facts videos back to back. The individual "
        f"clip titles, in order, are:\n"
        + "\n".join(f"{i+1}. {t}" for i, t in enumerate(clip_titles))
        + "\n\nWrite:\n"
        "TITLE: a punchy compilation title under 100 chars (e.g. \"7 Psychology "
        "Facts That Will Blow Your Mind\")\n"
        "DESCRIPTION: 2-3 sentences summarizing the compilation, then a numbered "
        "list of the clip titles as timestamps placeholder text, then relevant hashtags.\n"
        "Reply in exactly this format, nothing else:\nTITLE: ...\nDESCRIPTION: ..."
    )
    try:
        response = llm._generate_response(prompt=prompt)
        title, description = "", ""
        if "TITLE:" in response and "DESCRIPTION:" in response:
            title = response.split("TITLE:")[1].split("DESCRIPTION:")[0].strip()
            description = response.split("DESCRIPTION:")[1].strip()
        if title and description:
            return {"title": title[:100], "description": description[:5000]}
    except Exception as e:
        print(f"compilation metadata generation failed, using fallback: {e}")
    return {
        "title": f"{n} Psychology Facts That Will Blow Your Mind",
        "description": "This week's psychology facts, back to back:\n"
        + "\n".join(f"{i+1}. {t}" for i, t in enumerate(clip_titles))
        + "\n\n#psychology #mindfacts #shorts",
    }


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

    output_path = os.path.join(tempfile.gettempdir(), f"compilation_{datetime.now():%Y%m%d}.mp4")
    print(f"Concatenating {len(clips)} clips...")
    concat_videos(video_paths, output_path)

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

    try:
        extract_and_set_thumbnail(video_id, video_paths[0])
    except Exception as e:
        print(f"Thumbnail set failed (non-fatal): {e}")

    compilation_log.append({
        "video_id": video_id,
        "title": meta["title"],
        "task_ids": [c["task_id"] for c in clips],
        "clip_count": len(clips),
        "posted_at": datetime.now(timezone.utc).isoformat(),
    })
    save_compilation_log(compilation_log)

    if os.path.exists(output_path):
        os.remove(output_path)

    print(f"Done: {len(clips)} clips bundled into {video_id}.")


if __name__ == "__main__":
    main()
