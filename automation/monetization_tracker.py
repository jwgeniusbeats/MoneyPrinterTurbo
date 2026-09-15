"""
Tracks progress toward real monetization thresholds and writes
automation/monetization_status.md -- a plain-English "how close are we"
snapshot instead of having to check platform dashboards by hand.

Covers YouTube (real API data) and TikTok (follower count + recent-video
views, scraped weekly by the scrape-tiktok-stats task into
tiktok_followers.json / post_log.json). Instagram/Facebook don't have a
comparable creator-fund program to track, so they're listed for context
only.

Run manually with: uv run python automation/monetization_tracker.py
Intended to be invoked weekly by a scheduled task.
"""
import json
import os
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app.services import youtube_api  # noqa: E402

STATUS_FILE = os.path.join(BASE_DIR, "automation", "monetization_status.md")
TIKTOK_FOLLOWERS_FILE = os.path.join(BASE_DIR, "automation", "tiktok_followers.json")
POST_LOG_FILE = os.path.join(BASE_DIR, "automation", "post_log.json")

TIKTOK_FOLLOWERS_TARGET = 10_000
TIKTOK_VIEWS_30D_TARGET = 100_000

# YouTube Partner Program thresholds (rolling windows as of program rules).
YT_ENTRY_SUBS = 500
YT_ENTRY_VIDEOS_90D = 3
YT_ENTRY_WATCH_HOURS_365D = 3000
YT_ENTRY_SHORTS_VIEWS_90D = 3_000_000
YT_FULL_SUBS = 1000
YT_FULL_WATCH_HOURS_365D = 4000
YT_FULL_SHORTS_VIEWS_90D = 10_000_000


def _bar(current: float, target: float, width: int = 20) -> str:
    pct = max(0.0, min(1.0, current / target)) if target else 0.0
    filled = int(pct * width)
    return f"[{'#' * filled}{'-' * (width - filled)}] {pct*100:.1f}%"


def check_youtube() -> str:
    stats = youtube_api.get_channel_stats()
    subs = int(stats.get("subscriberCount", 0))
    total_views = int(stats.get("viewCount", 0))
    video_count = int(stats.get("videoCount", 0))

    try:
        watch_minutes_365 = youtube_api.get_watch_minutes(365)
    except Exception as e:
        print(f"watch-hours fetch failed (non-fatal): {e}", file=sys.stderr)
        watch_minutes_365 = 0.0
    watch_hours_365 = watch_minutes_365 / 60

    try:
        views_90 = youtube_api.get_views_in_window(90)
    except Exception as e:
        print(f"90-day views fetch failed (non-fatal): {e}", file=sys.stderr)
        views_90 = 0

    entry_ready = (
        subs >= YT_ENTRY_SUBS
        and video_count >= YT_ENTRY_VIDEOS_90D
        and (watch_hours_365 >= YT_ENTRY_WATCH_HOURS_365D or views_90 >= YT_ENTRY_SHORTS_VIEWS_90D)
    )
    full_ready = (
        subs >= YT_FULL_SUBS
        and (watch_hours_365 >= YT_FULL_WATCH_HOURS_365D or views_90 >= YT_FULL_SHORTS_VIEWS_90D)
    )

    lines = [
        "## YouTube",
        f"- Subscribers: {subs} / {YT_FULL_SUBS} — {_bar(subs, YT_FULL_SUBS)}",
        f"- Total lifetime views: {total_views:,} ({video_count} videos)",
        f"- Watch hours (365d): {watch_hours_365:.1f} / {YT_FULL_WATCH_HOURS_365D} — {_bar(watch_hours_365, YT_FULL_WATCH_HOURS_365D)}",
        f"- Views (90d, Shorts-path proxy): {views_90:,} / {YT_FULL_SHORTS_VIEWS_90D:,} — {_bar(views_90, YT_FULL_SHORTS_VIEWS_90D)}",
        "",
        f"**Entry-tier (Partner Program) eligible: {'YES — apply now' if entry_ready else 'not yet'}**",
        f"**Full monetization eligible: {'YES' if full_ready else 'not yet'}**",
    ]
    return "\n".join(lines)


def check_tiktok() -> str:
    if not os.path.exists(TIKTOK_FOLLOWERS_FILE):
        return (
            "\n## TikTok\n"
            "- No follower data yet — `scrape-tiktok-stats` writes "
            f"{os.path.basename(TIKTOK_FOLLOWERS_FILE)} on its next weekly run."
        )

    with open(TIKTOK_FOLLOWERS_FILE) as f:
        follower_data = json.load(f)
    followers = follower_data.get("followers", 0)
    updated_at = follower_data.get("updated_at", "unknown")

    views_30d = 0
    if os.path.exists(POST_LOG_FILE):
        with open(POST_LOG_FILE) as f:
            post_log = json.load(f)
        cutoff = datetime.now(timezone.utc).timestamp() - 30 * 86400
        for entry in post_log:
            tt = entry.get("platforms", {}).get("tiktok", {})
            if "views" not in tt:
                continue
            try:
                # Use TikTok's own posted_at (set by mark_tiktok_posted.py when
                # the post actually goes live) rather than the top-level
                # posted_at (video-generation time) -- these can diverge by
                # days if the TikTok posting queue backs up, which would
                # otherwise skew this 30-day window.
                tiktok_posted_at = tt.get("posted_at") or entry["posted_at"]
                posted_ts = datetime.fromisoformat(
                    tiktok_posted_at.replace("Z", "+00:00")
                ).timestamp()
            except Exception:
                continue
            if posted_ts >= cutoff:
                views_30d += tt["views"]

    ready = followers >= TIKTOK_FOLLOWERS_TARGET and views_30d >= TIKTOK_VIEWS_30D_TARGET
    return (
        "\n## TikTok\n"
        f"- Followers: {followers} / {TIKTOK_FOLLOWERS_TARGET:,} — {_bar(followers, TIKTOK_FOLLOWERS_TARGET)}\n"
        f"- Views (30d, videos posted in that window): {views_30d:,} / {TIKTOK_VIEWS_30D_TARGET:,} — "
        f"{_bar(views_30d, TIKTOK_VIEWS_30D_TARGET)}\n"
        f"- Follower count last scraped: {updated_at}\n"
        "- **Prerequisite, check on the TikTok mobile app**: the account must be switched "
        "to a Creator (or Business) account to even be eligible for Creator Rewards -- "
        "this toggle isn't exposed on TikTok's desktop web settings, only in the mobile "
        "app under Settings > Account > Switch to Business/Creator Account.\n\n"
        f"**Creator Rewards Program eligible: {'YES — apply now' if ready else 'not yet'}**"
    )


def main():
    sections = [check_youtube(), check_tiktok()]

    sections.append(
        "\n## Instagram / Facebook (no comparable program to track)\n"
        "- Instagram has no universal ad-revenue program in most regions — "
        "monetization here realistically means affiliate links, sponsorships, "
        "or Instagram Shopping once there's an audience.\n"
        "- Facebook in-stream ads need Page watch-time eligibility (checked in "
        "Meta Business Suite directly, not via this script)."
    )

    content = (
        f"# Monetization status\n\n_Last updated: {datetime.now(timezone.utc).isoformat()}_\n\n"
        + "\n\n".join(sections)
        + "\n"
    )
    with open(STATUS_FILE, "w") as f:
        f.write(content)
    print(f"Wrote {STATUS_FILE}")
    print(content)


if __name__ == "__main__":
    main()
