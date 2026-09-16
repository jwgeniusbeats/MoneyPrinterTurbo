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
from zoneinfo import ZoneInfo

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app.services import instagram_api, llm, meta_api, youtube_api  # noqa: E402
from app.utils import utils  # noqa: E402
from automation.post_log_lock import locked_post_log  # noqa: E402

BACKLOG_FILE = os.path.join(BASE_DIR, "automation", "topic_backlog.json")
STATE_FILE = os.path.join(BASE_DIR, "automation", "schedule_state.json")
MANUAL_REMINDER_FILE = os.path.join(BASE_DIR, "automation", "manual_post_queue.txt")
POST_LOG_FILE = os.path.join(BASE_DIR, "automation", "post_log.json")
LEARNINGS_FILE = os.path.join(BASE_DIR, "automation", "learnings.md")
VIDEOS_PER_RUN = 4
# Must match app.models.schema.VideoParams.video_script_prompt's
# Field(max_length=...) exactly -- see run_batch() for why going over this
# is a same-day full-batch outage, not a per-entry warning.
MAX_VIDEO_SCRIPT_PROMPT_LENGTH = 2000
# Link-in-bio page (all platform links + email capture) -- every post should
# drive traffic there, not just the bio itself, since that's the one owned
# asset that doesn't depend on any single platform's algorithm or payout
# threshold.
LINK_IN_BIO_URL = "tinyurl.com/238zsuaa"
LINK_IN_BIO_CTA = f"\n\n\U0001f517 More facts + early access: {LINK_IN_BIO_URL}"
CET_ZONE = ZoneInfo("Europe/Amsterdam")  # DST-aware CET/CEST, no manual offset to maintain


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


def build_seo_tags(
    subject: str, title: str, category: str, hashtags: list, is_short: bool = True
) -> list:
    """YouTube tags work better as natural search phrases than as reused
    hashtags (a bare '#dejavu' is a worse search tag than 'deja vu
    psychology') -- this builds a richer, de-duped tag list instead of just
    passing the caption hashtags through as-is.

    is_short=False for weekly_compilation.py's long-form bundles: they're
    5-7 minutes and the entire point of making them is to earn long-form
    watch-hours toward Partner Program (see that file's docstring) -- a
    'shorts' tag baked in here would mislabel that video's format to
    YouTube's own systems, working against the reason the compilation
    exists at all."""
    tags = [h.lstrip("#") for h in hashtags if h.lstrip("#")]
    tags.append(subject[:100])
    tags.append(title[:100])
    if category and category != "general":
        tags.append(category.replace("_", " "))
    tags.extend(["psychology facts", "mind facts", "psychology"])
    if is_short:
        tags.append("shorts")

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


def load_backlog() -> list:
    with open(BACKLOG_FILE) as f:
        return json.load(f)


def _atomic_write_json(path: str, data):
    """Write via tmp file + os.replace so a crash mid-write (launchd
    timeout, OOM, machine sleep) never leaves a truncated/corrupt file --
    plain open(path, 'w') can, and the next run would then crash on
    json.load() with no recovery path."""
    tmp_path = path + ".tmp"
    with open(tmp_path, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp_path, path)


def save_backlog(remaining: list):
    _atomic_write_json(BACKLOG_FILE, remaining)


def load_state() -> dict:
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state: dict):
    _atomic_write_json(STATE_FILE, state)


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
    """Advance to the next configured CET slot after the last scheduled one.

    Anchored at max(now, last_scheduled) rather than always last_scheduled --
    the old always-last_scheduled anchor never self-corrected, so any drift
    (a failed upload that still reserved a slot, a run that fell behind)
    compounded forever. Anchoring at "now" whenever the schedule has fallen
    behind (or drifted ahead, e.g. from the account-block failures on
    2026-09-13/14) makes it catch back up to real time instead."""
    last = datetime.fromisoformat(state["last_scheduled_utc"].replace("Z", "+00:00"))
    now = datetime.now(timezone.utc)
    anchor = max(last, now)
    slot_hours = sorted(state["slot_hours_cet"])
    cet = anchor.astimezone(CET_ZONE)
    for h in slot_hours:
        if h > cet.hour:
            candidate_cet = cet.replace(hour=h, minute=0, second=0, microsecond=0)
            return candidate_cet.astimezone(timezone.utc)
    candidate_cet = (cet + timedelta(days=1)).replace(
        hour=slot_hours[0], minute=0, second=0, microsecond=0
    )
    return candidate_cet.astimezone(timezone.utc)


def run_batch(topics: list) -> list:
    import random

    learnings = load_learnings()
    batch_path = os.path.join(BASE_DIR, "automation", "_run_batch.jsonl")
    subtitle_modes = []  # parallel to `topics`, so succeeded[] can log which mode each video got
    with open(batch_path, "w") as f:
        for topic in topics:
            # SCRIPT_STYLE_SUFFIX is a script-generation instruction, not part
            # of the topic's identity -- it goes in video_script_prompt, not
            # video_subject. video_subject is reused downstream (task.py) as
            # the title-card and post-title fallback whenever the LLM social-
            # metadata call fails, so appending instruction text to it used to
            # leak straight onto the visible video ("...STOP. KEEP THE SCRIPT
            # SHORT AND" was literally rendered on-screen on a real upload).
            # A/B test: 50/50 per video between the default sentence-by-
            # sentence subtitles and word-by-word with spring animation
            # (new in v1.3.7). Uncertain which helps THIS channel (dense
            # factual content vs. word-by-word's fast/hype-coded pacing),
            # so split evenly and let analyze_performance.py's real
            # retention data settle it instead of guessing -- see
            # subtitle_display_mode logged on log_entry below.
            subtitle_display_mode = random.choice(["sentence", "word_by_word"])
            subtitle_modes.append(subtitle_display_mode)
            entry = {
                "video_subject": topic,
                "video_language": "en-US",
                "subtitle_display_mode": subtitle_display_mode,
                "subtitle_animation": (
                    "pop_spring" if subtitle_display_mode == "word_by_word" else "none"
                ),
            }
            script_prompt_parts = [FOLLOW_CTA_INSTRUCTION, SCRIPT_STYLE_SUFFIX.strip()]

            # A different random sample per topic (not the whole fixed list
            # every time) so a day's 4 videos don't all lean on the same one
            # or two hook shapes -- see HOOK_PATTERNS above for provenance.
            sampled_patterns = random.sample(
                list(HOOK_PATTERNS.items()), k=min(3, len(HOOK_PATTERNS))
            )
            pattern_lines = "\n".join(f"- {name}: {desc}" for name, desc in sampled_patterns)
            script_prompt_parts.append(
                "Consider opening with one of these proven hook structures if it "
                f"fits this topic naturally (don't force it):\n{pattern_lines}"
            )

            # VideoParams.video_script_prompt has a hard pydantic
            # max_length=2000 -- this isn't a soft truncation applied later,
            # it's a validation error that rejects cli.py's ENTIRE batch
            # file (not just the offending entry) with "invalid CLI batch
            # input", silently producing 0 videos for the whole day. Found
            # 2026-09-16: FOLLOW_CTA + SCRIPT_STYLE_SUFFIX + hook patterns
            # (~875 chars, fixed) plus learnings.md (grows over time as
            # analyze_performance.py accumulates more data -- already at
            # ~2000 chars on its own) blew past 2000 combined and took down
            # that day's entire run. Learnings is the part that grows
            # unboundedly, so it's the one truncated to fit, never the
            # fixed instruction parts above.
            fixed_prompt = "\n\n".join(script_prompt_parts)
            if learnings:
                learnings_header = (
                    "Here is what has performed well vs. poorly on this channel so far "
                    "(real view/like data). Favor similar hooks/angles, avoid repeating "
                    "weak ones:\n\n"
                )
                budget = MAX_VIDEO_SCRIPT_PROMPT_LENGTH - len(fixed_prompt) - len("\n\n") - len(learnings_header)
                if budget > 100:  # not worth appending a learnings scrap smaller than this
                    script_prompt_parts.append(learnings_header + learnings[:budget])

            entry["video_script_prompt"] = "\n\n".join(script_prompt_parts)
            assert len(entry["video_script_prompt"]) <= MAX_VIDEO_SCRIPT_PROMPT_LENGTH, (
                f"video_script_prompt still over budget: {len(entry['video_script_prompt'])} chars"
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
                    "topic_index": task["index"] - 1,
                    "video_subject": topics[task["index"] - 1],
                    "script": r["script"],
                    "final_video": r["videos"][0],
                    "combined_video": r["combined_videos"][0] if r.get("combined_videos") else None,
                    "subtitle_display_mode": subtitle_modes[task["index"] - 1],
                }
            )
    return succeeded


BACKLOG_REFILL_THRESHOLD = 8  # ~2 days of runway at VIDEOS_PER_RUN=4/day
BACKLOG_REFILL_COUNT = 10
SCRIPT_STYLE_SUFFIX = (
    " Keep the script short and punchy, about 80-100 words total, "
    "fast hook in the first sentence."
)
# A brand-new channel gets very little algorithmic push on YouTube/Instagram
# (unlike TikTok's cold-start-friendly For You feed, which tests fresh
# uploads regardless of follower count) -- an explicit spoken follow/subscribe
# ask in the last line measurably improves follow-through, and no script
# generated so far has included one at all.
FOLLOW_CTA_INSTRUCTION = (
    "End the script with a brief, natural spoken call-to-action to follow the "
    "account for more psychology facts (e.g. \"Follow for more mind-bending "
    "psychology facts\" or similar, in your own words matching the script's "
    "tone) -- don't make it feel like an ad, keep it under 10 words, folded "
    "naturally into the last sentence rather than tacked on."
)


# Curated from resources/tiktok-viral-hooks (shixinzhang/tiktok-viral-hooks,
# MIT-licensed code / CC BY-NC-SA content) -- a hand-picked subset of its
# 448 structural hook patterns that fit psychology/facts content specifically
# (the full library skews toward beauty/finance/product-review niches). Only
# the pattern MECHANIC is described here in our own words, never the
# library's actual example transcripts, since those are the NC-licensed part.
HOOK_PATTERNS = {
    "Myth-Busting": "State a widely-believed claim, then immediately reveal it's wrong.",
    "Curiosity Gap via Surprising Fact": "Open with a specific, odd fact and withhold the explanation until later in the script.",
    "Counterintuitive Reframe": "Name the common/expected explanation for something, then flip it to the real, less obvious cause.",
    "Assumption vs. Reality": "State what people assume is happening, then contrast it with what's actually happening.",
    "Rhetorical Question with Unexpected Twist": "Ask a question the viewer thinks they know the answer to, then answer it in a way that subverts that.",
    "Psychological Label Reveal": "Describe a specific relatable behavior first, then name the psychological effect/bias behind it.",
    "Direct Question Hook": "Open with a direct, specific question aimed at the viewer's own experience (\"Have you ever...\").",
    "Bold Claim": "Open with a confident, slightly provocative claim that invites a 'wait, really?' reaction.",
    "Contradiction Hook": "Open with two things that seem to contradict each other, then resolve the tension.",
}


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
    # task_id -> {platform_key: new_platform_dict}, only for platforms this
    # call actually retried -- NOT a snapshot of every entry's full platforms
    # dict. A full-dict snapshot merged back later would silently overwrite
    # any other platform key (e.g. tiktok) that the independently-scheduled
    # tiktok-daily-post / mark_tiktok_posted.py flipped to "live" while these
    # (slow, network-bound) retries were still running.
    retried_updates = {}
    for entry in post_log:
        video_path = entry.get("video_path")
        if not video_path or not os.path.exists(video_path):
            continue
        title = entry.get("title", entry.get("subject", ""))
        description = entry.get("description", "")
        platforms = entry.get("platforms", {})
        task_id = entry.get("task_id")

        yt = platforms.get("youtube", {})
        if yt.get("status") in RETRYABLE_STATUSES:
            try:
                yt_result = youtube_api.upload_video(
                    video_path=video_path, title=title, description=description,
                    privacy_status="public",
                )
                new_yt = {"id": yt_result.get("id"), "status": "live"}
                platforms["youtube"] = new_yt
                print(f"Retry OK: YouTube for {title!r}")
                if task_id:
                    retried_updates.setdefault(task_id, {})["youtube"] = new_yt
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
                new_fb = {"id": fb_result.get("id"), "status": "live"}
                platforms["facebook"] = new_fb
                print(f"Retry OK: Facebook for {title!r}")
                if task_id:
                    retried_updates.setdefault(task_id, {})["facebook"] = new_fb
            except Exception as e:
                print(f"Retry still failing: Facebook for {title!r}: {e}")

        ig = platforms.get("instagram", {})
        if ig.get("status") in RETRYABLE_STATUSES:
            try:
                ig_result = instagram_api.upload_reel_via_url(video_path=video_path, caption=description)
                new_ig = {"id": ig_result.get("id"), "status": "live"}
                platforms["instagram"] = new_ig
                print(f"Retry OK: Instagram for {title!r}")
                if task_id:
                    retried_updates.setdefault(task_id, {})["instagram"] = new_ig
            except Exception as e:
                print(f"Retry still failing: Instagram for {title!r}: {e}")

    if retried_updates:
        # Merge into a freshly-read copy under lock instead of overwriting
        # with this possibly-stale in-memory list -- retrying involves slow
        # network calls, during which the concurrent tiktok-daily-post task
        # (running on its own fixed schedule, independent of this run) could
        # have appended or updated other entries via mark_tiktok_posted.py.
        # Only the specific platform keys that were retried are applied, so
        # any other platform key (tiktok's status/stats) written concurrently
        # onto fresh_log during this run is preserved untouched.
        with locked_post_log() as fresh_log:
            for entry in fresh_log:
                updates = retried_updates.get(entry.get("task_id"))
                if updates:
                    entry.setdefault("platforms", {}).update(updates)


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

    # Remove finished topics by position within `topics`, not by string value:
    # two identical topic strings in the backlog (plausible -- LLM-brainstormed
    # refills are only checked against the last 30 existing topics, not the
    # full history) would otherwise both get silently removed by a set-based
    # match even when only one was actually turned into a video.
    finished_indices = {t["topic_index"] for t in finished}
    unfinished_this_run = [t for i, t in enumerate(topics) if i not in finished_indices]
    remaining_backlog = unfinished_this_run + remaining

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
            # A/B test tag (see run_batch()) -- lets analyze_performance.py
            # eventually compare retention between the two subtitle styles
            # once enough videos of each have real data.
            "subtitle_display_mode": item.get("subtitle_display_mode"),
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

        # Compute the candidate slot but DON'T commit it to state yet -- only
        # persist it once the upload that actually uses it (YouTube) succeeds.
        # Previously this was written to state unconditionally before the
        # upload attempt, so a failed upload still permanently burned a slot
        # nothing ever published into -- the main cause, compounded daily,
        # of the schedule drifting further and further ahead of real time.
        slot_dt = next_slot_utc(state)
        candidate_scheduled_utc = slot_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        try:
            yt_result = youtube_api.upload_video(
                video_path=video_path,
                title=title,
                description=description,
                tags=seo_tags,
                publish_at=candidate_scheduled_utc,
            )
            state["last_scheduled_utc"] = candidate_scheduled_utc
            print(f"YouTube scheduled: {yt_result.get('id')} at {candidate_scheduled_utc}")
            log_entry["platforms"]["youtube"] = {
                "id": yt_result.get("id"),
                "status": "scheduled",
                "scheduled_for": candidate_scheduled_utc,
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
            state["last_scheduled_utc"] = candidate_scheduled_utc
            print(f"Facebook scheduled: {fb_result.get('id')} at {candidate_scheduled_utc}")
            log_entry["platforms"]["facebook"] = {
                "id": fb_result.get("id"),
                "status": "scheduled",
                "scheduled_for": candidate_scheduled_utc,
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
        # Append under lock against a freshly-read copy, not the in-memory
        # `post_log` loaded once at the top of main() -- a full run can span
        # 30-90+ minutes across several videos' platform uploads, plenty of
        # time for a concurrent tiktok-daily-post run to have appended or
        # updated other entries in the meantime.
        with locked_post_log() as fresh_log:
            fresh_log.append(log_entry)

        manual_lines.append(
            f"=== {title} ===\nFile: {labeled_path}\nCaption:\n{description}\n"
            f"Suggested time: {slot_dt.isoformat()} (UTC) -> post manually on TikTok\n\n"
        )

    if manual_lines:
        with open(MANUAL_REMINDER_FILE, "a") as f:
            f.write(f"\n--- Batch run {datetime.now(timezone.utc).isoformat()} ---\n")
            f.writelines(manual_lines)
        print(f"Manual TikTok reminders appended to {MANUAL_REMINDER_FILE}")

    try:
        # storage/tasks/ working folders (raw combined video, audio,
        # subtitle, script.json) are never cleaned up on their own and
        # accumulate forever -- ~27MB/task, found disk down to 4.5GB free
        # on 2026-09-15. Runs once per day here; only deletes folders
        # already backed up under Videos/ and at least 2 days old, so this
        # run's own just-created folders are never touched.
        from automation.cleanup_storage import main as cleanup_storage_main
        cleanup_storage_main(argv=[])
    except Exception as e:
        print(f"Storage cleanup failed (non-fatal): {e}")


if __name__ == "__main__":
    main()
