"""
Deletes storage/tasks/<task_uuid>/ working folders (raw combined video,
final video, audio, subtitle, script.json) once they're old enough that
their finished video is safely backed up elsewhere -- these folders are
pure intermediate/working data, never cleaned up by the pipeline itself,
and accumulate forever (found accumulating ~27MB/task with disk down to
4.5GB free on 2026-09-15).

Safety: only deletes a task folder if a labeled copy of its final video
already exists under Videos/ (the pipeline's permanent, human-browsable
archive -- see app.utils.utils.save_labeled_videos) AND the folder is
older than MIN_AGE_DAYS. A task folder with no matching Videos/ copy is
left alone (still in progress, or the copy step failed) no matter how old.

Also runs app.services.cache_manager.clean_video_cache() (storage/cache_videos
-- downloaded stock-footage clips, keyed by source URL so repeat searches can
reuse them). That function already exists but is only ever wired to a manual
button in the Streamlit WebUI; our automation is headless and nobody clicks
it, so this cache also grows forever on its own -- found at 1GB/150 files on
2026-09-16. Kept for CACHE_VIDEOS_MAX_AGE_DAYS since, unlike storage/tasks,
older cached clips are still genuinely useful (avoids re-downloading the same
stock footage for a topic with similar search terms to an earlier video).

Usage: uv run python automation/cleanup_storage.py [--dry-run]
Intended to run daily (e.g. appended to the daily_pipeline cron) or by hand.
"""
import argparse
import os
import shutil
import sys
import time

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

from app.services import cache_manager  # noqa: E402

STORAGE_TASKS_DIR = os.path.join(BASE_DIR, "storage", "tasks")
VIDEOS_DIR = os.path.join(BASE_DIR, "Videos")
MIN_AGE_DAYS = 2
CACHE_VIDEOS_MAX_AGE_DAYS = 7


def _has_backed_up_video(task_id: str) -> bool:
    """A Videos/ subfolder name ends in _<short-task-id> (see
    utils.save_labeled_videos) -- the short id is the first 8 chars of the
    full task uuid used here. Match on that suffix."""
    if not os.path.isdir(VIDEOS_DIR):
        return False
    short_id = task_id.split("-")[0]
    for entry in os.listdir(VIDEOS_DIR):
        if entry.endswith(f"_{short_id}"):
            folder = os.path.join(VIDEOS_DIR, entry)
            if os.path.isdir(folder):
                for f in os.listdir(folder):
                    if f.endswith(".mp4") and os.path.getsize(os.path.join(folder, f)) > 100_000:
                        return True
    return False


def main(argv: list | None = None):
    # argv=None (the default) makes argparse read sys.argv, which is only
    # correct when this runs standalone -- daily_pipeline.py imports and
    # calls this function directly, and would otherwise hand argparse its
    # OWN command-line args by accident. Pass argv=[] for a programmatic,
    # no-flags call.
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    if not os.path.isdir(STORAGE_TASKS_DIR):
        print("No storage/tasks dir, nothing to clean.")
        return

    now = time.time()
    freed_bytes = 0
    deleted = 0
    skipped_too_new = 0
    skipped_no_backup = 0

    for entry in sorted(os.listdir(STORAGE_TASKS_DIR)):
        folder = os.path.join(STORAGE_TASKS_DIR, entry)
        if not os.path.isdir(folder):
            continue

        age_days = (now - os.path.getmtime(folder)) / 86400
        if age_days < MIN_AGE_DAYS:
            skipped_too_new += 1
            continue

        if not _has_backed_up_video(entry):
            skipped_no_backup += 1
            print(f"SKIP (no Videos/ backup found, leaving alone): {entry}")
            continue

        size = sum(
            os.path.getsize(os.path.join(dp, f))
            for dp, _, files in os.walk(folder)
            for f in files
        )
        if args.dry_run:
            print(f"Would delete: {entry} ({size / 1024 / 1024:.1f}MB, {age_days:.1f}d old)")
        else:
            shutil.rmtree(folder)
            print(f"Deleted: {entry} ({size / 1024 / 1024:.1f}MB freed)")
        freed_bytes += size
        deleted += 1

    verb = "Would free" if args.dry_run else "Freed"
    print(
        f"\n{verb} {freed_bytes / 1024 / 1024:.1f}MB from {deleted} task folder(s). "
        f"Skipped {skipped_too_new} (too new, <{MIN_AGE_DAYS}d) and "
        f"{skipped_no_backup} (no confirmed Videos/ backup yet)."
    )

    if args.dry_run:
        cache_stats = cache_manager.get_video_cache_stats(
            max_age_days=CACHE_VIDEOS_MAX_AGE_DAYS
        )
        print(
            f"Would free {cache_stats.total_size / 1024 / 1024:.1f}MB from "
            f"{cache_stats.file_count} cached video file(s) older than "
            f"{CACHE_VIDEOS_MAX_AGE_DAYS}d in {cache_manager.video_cache_dir()}."
        )
    else:
        cache_result = cache_manager.clean_video_cache(
            max_age_days=CACHE_VIDEOS_MAX_AGE_DAYS
        )
        print(
            f"Freed {cache_result.deleted_size / 1024 / 1024:.1f}MB from "
            f"{cache_result.deleted_count} cached video file(s) "
            f"({cache_result.failed_count} failed to delete)."
        )


if __name__ == "__main__":
    main()
