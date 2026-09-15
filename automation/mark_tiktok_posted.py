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
import json
import sys
from datetime import datetime, timezone

BASE_DIR = "/Users/geniusbeats/MoneyPrinterTurbo"
POST_LOG_FILE = f"{BASE_DIR}/automation/post_log.json"


def main():
    if len(sys.argv) != 2:
        print("Usage: mark_tiktok_posted.py <video_path>", file=sys.stderr)
        sys.exit(1)

    video_path = sys.argv[1]
    with open(POST_LOG_FILE) as f:
        entries = json.load(f)

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

    with open(POST_LOG_FILE, "w") as f:
        json.dump(entries, f, indent=2)

    print(f"Marked TikTok live for: {match.get('title', match.get('subject', video_path))!r}")


if __name__ == "__main__":
    main()
