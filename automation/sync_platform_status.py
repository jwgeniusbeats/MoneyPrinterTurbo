"""One-off + reusable fix: post_log.json's per-platform "status" field is
write-once and never gets updated after the initial upload call. A YouTube
entry uploaded with a future publishAt is written as "scheduled" and stays
that way forever, even once YouTube actually makes it public -- confirmed
2026-09-23 when the real YouTube channel showed 40 public videos while
post_log said only 11 were "live". This caused a wrong status report to the
user (counted from stale post_log data instead of the real platform).

Run manually: uv run python automation/sync_platform_status.py
Queries the real YouTube Data API for each logged video's actual
privacyStatus and corrects post_log.json's youtube.status field to match
reality (scheduled -> live once YouTube confirms it's public; also flags
anything YouTube reports as missing/private/deleted so a human notices).

TikTok is not fixed by this script -- there is no public API to query real
per-video status, so a wrong TikTok "live" entry (confirmed 2026-09-23:
"5 signs someone is lying to you, according to psychology" from 13 sep,
marked "live" but not actually present on TikTok, likely lost during the
account's early manual-posting days before this pipeline existed) has to be
corrected by hand -- done directly below this script, once, for that one
entry.
"""
import os
import sys

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
from app.services import meta_api, youtube_api  # noqa: E402
from automation.post_log_lock import locked_post_log  # noqa: E402


def sync_youtube(post_log):
    yt_ids = [e["platforms"]["youtube"]["id"] for e in post_log if e.get("platforms", {}).get("youtube", {}).get("id")]
    if not yt_ids:
        print("YouTube: no entries with an id found.")
        return 0

    yt = youtube_api.get_authenticated_service()
    real_status = {}
    for i in range(0, len(yt_ids), 50):
        batch = yt_ids[i:i + 50]
        resp = yt.videos().list(part="status", id=",".join(batch)).execute()
        found_ids = {item["id"] for item in resp.get("items", [])}
        for item in resp.get("items", []):
            st = item["status"]
            real_status[item["id"]] = "live" if st.get("privacyStatus") == "public" else st.get("privacyStatus")
        for missing_id in set(batch) - found_ids:
            real_status[missing_id] = "missing"  # deleted or otherwise gone

    changed = 0
    for e in post_log:
        yt_entry = e.get("platforms", {}).get("youtube")
        if not yt_entry or "id" not in yt_entry:
            continue
        real = real_status.get(yt_entry["id"])
        if real and real != yt_entry.get("status"):
            print(f"YouTube {e.get('title', '')[:50]!r}: {yt_entry.get('status')} -> {real}")
            yt_entry["status"] = real
            changed += 1
    return changed


def sync_facebook(post_log):
    # video.status.publishing_phase.publish_status is the real signal --
    # "ready"/video_status only means "finished encoding", it says nothing
    # about whether the scheduled publish time has actually passed. Confirmed
    # 2026-09-23: 4 of 5 sampled "scheduled" entries were already live on
    # Facebook for days, because nothing ever flips this field after upload.
    fb_ids = [e["platforms"]["facebook"]["id"] for e in post_log if e.get("platforms", {}).get("facebook", {}).get("id")]
    if not fb_ids:
        print("Facebook: no entries with an id found.")
        return 0

    page_id, token = meta_api._load_page_token()
    real_status = {}
    for vid in fb_ids:
        resp = requests.get(
            f"https://graph.facebook.com/v21.0/{vid}",
            params={"access_token": token, "fields": "status"},
            timeout=30,
        ).json()
        if "error" in resp:
            real_status[vid] = "missing"  # deleted or inaccessible
            continue
        publish_status = resp.get("status", {}).get("publishing_phase", {}).get("publish_status")
        real_status[vid] = "live" if publish_status == "published" else "scheduled" if publish_status == "scheduled" else publish_status

    changed = 0
    for e in post_log:
        fb_entry = e.get("platforms", {}).get("facebook")
        if not fb_entry or "id" not in fb_entry:
            continue
        real = real_status.get(fb_entry["id"])
        if real and real != fb_entry.get("status"):
            print(f"Facebook {e.get('title', '')[:50]!r}: {fb_entry.get('status')} -> {real}")
            fb_entry["status"] = real
            changed += 1
    return changed


def main():
    # Everything that reads post_log's current ids and writes back the
    # corrected status happens inside the lock: daily_pipeline.py or
    # mark_tiktok_posted.py could otherwise write mid-run and have their
    # change silently clobbered by this script's own overwrite (the same
    # class of bug post_log_lock.py exists to prevent -- see its docstring).
    with locked_post_log() as post_log:
        try:
            yt_changed = sync_youtube(post_log)
        except Exception as exc:
            print(f"YouTube: skipped, token error ({exc}) -- needs manual reauth, not attempting a fix here.")
            yt_changed = 0
        fb_changed = sync_facebook(post_log)

    total = yt_changed + fb_changed
    if total:
        print(f"\nUpdated {yt_changed} YouTube + {fb_changed} Facebook status field(s) in post_log.json.")
    else:
        print("Nothing to update -- all statuses already matched reality.")


if __name__ == "__main__":
    main()
