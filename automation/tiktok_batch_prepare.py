#!/usr/bin/env python3
"""Prepare a batch of videos for scheduling in TikTok Studio (allowed, no automation).

  uv run python automation/tiktok_batch_prepare.py                 # 14 videos, 2 per day, from tomorrow
  uv run python automation/tiktok_batch_prepare.py --count 20 --per-day 2 --out ~/Desktop/tiktok_batch

It takes the NEWEST unposted entries from manual_post_queue.txt and creates a folder with
  - the videos, named <date>_<time>_<title>.mp4 in the order they should go live
  - captions.txt: for every video the date/time to schedule and the caption to paste
Then you open https://www.tiktok.com/tiktokstudio/upload (desktop browser), upload a video,
paste its caption, choose "Schedule" and the date and time from the file name.

TikTok Studio schedules at most about 10 days ahead (per third-party guides, check the
date picker), so a batch of 14 at 2 per day covers 7 days.
Add --mark-posted to append the chosen paths to tiktok_posted.log so the tiktok-daily-post
task (if it is ever enabled) will not post them a second time.
"""
import argparse
import os
import re
import shutil
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QUEUE = os.path.join(BASE, "automation", "manual_post_queue.txt")
LOG = os.path.join(BASE, "automation", "tiktok_posted.log")
TZ = ZoneInfo("Europe/Amsterdam")
SLOTS = {1: ["17:00"], 2: ["11:00", "17:00"], 3: ["11:00", "14:00", "20:00"], 4: ["11:00", "14:00", "17:00", "20:00"]}


def parse_queue(text):
    entries = []
    for block in re.split(r"(?m)^=== ", text)[1:]:
        title = block.split(" ===", 1)[0].strip()
        m = re.search(r"(?m)^File: (.+)$", block)
        if not m:
            continue
        cap = re.search(r"(?ms)^Caption:\n(.*?)(?=^Suggested time:|^--- Batch|\Z)", block)
        entries.append({"title": title, "file": m.group(1).strip(), "caption": (cap.group(1).strip() if cap else "")})
    return entries


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "video"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=14)
    ap.add_argument("--per-day", type=int, default=2, choices=[1, 2, 3, 4])
    ap.add_argument("--start", help="first day, YYYY-MM-DD (default: tomorrow)")
    ap.add_argument("--out", default=os.path.expanduser("~/Desktop/tiktok_batch"))
    ap.add_argument("--mark-posted", action="store_true")
    a = ap.parse_args()

    done = set()
    if os.path.exists(LOG):
        for line in open(LOG):
            done.update(p for p in line.split() if p.startswith("/"))
    seen, todo = set(), []
    for e in parse_queue(open(QUEUE).read()):
        if e["file"] not in done and e["file"] not in seen and os.path.exists(e["file"]):
            seen.add(e["file"])
            todo.append(e)
    chosen = todo[-a.count:]  # newest N, oldest first
    if not chosen:
        raise SystemExit("No unposted videos with an existing file found in the queue.")

    day = datetime.strptime(a.start, "%Y-%m-%d").date() if a.start else (datetime.now(TZ).date() + timedelta(days=1))
    slots = SLOTS[a.per_day]
    os.makedirs(a.out, exist_ok=True)
    lines = []
    last = None
    for i, e in enumerate(chosen):
        d = day + timedelta(days=i // len(slots))
        t = slots[i % len(slots)]
        name = f"{d.isoformat()}_{t.replace(':', '-')}_{slug(e['title'])}.mp4"
        shutil.copy2(e["file"], os.path.join(a.out, name))
        lines.append(f"FILE: {name}\nSCHEDULE: {d.isoformat()} {t} (your local time)\nCAPTION:\n{e['caption']}\n")
        last = d
    with open(os.path.join(a.out, "captions.txt"), "w") as f:
        f.write("\n----------------------------------------\n\n".join(lines))
    span = (last - datetime.now(TZ).date()).days
    print(f"Prepared {len(chosen)} videos in {a.out}")
    print(f"Schedule runs until {last.isoformat()} ({span} days from today).")
    if span > 10:
        print("WARNING: that is more than ~10 days ahead; TikTok Studio may refuse the last ones. Use a lower --count.")
    if a.mark_posted:
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(LOG, "a") as f:
            for e in chosen:
                f.write(f"{now} BATCHED {e['file']}\n")
        print("Appended BATCHED lines to tiktok_posted.log.")


if __name__ == "__main__":
    main()
