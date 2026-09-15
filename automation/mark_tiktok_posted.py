"""
Flips a post_log.json entry's TikTok status from "pending_manual" to "live"
once tiktok-daily-post has actually confirmed the upload succeeded.

Why this matters: update_tiktok_stats.py pairs scraped TikTok rows against
post_log entries by ORDER (no stored TikTok id to match on). It must only
pair against entries that are actually live on TikTok -- an entry still
sitting at "pending_manual" would shift every pairing by one and silently
attribute stats to the wrong video. This script is the other half of that
fix: it's what makes "live" mean "actually confirmed posted."

Usage: uv run python automation/mark_tiktok_posted.py <video_path>
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/Users/geniusbeats/MoneyPrinterTurbo")
from automation.post_log_lock import locked_post_log  # noqa: E402


def main():
    if len(sys.argv) != 2:
        print("Usage: mark_tiktok_posted.py <video_path>", file=sys.stderr)
        sys.exit(1)

    video_path = sys.argv[1]

    # Everything happens inside the lock: this task runs on its own fixed
    # schedule, independent of daily_pipeline.py's (potentially 30-90+
    # minute) run, so the file could be mid-write when this fires.
    with locked_post_log() as entries:
        match = None
        for entry in entries:
            if entry.get("video_path") == video_path:
                match = entry
                break

        if match is None:
            print(f"No post_log entry found with video_path == {video_path!r}", file=sys.stderr)
            sys.exit(1)

        tt = match.setdefault("platforms", {}).setdefault("tiktok", {})
        tt["status"] = "live"
        tt["posted_at"] = datetime.now(timezone.utc).isoformat()
        title = match.get("title", match.get("subject", video_path))

    print(f"Marked TikTok live for: {title!r}")


if __name__ == "__main__":
    main()
