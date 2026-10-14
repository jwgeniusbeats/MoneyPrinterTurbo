#!/usr/bin/env python3
"""Publish parked Instagram Reels at their slot time.

Instagram's API cannot schedule, so daily_pipeline.py (INSTAGRAM_MODE=slot, the default) stores
platforms.instagram = {"status": "pending_slot", "scheduled_for": "<UTC ISO>"} next to the same slot
used for the YouTube/Facebook schedule. This script runs every 10 minutes (launchd,
com.nognize.instagramslot) and publishes what is due.

Rules that keep it from bursting again (see the 2026-09-20 incident in daily_pipeline.py):
  * at most ONE Reel per run
  * at least MIN_GAP_MINUTES since the last Instagram post it published
  * a claim ("publishing") is written under the lock before the slow upload, so two overlapping runs
    cannot post the same Reel twice; a claim older than CLAIM_TIMEOUT_MINUTES counts as abandoned
  * a failed upload becomes status "failed", which daily_pipeline.retry_failed_platforms() already retries

  uv run python automation/instagram_slot_publisher.py             # publish what is due
  uv run python automation/instagram_slot_publisher.py --dry-run   # only show what would happen
"""
import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from automation.post_log_lock import locked_post_log  # noqa: E402

MIN_GAP_MINUTES = 120
CLAIM_TIMEOUT_MINUTES = 45


def _parse(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def pick_due(entries: list, now: datetime):
    """Return (entry, reason). entry is None when nothing should be published now."""
    last_post = None
    due = []
    for e in entries:
        ig = e.get("platforms", {}).get("instagram", {})
        if ig.get("posted_at"):
            t = _parse(ig["posted_at"])
            if last_post is None or t > last_post:
                last_post = t
        status = ig.get("status")
        claimed_at = ig.get("claimed_at")
        if status == "publishing" and claimed_at and now - _parse(claimed_at) > timedelta(minutes=CLAIM_TIMEOUT_MINUTES):
            status = "pending_slot"  # abandoned claim
        if status == "pending_slot" and ig.get("scheduled_for") and _parse(ig["scheduled_for"]) <= now:
            due.append(e)
    if not due:
        return None, "nothing due"
    if last_post and now - last_post < timedelta(minutes=MIN_GAP_MINUTES):
        return None, f"last Instagram post was {int((now - last_post).total_seconds() // 60)} min ago (min gap {MIN_GAP_MINUTES})"
    due.sort(key=lambda e: _parse(e["platforms"]["instagram"]["scheduled_for"]))
    return due[0], "due"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    now = datetime.now(timezone.utc)

    with locked_post_log() as entries:
        entry, reason = pick_due(entries, now)
        if entry is None:
            print(f"{now.isoformat()} Instagram slot publisher: {reason}")
            return 0
        title = entry.get("title", entry.get("subject", ""))
        video_path = entry.get("video_path")
        description = entry.get("description", "")
        task_id = entry.get("task_id")
        if args.dry_run:
            print(f"DRY RUN: would publish {title!r} ({video_path})")
            return 0
        if not video_path or not os.path.exists(video_path):
            entry["platforms"]["instagram"] = {"status": "failed", "error": f"video file missing: {video_path}"}
            print(f"Instagram: video file missing for {title!r}, marked failed")
            return 1
        entry["platforms"]["instagram"] = {
            **entry["platforms"]["instagram"], "status": "publishing", "claimed_at": now.isoformat(),
        }

    # Slow network work happens OUTSIDE the lock; the result is merged into a fresh copy below.
    from app.services import instagram_api  # noqa: E402  (lazy: needs secrets, only when publishing)
    from automation.daily_pipeline import create_meta_variant  # noqa: E402

    variant = video_path
    result = None
    error = None
    try:
        variant = create_meta_variant(video_path)
        result = instagram_api.upload_reel_via_url(video_path=variant, caption=description)
    except Exception as e:  # noqa: BLE001
        error = str(e)
    finally:
        if variant != video_path and os.path.exists(variant):
            os.remove(variant)

    with locked_post_log() as fresh:
        for e in fresh:
            if e.get("task_id") != task_id:
                continue
            if error is None:
                e["platforms"]["instagram"] = {
                    "id": result.get("id"), "status": "live", "posted_at": datetime.now(timezone.utc).isoformat(),
                }
            else:
                e["platforms"]["instagram"] = {"status": "failed", "error": error}
    if error is None:
        print(f"Instagram published at slot: {result.get('id')} for {title!r}")
        return 0
    print(f"Instagram upload failed for {title!r}: {error}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
