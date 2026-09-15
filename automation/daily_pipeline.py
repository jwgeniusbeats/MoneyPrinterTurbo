"""
Fully autonomous daily content pipeline for Nognize.

Each run:
1. Pops the next N topics off automation/topic_backlog.json
2. Generates videos for them via the existing CLI batch pipeline
3. For each finished video, generates social metadata (title/caption/hashtags)
4. Uploads + natively schedules on YouTube (direct API) and Facebook (direct API)
   at the next two 13:00/20:00 CET slots, advancing automation/schedule_state.json
5. Writes an Instagram/TikTok manual-post reminder file (no free auto-post API
   for those two platforms yet)

Run manually with: uv run python automation/daily_pipeline.py
Intended to be invoked daily by a launchd job (see automation/setup_launchd.sh).
"""
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app.services import instagram_api, llm, meta_api, youtube_api  # noqa: E402
from app.utils import utils  # noqa: E402

BACKLOG_FILE = os.path.join(BASE_DIR, "automation", "topic_backlog.json")
STATE_FILE = os.path.join(BASE_DIR, "automation", "schedule_state.json")
MANUAL_REMINDER_FILE = os.path.join(BASE_DIR, "automation", "manual_post_queue.txt")
POST_LOG_FILE = os.path.join(BASE_DIR, "automation", "post_log.json")
LEARNINGS_FILE = os.path.join(BASE_DIR, "automation", "learnings.md")
VIDEOS_PER_RUN = 4
# Link-in-bio page (all platform links + email capture) -- every post should
# drive traffic there, not just the bio itself, since that's the one owned
# asset that doesn't depend on any single platform's algorithm or payout
# threshold.
LINK_IN_BIO_URL = "tinyurl.com/238zsuaa"
LINK_IN_BIO_CTA = f"\n\n\U0001f517 More facts + early access: {LINK_IN_BIO_URL}"
CET_OFFSET_HOURS = 2  # CEST (summer time); adjust to 1 in winter


CATEGORY_KEYWORDS = {
    "memory": ["memory", "remember", "forget", "deja vu", "déjà vu", "mandela effect"],
    "fear_safety": ["danger", "fear", "emergency", "bystander", "survival", "threat", "panic"],
    "social_relationships": [
        "impression", "judge", "trust", "friend", "relationship", "attract", "social",
    ],
    "bias_decisionmaking": ["bias", "trick", "manipulat", "decision", "procrastinat", "buy"],
    "emotion_music": ["sad song", "music", "emotion", "feel better", "mood"],
    "perception_illusion": ["illusion", "lying", "lie", "perceive", "perception"],
}


def build_seo_tags(subject: str, title: str, category: str, hashtags: list) -> list:
    """YouTube tags work better as natural search phrases than as reused
    hashtags (a bare '#dejavu' is a worse search tag than 'deja vu
    psychology') -- this builds a richer, de-duped tag list instead of just
    passing the caption hashtags through as-is."""
    tags = [h.lstrip("#") for h in hashtags if h.lstrip("#")]
    tags.append(subject[:100])
    tags.append(title[:100])
    if category and category != "general":
        tags.append(category.replace("_", " "))
    tags.extend(["psychology facts", "mind facts", "psychology", "shorts"])

    seen = set()
    deduped = []
    for t in tags:
        key = t.lower().strip()
        if key and key not in seen:
            seen.add(key)
            deduped.append(t.strip())
    return deduped[:15]


def classify_video(subject: str, title: str) -> tuple[str, str]:
    """Rule-based (no LLM call) category + hook-type tagging, used to spot
    which angles perform well once enough data exists in learnings.md."""
    text = f"{subject} {title}".lower()
    category = "general"
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            category = cat
            break

    title_lower = title.lower().strip()
    if title_lower.startswith("why"):
        hook_type = "why_hook"
    elif title_lower.startswith("how"):
        hook_type = "how_hook"
    elif title_lower[:1].isdigit():
        hook_type = "listicle_hook"
    elif "?" in title:
        hook_type = "question_hook"
    elif any(w in title_lower for w in ["secret", "reason", "no one", "nobody"]):
        hook_type = "curiosity_hook"
    else:
        hook_type = "statement_hook"

    return category, hook_type


def load_learnings() -> str:
    """Past-performance guidance from automation/analyze_performance.py, fed
    into script generation so new videos build on what's proven to work."""
    if not os.path.exists(LEARNINGS_FILE):
        return ""
    with open(LEARNINGS_FILE) as f:
        return f.read().strip()


def load_post_log() -> list:
    if not os.path.exists(POST_LOG_FILE):
        return []
    with open(POST_LOG_FILE) as f:
        return json.load(f)


def save_post_log(entries: list):
    with open(POST_LOG_FILE, "w") as f:
        json.dump(entries, f, indent=2)


def load_backlog() -> list:
    with open(BACKLOG_FILE) as f:
        return json.load(f)


def save_backlog(remaining: list):
    with open(BACKLOG_FILE, "w") as f:
        json.dump(remaining, f, indent=2)


def load_state() -> dict:
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def extract_and_set_thumbnail(youtube_video_id: str, video_path: str):
    """Grab a frame from the voiced title-card (~1.2s in) as a custom YouTube
    thumbnail — the auto-picked frame YouTube defaults to is a much weaker
    click-through lever than the title-card text."""
    thumb_path = os.path.join(tempfile.gettempdir(), f"thumb_{youtube_video_id}.jpg")
    result = subprocess.run(
        ["ffmpeg", "-y", "-ss", "1.2", "-i", video_path, "-frames:v", "1", "-q:v", "2", thumb_path],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0 or not os.path.exists(thumb_path):
        raise RuntimeError(f"ffmpeg thumbnail extraction failed: {result.stderr[-500:]}")
    try:
        youtube_api.set_thumbnail(youtube_video_id, thumb_path)
    finally:
        if os.path.exists(thumb_path):
            os.remove(thumb_path)


def next_slot_utc(state: dict) -> datetime:
    """Advance to the next 13:00/20:00 CET slot after the last scheduled one."""
    last = datetime.fromisoformat(state["last_scheduled_utc"].replace("Z", "+00:00"))
    slot_hours = sorted(state["slot_hours_cet"])
    cet = last + timedelta(hours=CET_OFFSET_HOURS)
    for h in slot_hours:
        if h > cet.hour or (h == cet.hour and cet.minute > 0 and False):
            candidate_cet = cet.replace(hour=h, minute=0, second=0, microsecond=0)
            return candidate_cet - timedelta(hours=CET_OFFSET_HOURS)
    candidate_cet = (cet + timedelta(days=1)).replace(
        hour=slot_hours[0], minute=0, second=0, microsecond=0
    )
    return candidate_cet - timedelta(hours=CET_OFFSET_HOURS)


def run_batch(topics: list) -> list:
    learnings = load_learnings()
    batch_path = os.path.join(BASE_DIR, "automation", "_run_batch.jsonl")
    with open(batch_path, "w") as f:
        for topic in topics:
            # SCRIPT_STYLE_SUFFIX is a script-generation instruction, not part
            # of the topic's identity -- append it only for this CLI call, so
            # `topic` itself (used later as title fallback / SEO tag / logged
            # subject) stays clean human-readable text.
            entry = {"video_subject": topic + SCRIPT_STYLE_SUFFIX, "video_language": "en-US"}
            if learnings:
                entry["video_script_prompt"] = (
                    "Here is what has performed well vs. poorly on this channel so far "
                    "(real view/like data). Favor similar hooks/angles, avoid repeating "
                    f"weak ones:\n\n{learnings}"
                )
            f.write(json.dumps(entry) + "\n")

    result = subprocess.run(
        ["uv", "run", "python", "cli.py", "--batch-file", batch_path],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=3600,
    )
    print(result.stdout[-3000:])
    if result.returncode != 0:
        print("BATCH STDERR:", result.stderr[-2000:], file=sys.stderr)

    summary_line = None
    for line in reversed(result.stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and '"succeeded"' in line:
            summary_line = line
            break
    if not summary_line:
        print("Could not find batch summary JSON in output.", file=sys.stderr)
        return []

    summary = json.loads(summary_line)
    succeeded = []
    for task in summary.get("tasks", []):
        if task.get("status") == "succeeded":
            r = task["result"]
            succeeded.append(
                {
                    "task_id": r.get("task_id") or task.get("task_id"),
                    "video_subject": topics[task["index"] - 1],
                    "script": r["script"],
                    "final_video": r["videos"][0],
                    "combined_video": r["combined_videos"][0] if r.get("combined_videos") else None,
                }
            )
    return succeeded


BACKLOG_REFILL_THRESHOLD = 8  # ~2 days of runway at VIDEOS_PER_RUN=4/day
BACKLOG_REFILL_COUNT = 10
SCRIPT_STYLE_SUFFIX = (
    " Keep the script short and punchy, about 80-100 words total, "
    "fast hook in the first sentence."
)


TREND_SEED_PHRASES = [
    "why do people", "why do i", "the psychology of", "why does my brain",
    "how to stop", "why we", "the reason you", "why your brain",
]


def fetch_trending_seeds(max_seeds: int = 4, max_per_seed: int = 5) -> list[str]:
    """Pull real YouTube search autocomplete suggestions for a rotating set of
    psychology-niche seed phrases (no API key, public endpoint) — biases new
    topics toward what people are actually searching for, not just novel
    facts an LLM invents blind. Best-effort: returns [] on any network issue,
    generate_new_topics() works fine without it, just less informed."""
    import random
    import urllib.parse
    import urllib.request

    seeds = random.sample(TREND_SEED_PHRASES, min(max_seeds, len(TREND_SEED_PHRASES)))
    suggestions = []
    for seed in seeds:
        try:
            url = (
                "http://suggestqueries.google.com/complete/search?client=firefox&ds=yt&q="
                + urllib.parse.quote(seed)
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())
            suggestions.extend(data[1][:max_per_seed])
        except Exception as e:
            print(f"trend fetch failed for {seed!r} (non-fatal): {e}")
    return suggestions


def generate_new_topics(n: int, learnings: str, existing: list) -> list:
    """Auto-refill the topic backlog with new psychology/mind-facts angles
    once it's running low, so the channel never silently starves for topics.
    Informed by learnings.md when available, to lean into what performs."""
    guidance = (
        f"\n\nHere is what has performed well on this channel so far, lean into similar angles:\n{learnings}"
        if learnings else ""
    )
    trends = fetch_trending_seeds()
    trend_guidance = (
        "\n\nReal things people are currently searching for on YouTube related to this "
        "niche (bias your topics toward these when a good psychology angle fits, since "
        "they have proven search demand, not just novelty):\n"
        + "\n".join(f"- {t}" for t in trends)
        if trends else ""
    )
    prompt = (
        "Brainstorm {n} new video topic ideas for a faceless psychology/mind-facts "
        "short-form video channel (TikTok/YouTube Shorts/Instagram Reels). "
        "Each topic should be a single punchy sentence describing a specific, "
        "surprising psychological phenomenon or mind fact — the kind that makes "
        "someone stop scrolling. Avoid these already-used topics:\n"
        + "\n".join(f"- {t}" for t in existing[-30:])
        + guidance
        + trend_guidance
        + f"\n\nReply with exactly {n} lines, one topic per line, no numbering, no extra text."
    ).format(n=n)

    from app.services import llm as _llm

    response = _llm._generate_response(prompt=prompt)
    lines = [ln.strip("-* \t") for ln in response.strip().splitlines() if ln.strip()]
    # Stored as plain topics -- SCRIPT_STYLE_SUFFIX is appended only at the
    # point of generation (run_batch), not baked into the topic's identity,
    # so titles/SEO tags/logged subjects built from these later stay clean.
    return lines[:n]


RETRYABLE_STATUSES = {"failed", "blocked_meta"}


def retry_failed_platforms(post_log: list):
    """Before generating new videos, retry any platform upload that failed or
    was blocked on a previous run (e.g. the Meta/Google account suspensions
    of 2026-09-13/14) — catches the pipeline back up automatically instead of
    leaving posts stuck until someone notices and retries by hand."""
    any_retried = False
    for entry in post_log:
        video_path = entry.get("video_path")
        if not video_path or not os.path.exists(video_path):
            continue
        title = entry.get("title", entry.get("subject", ""))
        description = entry.get("description", "")
        platforms = entry.get("platforms", {})

        yt = platforms.get("youtube", {})
        if yt.get("status") in RETRYABLE_STATUSES:
            try:
                yt_result = youtube_api.upload_video(
                    video_path=video_path, title=title, description=description,
                    privacy_status="public",
                )
                platforms["youtube"] = {"id": yt_result.get("id"), "status": "live"}
                print(f"Retry OK: YouTube for {title!r}")
                any_retried = True
                try:
                    extract_and_set_thumbnail(yt_result["id"], video_path)
                except Exception as thumb_e:
                    print(f"Thumbnail set failed (non-fatal) for {title!r}: {thumb_e}")
            except Exception as e:
                print(f"Retry still failing: YouTube for {title!r}: {e}")

        fb = platforms.get("facebook", {})
        if fb.get("status") in RETRYABLE_STATUSES:
            try:
                fb_result = meta_api.upload_facebook_video(
                    video_path=video_path, title=title, description=description,
                )
                platforms["facebook"] = {"id": fb_result.get("id"), "status": "live"}
                print(f"Retry OK: Facebook for {title!r}")
                any_retried = True
            except Exception as e:
                print(f"Retry still failing: Facebook for {title!r}: {e}")

        ig = platforms.get("instagram", {})
        if ig.get("status") in RETRYABLE_STATUSES:
            try:
                ig_result = instagram_api.upload_reel_via_url(video_path=video_path, caption=description)
                platforms["instagram"] = {"id": ig_result.get("id"), "status": "live"}
                print(f"Retry OK: Instagram for {title!r}")
                any_retried = True
            except Exception as e:
                print(f"Retry still failing: Instagram for {title!r}: {e}")

    if any_retried:
        save_post_log(post_log)


def main():
    post_log = load_post_log()
    retry_failed_platforms(post_log)

    backlog = load_backlog()
    if not backlog:
        print("Topic backlog empty, nothing to do.")
        return
    topics = backlog[:VIDEOS_PER_RUN]
    remaining = backlog[VIDEOS_PER_RUN:]

    print(f"Generating {len(topics)} videos...")
    finished = run_batch(topics)
    print(f"{len(finished)}/{len(topics)} videos finished successfully.")

    used_topics = {t["video_subject"] for t in finished}
    remaining_backlog = [t for t in backlog if t not in used_topics] or remaining

    if len(remaining_backlog) < BACKLOG_REFILL_THRESHOLD:
        try:
            new_topics = generate_new_topics(
                BACKLOG_REFILL_COUNT, load_learnings(), backlog
            )
            if new_topics:
                remaining_backlog = remaining_backlog + new_topics
                print(f"Backlog low ({len(remaining_backlog) - len(new_topics)} left), "
                      f"auto-refilled with {len(new_topics)} new topics.")
        except Exception as e:
            print(f"Backlog auto-refill failed (non-fatal): {e}")

    save_backlog(remaining_backlog)

    state = load_state()
    manual_lines = []

    for item in finished:
        subject = item["video_subject"]
        script = item["script"]
        video_path = item["final_video"]
        log_entry = {
            "task_id": item["task_id"],
            "subject": subject,
            "posted_at": datetime.now(timezone.utc).isoformat(),
            "platforms": {},
        }

        try:
            meta = llm.generate_social_metadata(
                video_subject=subject, video_script=script, language="en-US", platform="instagram"
            )
        except Exception as e:
            print(f"metadata generation failed for {subject}: {e}")
            meta = {"title": subject[:100], "caption": script, "hashtags": ["#shorts", "#psychology"]}

        title = meta.get("title") or subject[:100]
        log_entry["title"] = title
        category, hook_type = classify_video(subject, title)
        log_entry["category"] = category
        log_entry["hook_type"] = hook_type
        caption = meta.get("caption") or script
        hashtags_list = meta.get("hashtags") or []
        hashtags = " ".join(hashtags_list)
        description = f"{caption}{LINK_IN_BIO_CTA}\n\n{hashtags}".strip()
        seo_tags = build_seo_tags(subject, title, category, hashtags_list)

        labeled_paths = utils.save_labeled_videos(item["task_id"], subject, [video_path])
        labeled_path = labeled_paths[0] if labeled_paths else video_path
        log_entry["video_path"] = labeled_path
        log_entry["description"] = description

        slot_dt = next_slot_utc(state)
        state["last_scheduled_utc"] = slot_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            yt_result = youtube_api.upload_video(
                video_path=video_path,
                title=title,
                description=description,
                tags=seo_tags,
                publish_at=state["last_scheduled_utc"],
            )
            print(f"YouTube scheduled: {yt_result.get('id')} at {state['last_scheduled_utc']}")
            log_entry["platforms"]["youtube"] = {
                "id": yt_result.get("id"),
                "status": "scheduled",
                "scheduled_for": state["last_scheduled_utc"],
            }
            try:
                extract_and_set_thumbnail(yt_result["id"], video_path)
                print(f"Thumbnail set for {yt_result['id']}")
            except Exception as e:
                print(f"Thumbnail set failed (non-fatal) for {subject}: {e}")
        except Exception as e:
            print(f"YouTube upload failed for {subject}: {e}")
            log_entry["platforms"]["youtube"] = {"status": "failed", "error": str(e)}

        try:
            fb_result = meta_api.upload_facebook_video(
                video_path=video_path,
                title=title,
                description=description,
                scheduled_publish_time=int(slot_dt.timestamp()),
            )
            print(f"Facebook scheduled: {fb_result.get('id')} at {state['last_scheduled_utc']}")
            log_entry["platforms"]["facebook"] = {
                "id": fb_result.get("id"),
                "status": "scheduled",
                "scheduled_for": state["last_scheduled_utc"],
            }
        except Exception as e:
            print(f"Facebook upload failed for {subject}: {e}")
            log_entry["platforms"]["facebook"] = {"status": "failed", "error": str(e)}

        save_state(state)

        try:
            # Instagram's API has no native "schedule for later" (unlike
            # YouTube/Facebook), so it publishes immediately at generation
            # time rather than waiting for the 13:00/20:00 slot.
            ig_result = instagram_api.upload_reel_via_url(video_path=video_path, caption=description)
            print(f"Instagram published: {ig_result.get('id')}")
            log_entry["platforms"]["instagram"] = {"id": ig_result.get("id"), "status": "live"}
        except Exception as e:
            print(f"Instagram upload failed for {subject}: {e}")
            log_entry["platforms"]["instagram"] = {"status": "failed", "error": str(e)}

        log_entry["platforms"]["tiktok"] = {"status": "pending_manual"}
        post_log.append(log_entry)
        save_post_log(post_log)

        manual_lines.append(
            f"=== {title} ===\nFile: {labeled_path}\nCaption:\n{description}\n"
            f"Suggested time: {slot_dt.isoformat()} (UTC) -> post manually on TikTok\n\n"
        )

    if manual_lines:
        with open(MANUAL_REMINDER_FILE, "a") as f:
            f.write(f"\n--- Batch run {datetime.now(timezone.utc).isoformat()} ---\n")
            f.writelines(manual_lines)
        print(f"Manual TikTok reminders appended to {MANUAL_REMINDER_FILE}")


if __name__ == "__main__":
    main()
