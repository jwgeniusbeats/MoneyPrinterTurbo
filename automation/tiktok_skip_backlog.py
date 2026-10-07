#!/usr/bin/env python3
"""Mark old queue entries as 'skipped' for the tiktok-daily-post task, keeping the newest N.

  uv run python automation/tiktok_skip_backlog.py            # dry run: only shows what it would do
  uv run python automation/tiktok_skip_backlog.py --keep 8 --apply

The tiktok-daily-post task posts the OLDEST entry of manual_post_queue.txt that is not yet in
tiktok_posted.log. After a pause of two weeks that means old videos go first and new ones wait.
This appends "<time> SKIPPED <path>" lines to tiktok_posted.log for all but the newest N unposted
entries, so the task skips them. Nothing else changes: post_log.json is NOT touched, so these
videos stay "pending_manual" and the stats pairing in update_tiktok_stats.py stays correct.
Undo: delete the SKIPPED lines from tiktok_posted.log.
"""
import argparse
import os
import re
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE = os.path.join(BASE, "automation", "manual_post_queue.txt")
LOG = os.path.join(BASE, "automation", "tiktok_posted.log")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--keep", type=int, default=8, help="newest unposted entries to keep (default 8)")
    ap.add_argument("--apply", action="store_true", help="write to tiktok_posted.log (default: dry run)")
    a = ap.parse_args()

    q = open(QUEUE).read()
    done = set()
    if os.path.exists(LOG):
        for line in open(LOG):
            parts = line.strip().split()
            done.update(p for p in parts if p.startswith("/"))
    files = [f.strip() for f in re.findall(r"^File: (.+)$", q, re.M)]
    seen, unposted = set(), []
    for f in files:  # file order = oldest first; drop duplicates
        if f not in done and f not in seen:
            seen.add(f)
            unposted.append(f)
    skip = unposted[: max(0, len(unposted) - a.keep)]
    keep = unposted[len(skip):]
    print(f"{len(files)} entries in queue, {len(unposted)} not posted yet.")
    print(f"Would skip {len(skip)} oldest, keep the newest {len(keep)}.")
    if keep:
        print("First video the task would post after this:", keep[0])
    if not a.apply:
        print("Dry run. Add --apply to write the SKIPPED lines.")
        return
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with open(LOG, "a") as f:
        for p in skip:
            f.write(f"{now} SKIPPED {p}\n")
    print(f"Wrote {len(skip)} SKIPPED lines to {LOG}")


if __name__ == "__main__":
    main()
