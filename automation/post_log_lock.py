"""
Advisory file lock for automation/post_log.json.

Why: multiple independent processes touch this file -- daily_pipeline.py
(a single run can span 30-90+ minutes across YouTube/Facebook/Instagram
uploads, saving after every video), tiktok-daily-post's
mark_tiktok_posted.py (runs 4x/day on its own fixed schedule via
claude-in-chrome, independent of daily_pipeline.py's runtime),
update_tiktok_stats.py, and analyze_performance.py. Every one of them does
a full load -> mutate -> full overwrite with no coordination, so if two
land at the same time, whichever writes second silently wins and can
erase the other's changes. This wraps that read-modify-write in a real
OS-level lock (fcntl.flock) so concurrent writers queue up instead of
clobbering each other.

Usage:
    from automation.post_log_lock import locked_post_log

    with locked_post_log() as entries:
        # read and mutate `entries` (a list) in place
        entries.append(...)
    # written back automatically on clean exit; not written if an
    # exception propagates out of the `with` block
"""
import contextlib
import fcntl
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POST_LOG_FILE = os.path.join(BASE_DIR, "automation", "post_log.json")
LOCK_FILE = POST_LOG_FILE + ".lock"


@contextlib.contextmanager
def locked_post_log():
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)  # blocks until any other holder releases
        entries = []
        if os.path.exists(POST_LOG_FILE):
            with open(POST_LOG_FILE) as f:
                entries = json.load(f)
        yield entries
        tmp_path = POST_LOG_FILE + ".tmp"
        with open(tmp_path, "w") as f:
            json.dump(entries, f, indent=2)
        os.replace(tmp_path, POST_LOG_FILE)  # atomic on the same filesystem
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
