"""
Pulls performance stats for every video in automation/post_log.json and
writes automation/learnings.md: a short summary of what's working, fed back
into future script generation by daily_pipeline.py (see run_batch()).

Run manually with: uv run python automation/analyze_performance.py
Intended to be invoked weekly by a scheduled task.
"""
import json
import os
import sys
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app.services import instagram_api, meta_api, youtube_api  # noqa: E402
from automation.post_log_lock import locked_post_log  # noqa: E402

LEARNINGS_FILE = os.path.join(BASE_DIR, "automation", "learnings.md")

MIN_VIDEOS_FOR_LEARNINGS = 5


def refresh_youtube_stats(entries: list) -> list:
    for entry in entries:
        yt = entry.get("platforms", {}).get("youtube")
        if not yt or not yt.get("id"):
            continue
        try:
            stats = youtube_api.get_channel_analytics(yt["id"])
            yt["views"] = int(stats.get("viewCount", 0))
            yt["likes"] = int(stats.get("likeCount", 0))
            yt["comments"] = int(stats.get("commentCount", 0))
            yt["stats_fetched_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            print(f"stats fetch failed for {entry.get('title')}: {e}", file=sys.stderr)
        try:
            retention = youtube_api.get_video_retention(yt["id"])
            if retention:
                yt["avg_view_duration_sec"] = retention.get("averageViewDuration")
                yt["avg_view_percentage"] = retention.get("averageViewPercentage")
        except Exception as e:
            print(f"retention fetch failed for {entry.get('title')}: {e}", file=sys.stderr)
    return entries


def refresh_facebook_stats(entries: list) -> list:
    """Was blocked for months by the Meta developer-account restriction;
    confirmed working again once that cleared (2026-09-15)."""
    for entry in entries:
        fb = entry.get("platforms", {}).get("facebook")
        if not fb or not fb.get("id"):
            continue
        try:
            stats = meta_api.get_video_insights(fb["id"])
            fb["views"] = stats["views"]
            fb["likes"] = stats["likes"]
            fb["comments"] = stats["comments"]
            fb["stats_fetched_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            print(f"facebook stats fetch failed for {entry.get('title')}: {e}", file=sys.stderr)
    return entries


def refresh_instagram_stats(entries: list) -> list:
    """Was blocked for months by the Meta developer-account restriction;
    confirmed working again once that cleared (2026-09-15)."""
    for entry in entries:
        ig = entry.get("platforms", {}).get("instagram")
        if not ig or not ig.get("id"):
            continue
        try:
            stats = instagram_api.get_reel_insights(ig["id"])
            ig["views"] = stats["views"]
            ig["likes"] = stats["likes"]
            ig["comments"] = stats["comments"]
            ig["stats_fetched_at"] = datetime.now(timezone.utc).isoformat()
        except Exception as e:
            print(f"instagram stats fetch failed for {entry.get('title')}: {e}", file=sys.stderr)
    return entries


def _total_views(entry: dict) -> int:
    """Sum of views across every platform that has reported them."""
    total = 0
    for platform in entry.get("platforms", {}).values():
        v = platform.get("views")
        if v is not None:
            total += int(v)
    return total


MIN_VIEWS_FOR_RETENTION = 10


def _score(entry: dict) -> tuple:
    """Rank key as (tier, value) so values on different scales never compare.
    Tier 2: YouTube retention (% watched), a stronger signal than raw views
    on a small channel -- but only when the video has >= MIN_VIEWS_FOR_RETENTION
    views, since a % from 3 views is noise. Tier 1: YouTube views. Tier 0:
    summed views across all platforms, so a stretch where YouTube uploads fail
    (account block, quota, etc.) doesn't blind the whole learning loop.
    Comparing a retention % against a view count directly (the old behavior)
    let e.g. 500 views outrank 255% watched."""
    yt = entry.get("platforms", {}).get("youtube", {})
    pct = yt.get("avg_view_percentage")
    views = yt.get("views")
    if pct and (views or 0) >= MIN_VIEWS_FOR_RETENTION:
        return (2, float(pct))
    if views is not None:
        return (1, float(views))
    return (0, float(_total_views(entry)))


MIN_AGE_DAYS_FOR_LEARNINGS = 3


def _parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_mature(entry: dict) -> bool:
    """True once the video had >= MIN_AGE_DAYS_FOR_LEARNINGS between going
    public and its stats being fetched. Videos are uploaded scheduled for the
    next morning and YouTube analytics lag 24-48h, so a stats pass run the
    evening after posting records 0 views, and the video then lands in the
    'Lowest performing -- avoid this angle' list for a reason that is only
    timing. Compare against stats_fetched_at (not now), so an old video whose
    stats were never refreshed late still counts as measured when fetched."""
    yt = entry.get("platforms", {}).get("youtube", {})
    live_at = (_parse_ts(yt.get("scheduled_for")) or _parse_ts(yt.get("posted_at"))
               or _parse_ts(entry.get("posted_at")))
    if live_at is None:
        return True
    fetched = _parse_ts(yt.get("stats_fetched_at")) or datetime.now(timezone.utc)
    return (fetched - live_at).days >= MIN_AGE_DAYS_FOR_LEARNINGS


def write_learnings(entries: list):
    scored = [e for e in entries
              if (_total_views(e) > 0 or e.get("platforms", {}).get("youtube", {}).get("views") is not None)
              and _is_mature(e)]
    if len(scored) < MIN_VIDEOS_FOR_LEARNINGS:
        with open(LEARNINGS_FILE, "w") as f:
            f.write(
                "# Performance learnings\n\n"
                f"Only {len(scored)} video(s) with stats so far "
                f"(need {MIN_VIDEOS_FOR_LEARNINGS}+ for reliable patterns). "
                "No learnings applied yet — keep posting.\n"
            )
        print(f"Only {len(scored)} scored videos, skipped learnings extraction.")
        return

    scored.sort(key=_score, reverse=True)
    top = scored[:5]
    # Only show a "lowest performing" section once top/bottom can't overlap --
    # with fewer than 8 scored videos, the same video would show up as both a
    # top and a bottom performer, giving the LLM prompt self-contradicting
    # guidance (emulate and avoid the same topic at once).
    bottom = scored[-3:] if len(scored) >= 8 else []

    def _fmt(e: dict) -> str:
        yt = e.get("platforms", {}).get("youtube", {})
        parts = []
        if yt.get("views") is not None:
            parts.append(f"{yt['views']} YT views")
        if yt.get("likes") is not None:
            parts.append(f"{yt['likes']} likes")
        if yt.get("avg_view_percentage"):
            parts.append(f"{yt['avg_view_percentage']:.0f}% avg watched")
        tt_views = e.get("platforms", {}).get("tiktok", {}).get("views")
        if tt_views is not None:
            parts.append(f"{tt_views} TikTok views")
        fb_views = e.get("platforms", {}).get("facebook", {}).get("views")
        if fb_views is not None:
            parts.append(f"{fb_views} FB views")
        ig_views = e.get("platforms", {}).get("instagram", {}).get("views")
        if ig_views is not None:
            parts.append(f"{ig_views} IG views")
        cat = e.get("category")
        hook = e.get("hook_type")
        tag = f" [{cat}/{hook}]" if cat or hook else ""
        return f"- \"{e['title']}\"{tag} — {', '.join(parts)}: {e['subject']}"

    lines = ["# Performance learnings", ""]
    lines.append("Auto-generated from real YouTube view/retention data.")
    lines.append("Used as extra context when generating new video scripts.")
    lines.append("")
    lines.append("## Top performing topics (ranked by YT watch-through % (10+ views), falls back to YT views, then total cross-platform views)")
    for e in top:
        lines.append(_fmt(e))
    if bottom:
        lines.append("")
        lines.append("## Lowest performing topics (avoid repeating this angle)")
        for e in bottom:
            lines.append(_fmt(e))

    # _score() picks whichever of "% watched" or "view count" is available
    # per video, so two entries in the same bucket can be on different
    # scales (e.g. 186 meaning 186% watched vs. 186 meaning 186 views).
    # Averaging _score() across a category/hook-type would silently mix
    # those units into one meaningless number. Total views is the only
    # metric every entry has on the same scale, so aggregates use that.
    tagged = [e for e in scored if e.get("category")]
    if tagged:
        by_cat = {}
        for e in tagged:
            by_cat.setdefault(e["category"], []).append(_total_views(e))
        by_hook = {}
        for e in tagged:
            if e.get("hook_type"):
                by_hook.setdefault(e["hook_type"], []).append(_total_views(e))
        lines.append("")
        lines.append("## Average total views by category")
        for cat, vals in sorted(by_cat.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
            lines.append(f"- {cat}: avg {sum(vals)/len(vals):.1f} ({len(vals)} videos)")
        if by_hook:
            lines.append("")
            lines.append("## Average total views by hook type")
            for hook, vals in sorted(by_hook.items(), key=lambda kv: -sum(kv[1]) / len(kv[1])):
                lines.append(f"- {hook}: avg {sum(vals)/len(vals):.1f} ({len(vals)} videos)")

    lines.append("")
    lines.append(
        "## Guidance for new scripts\n"
        "Favor hooks and subjects similar to the top performers above. "
        "Avoid repeating the framing of the lowest performers."
    )

    with open(LEARNINGS_FILE, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote learnings from {len(scored)} scored videos to {LEARNINGS_FILE}")


def main():
    with locked_post_log() as entries:
        if not entries:
            print("post_log.json empty, nothing to analyze.")
            return
        refresh_youtube_stats(entries)
        refresh_facebook_stats(entries)
        refresh_instagram_stats(entries)
        learnings_input = list(entries)
    write_learnings(learnings_input)


if __name__ == "__main__":
    main()
