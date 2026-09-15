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

STORAGE_TASKS_DIR = os.path.join(BASE_DIR, "storage", "tasks")
VIDEOS_DIR = os.path.join(BASE_DIR, "Videos")
MIN_AGE_DAYS = 2


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


if __name__ == "__main__":
    main()
