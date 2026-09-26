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

try:
    # Losse, optionele stap (branded intro/outro + IG-carousel-stills) via
    # heygen-com/hyperframes -- lokaal, geen API-kosten. Als Node/npx niet
    # aanwezig is faalt alleen deze import; de rest van de pipeline draait
    # gewoon door zonder bumpers/carousels (zie run_batch()).
    from app.services import hyperframes as hyperframes_service
except Exception:
    hyperframes_service = None

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
LINK_IN_BIO_URL = "tinyurl.com/nognize"
# The old tinyurl.com/238zsuaa link was created via TinyURL's now-deprecated
# API endpoint -- confirmed 2026-09-24 it now shows a cookie-consent wall
# plus a 10-second "Preview" countdown before redirecting, instead of an
# instant redirect. Every video's CTA had been sending traffic through that
# friction the whole time. First replaced with a random-string fresh link
# (tinyurl.com/ycxk9x6u), then upgraded to this branded alias the same day
# -- both verified instant 301, no interstitial.
LINK_IN_BIO_CTA = f"\n\n\U0001f517 More facts + early access: {LINK_IN_BIO_URL}"
CET_ZONE = ZoneInfo("Europe/Amsterdam")  # DST-aware CET/CEST, no manual offset to maintain
COMPILATION_LOG_FILE = os.path.join(BASE_DIR, "automation", "compilation_log.json")


def youtube_compilation_cta() -> str:
    """CTA pointing a Short at the latest long-form compilation, YouTube-only
    (not mixed into the shared `description` used for FB/IG too) -- Shorts
    get real views but almost no watch-hours, compilations get watch-hours
    but ~zero organic discovery since YouTube treats Shorts and long-form as
    separate feeds. This is the only traffic path between the two until the
    channel is big enough for either to surface the other on its own
    (confirmed 2026-09-24: both existing compilations sat at 2-5 views days
    after posting, despite same-channel Shorts getting hundreds). Points at
    the "Full Deep Dives" playlist (PLOp_76iMfnsI, created 2026-09-24)
    rather than a single latest-video link -- a playlist link never goes
    stale week to week and shows the whole backlog, not just one video."""
    if not os.path.exists(COMPILATION_LOG_FILE):
        return ""
    with open(COMPILATION_LOG_FILE) as f:
        entries = json.load(f)
    if not entries:
        return ""
    return "\n\n\U0001f3ac Full deep dives: youtube.com/playlist?list=PLOp_76iMfnsI"


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

# Kokoro (local, free TTS -- see resources/kokoro/) voice per category, so the
# narrator's tone matches the content instead of one voice for everything:
# am_adam = punchy/urgent male ("viral" hook delivery), for content that
# hooks on danger or manipulation; bm_george = calm authoritative British
# male (documentary/Nat-Geo narrator feel), for explainer/fact content;
# af_heart = warm female, for relationship/social and anything unclassified.
CATEGORY_VOICE = {
    "fear_safety": "am_adam",
    "bias_decisionmaking": "am_adam",
    "memory": "bm_george",
    "perception_illusion": "bm_george",
    "emotion_music": "bm_george",
    "social_relationships": "af_heart",
    "general": "af_heart",
}


def voice_for_topic(topic: str) -> str:
    """Pick a Kokoro voice id for a topic string, before the video (and its
    title) exist -- classify_video() needs a title too, so this repeats its
    keyword scan on the topic alone, good enough for voice selection."""
    text = topic.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            return CATEGORY_VOICE.get(cat, "af_heart")
    return CATEGORY_VOICE["general"]


# resource/songs/*.mp3 tracks grouped by tempo/energy (librosa beat_track +
# RMS, see automation/classify_bgm.py -- one-off analysis, not run per video)
# so bgm mood matches the narrator instead of a random pick across all 29
# tracks. Random bgm_type picked e.g. a 185bpm energetic track under the
# calm bm_george narration -- found 2026-09-18, first Kokoro test render.
BGM_BY_VOICE = {
    # am_adam: punchy/urgent hook delivery -> high-tempo (172-185bpm) tracks.
    "am_adam": [
        "output001.mp3", "output004.mp3", "output014.mp3",
        "output016.mp3", "output023.mp3", "output029.mp3",
    ],
    # bm_george: calm documentary narrator -> lowest tempo (<=90bpm) and/or
    # low-energy tracks.
    "bm_george": [
        "output010.mp3", "output025.mp3", "output028.mp3", "output022.mp3",
        "output011.mp3", "output007.mp3", "output018.mp3", "output003.mp3",
    ],
    # af_heart: warm/general -> everything mid-tempo (89-103bpm), the
    # largest pool since it also covers the "general" catch-all category.
    "af_heart": [
        "output000.mp3", "output002.mp3", "output005.mp3", "output006.mp3",
        "output008.mp3", "output009.mp3", "output012.mp3", "output013.mp3",
        "output015.mp3", "output017.mp3", "output019.mp3", "output020.mp3",
        "output021.mp3", "output024.mp3", "output027.mp3",
    ],
}


def bgm_for_voice(voice: str) -> str:
    import random

    pool = BGM_BY_VOICE.get(voice, BGM_BY_VOICE["af_heart"])
    return random.choice(pool)


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


def create_meta_variant(video_path: str) -> str:
    """Re-encode a distinct copy of video_path for Instagram/Facebook uploads.

    Instagram's "Originality Score" (confirmed by Adam Mosseri, in effect
    2026) flags a Reel whose underlying video file matches one already on
    the platform elsewhere -- even with no visible watermark -- and cuts
    reach 40-80%, no longer recommending it via Explore. This pipeline
    uploads the SAME rendered .mp4 to TikTok, YouTube, Facebook and
    Instagram, so the Instagram/Facebook copy was very likely getting
    fingerprint-matched against the TikTok upload. A tiny imperceptible
    zoom (crop + scale back to the original frame size) changes every
    pixel and the file bytes, breaking that match, without a visible
    difference to viewers. Re-encoded once here and reused for both the
    Facebook and Instagram uploads (not re-derived per platform) so they
    don't also fingerprint-match EACH OTHER.

    Falls back to the original video_path on any ffmpeg failure -- a
    slightly-lower-reach upload beats a failed one.
    """
    base, ext = os.path.splitext(video_path)
    variant_path = f"{base}-meta{ext}"
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-i", video_path,
            "-vf", "crop=trunc(iw*0.97/2)*2:trunc(ih*0.97/2)*2,scale=1080:1920",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k",
            variant_path,
        ],
        capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0 or not os.path.exists(variant_path):
        print(f"create_meta_variant failed, falling back to original: {result.stderr[-300:]}")
        return video_path
    return variant_path


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


def _apply_hyperframes_bumpers(video_path: str, subject: str) -> str:
    """
    Plakt de Nognize-intro/outro-bumper (hyperframes, lokaal, gratis) om een
    klaar-gerenderde video heen. Best-effort: bij elke fout (Node ontbreekt,
    ffmpeg-concat faalt, timeout) wordt de ORIGINELE video_path teruggegeven
    en gaat de rest van de pipeline gewoon door -- een bumper-fout mag nooit
    een succesvolle video laten mislukken.
    """
    if hyperframes_service is None or not hyperframes_service.is_available():
        return video_path
    try:
        bumpered = hyperframes_service.add_bumpers(video_path)
        return str(bumpered)
    except Exception as e:
        print(f"hyperframes bumper mislukt (non-fatal) voor {subject!r}: {e}")
        return video_path


def _generate_hyperframes_carousel(subject: str, script: str) -> list[str] | None:
    """
    Genereert best-effort een 3-slide IG-carousel (hook/fact/cta) uit het
    script van dezelfde video, via hyperframes (lokaal, gratis, 4:5-stills).
    Geeft None terug bij falen -- geen carousel is geen pipeline-fout.
    """
    if hyperframes_service is None or not hyperframes_service.is_available():
        return None
    try:
        sentences = [s.strip() for s in script.replace("\n", " ").split(".") if s.strip()]
        hook = (sentences[0] + ".") if sentences else subject
        fact = ". ".join(sentences[1:]) or script
        slides = [
            hyperframes_service.CarouselSlideSpec(kind="hook", text=hook[:80]),
            hyperframes_service.CarouselSlideSpec(kind="fact", text=fact[:220]),
            hyperframes_service.CarouselSlideSpec(kind="cta", text="Follow @nognize for more"),
        ]
        paths = hyperframes_service.render_carousel(slides)
        return [str(p) for p in paths]
    except Exception as e:
        print(f"hyperframes carousel mislukt (non-fatal) voor {subject!r}: {e}")
        return None


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
            chosen_voice = voice_for_topic(topic)
            entry = {
                "video_subject": topic,
                "video_language": "en-US",
                "subtitle_display_mode": subtitle_display_mode,
                "subtitle_animation": (
                    "pop_spring" if subtitle_display_mode == "word_by_word" else "none"
                ),
                "voice_name": f"kokoro:{chosen_voice}",
                "bgm_type": "custom",
                "bgm_file": bgm_for_voice(chosen_voice),
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
            subject = topics[task["index"] - 1]
            final_video = _apply_hyperframes_bumpers(r["videos"][0], subject)
            carousel_slides = _generate_hyperframes_carousel(subject, r["script"])
            succeeded.append(
                {
                    "task_id": r.get("task_id") or task.get("task_id"),
                    "topic_index": task["index"] - 1,
                    "video_subject": subject,
                    "script": r["script"],
                    "final_video": final_video,
                    "combined_video": r["combined_videos"][0] if r.get("combined_videos") else None,
                    "subtitle_display_mode": subtitle_modes[task["index"] - 1],
                    "carousel_slides": carousel_slides,
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
    "Provocative Paradox": "Name a trait or behavior people see as purely good (honesty, kindness, wisdom), then reveal the hidden cost it carries.",
    "Keyword Stack": "Open with a rapid-fire string of 3-4 trending psychology/relationship buzzwords, then land on one universal question tying them together.",
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
    # Cap retries per platform per call to 1 (real incident, 2026-09-20: a
    # GCS token fix let 4 backlogged Instagram posts all retry in the same
    # run, going live back-to-back within 5 minutes -- Instagram has no
    # native scheduling, so "retry" always means "post right now", and
    # burst-posting several at once looks spammy and isn't reversible.
    # One per call means one per daily_pipeline.py run (this function's
    # only caller), naturally spacing a backlog out at one per day instead
    # of dumping it all at once. Applies to YouTube/Facebook too even
    # though those two are less exposed (a retry there also posts
    # immediately rather than at a computed future slot).
    retries_done = {"youtube": 0, "facebook": 0, "instagram": 0}
    MAX_RETRIES_PER_PLATFORM_PER_RUN = 1
    for entry in post_log:
        video_path = entry.get("video_path")
        if not video_path or not os.path.exists(video_path):
            continue
        title = entry.get("title", entry.get("subject", ""))
        description = entry.get("description", "")
        platforms = entry.get("platforms", {})
        task_id = entry.get("task_id")

        yt = platforms.get("youtube", {})
        if yt.get("status") in RETRYABLE_STATUSES and retries_done["youtube"] < MAX_RETRIES_PER_PLATFORM_PER_RUN:
            retries_done["youtube"] += 1
            try:
                yt_result = youtube_api.upload_video(
                    video_path=video_path, title=title, description=description + youtube_compilation_cta(),
                    tags=entry.get("seo_tags"), privacy_status="public",
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
        ig = platforms.get("instagram", {})
        fb_eligible = fb.get("status") in RETRYABLE_STATUSES and retries_done["facebook"] < MAX_RETRIES_PER_PLATFORM_PER_RUN
        ig_eligible = ig.get("status") in RETRYABLE_STATUSES and retries_done["instagram"] < MAX_RETRIES_PER_PLATFORM_PER_RUN
        meta_variant_path = video_path
        if fb_eligible or ig_eligible:
            meta_variant_path = create_meta_variant(video_path)

        if fb_eligible:
            retries_done["facebook"] += 1
            try:
                fb_result = meta_api.upload_facebook_video(
                    video_path=meta_variant_path, title=title, description=description,
                )
                new_fb = {"id": fb_result.get("id"), "status": "live"}
                platforms["facebook"] = new_fb
                print(f"Retry OK: Facebook for {title!r}")
                if task_id:
                    retried_updates.setdefault(task_id, {})["facebook"] = new_fb
            except Exception as e:
                print(f"Retry still failing: Facebook for {title!r}: {e}")

        if ig_eligible:
            retries_done["instagram"] += 1
            try:
                ig_result = instagram_api.upload_reel_via_url(video_path=meta_variant_path, caption=description)
                new_ig = {"id": ig_result.get("id"), "status": "live"}
                platforms["instagram"] = new_ig
                print(f"Retry OK: Instagram for {title!r}")
                if task_id:
                    retried_updates.setdefault(task_id, {})["instagram"] = new_ig
            except Exception as e:
                print(f"Retry still failing: Instagram for {title!r}: {e}")

        if meta_variant_path != video_path and os.path.exists(meta_variant_path):
            os.remove(meta_variant_path)

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
        # Persisted so retry_failed_platforms() can pass the same tags on a
        # retried YouTube upload -- without this, a video that fails its
        # first upload attempt and succeeds on retry publishes with zero
        # SEO tags, since seo_tags only ever lived in this local variable.
        log_entry["seo_tags"] = seo_tags

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
                description=description + youtube_compilation_cta(),
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

        meta_variant_path = create_meta_variant(video_path)

        try:
            fb_result = meta_api.upload_facebook_video(
                video_path=meta_variant_path,
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
            ig_result = instagram_api.upload_reel_via_url(video_path=meta_variant_path, caption=description)
            print(f"Instagram published: {ig_result.get('id')}")
            log_entry["platforms"]["instagram"] = {"id": ig_result.get("id"), "status": "live"}
        except Exception as e:
            print(f"Instagram upload failed for {subject}: {e}")
            log_entry["platforms"]["instagram"] = {"status": "failed", "error": str(e)}

        if meta_variant_path != video_path and os.path.exists(meta_variant_path):
            os.remove(meta_variant_path)

        log_entry["platforms"]["tiktok"] = {"status": "pending_manual"}
        # Append under lock against a freshly-read copy, not the in-memory
        # `post_log` loaded once at the top of main() -- a full run can span
        # 30-90+ minutes across several videos' platform uploads, plenty of
        # time for a concurrent tiktok-daily-post run to have appended or
        # updated other entries in the meantime.
        with locked_post_log() as fresh_log:
            fresh_log.append(log_entry)

        manual_line = (
            f"=== {title} ===\nFile: {labeled_path}\nCaption:\n{description}\n"
            f"Suggested time: {slot_dt.isoformat()} (UTC) -> post manually on TikTok\n"
        )
        carousel_slides = item.get("carousel_slides")
        if carousel_slides:
            manual_line += (
                "IG carousel (optional, post manually, not auto-posted):\n"
                + "\n".join(f"  {p}" for p in carousel_slides) + "\n"
            )
        manual_lines.append(manual_line + "\n")

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
