"""
Merges scraped TikTok view/like/comment counts into automation/post_log.json.

Usage: uv run python automation/update_tiktok_stats.py <rows.json>
where rows.json is a JSON array of {"views": int, "likes": int, "comments": int}
in the SAME top-to-bottom order as TikTok Studio's content list (newest post
first — that's the list's default sort, do not reorder it).

Why order-based, not text matching: TikTok posts are made manually and have
no stored id, and fuzzy-matching a hand-typed caption against the original
video subject/title is unreliable (paraphrased wording, hashtags, emoji all
dilute the overlap). Every video that gets a TikTok post is logged to
post_log.json in the same order it's actually posted, so pairing scraped
rows (newest-first) against post_log's tiktok-tagged entries (also newest-
first once reversed) is a simple, exact correspondence — no thresholds to
tune. If the counts don't line up, this refuses to write rather than risk
mis-attributing stats to the wrong video.
"""
import json
import sys
from datetime import datetime, timezone

BASE_DIR = "/Users/geniusbeats/MoneyPrinterTurbo"
POST_LOG_FILE = f"{BASE_DIR}/automation/post_log.json"


def main():
    if len(sys.argv) != 2:
        print("Usage: update_tiktok_stats.py <rows.json>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        scraped_rows = json.load(f)  # newest-first, matches TikTok Studio's list order

    with open(POST_LOG_FILE) as f:
        entries = json.load(f)

    # post_log is appended in posting order (oldest-first). Only entries whose
    # tiktok status is "live" were actually confirmed posted by
    # tiktok-daily-post (via mark_tiktok_posted.py) -- a "pending_manual" one
    # isn't on TikTok's content list yet and would shift every pairing by one
    # if included. Reverse to newest-first to align with the scrape's order.
    tiktok_entries = [
        e for e in entries if e.get("platforms", {}).get("tiktok", {}).get("status") == "live"
    ]
    tiktok_entries_newest_first = list(reversed(tiktok_entries))

    if len(scraped_rows) > len(tiktok_entries_newest_first):
        print(
            f"WARNING: scraped {len(scraped_rows)} rows but only "
            f"{len(tiktok_entries_newest_first)} post_log entries have a tiktok "
            "field — there may be a TikTok post never logged here. "
            "Matching only the first (newest) entries; extra scraped rows ignored.",
            file=sys.stderr,
        )

    updated = 0
    for entry, row in zip(tiktok_entries_newest_first, scraped_rows):
        tt = entry["platforms"]["tiktok"]
        tt["views"] = row.get("views", 0)
        tt["likes"] = row.get("likes", 0)
        tt["comments"] = row.get("comments", 0)
        tt["stats_fetched_at"] = datetime.now(timezone.utc).isoformat()
        updated += 1

    with open(POST_LOG_FILE, "w") as f:
        json.dump(entries, f, indent=2)

    print(f"Updated TikTok stats for {updated} video(s), oldest-scraped-row matched last.")


if __name__ == "__main__":
    main()
