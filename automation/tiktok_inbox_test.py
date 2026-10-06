#!/usr/bin/env python3
"""Safe one-video test of the TikTok inbox (draft) upload. Nothing gets published.

  uv run python automation/tiktok_inbox_test.py path/to/video.mp4

The video appears as a DRAFT in the TikTok app of the authorised account (you
get a notification in the app). Open it, check it, and delete it or post it.
Needs a token with scope video.upload (see automation/tiktok_oauth_production.py
--scopes user.info.basic,video.publish,video.upload).
"""
import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from app.services import tiktok_api  # noqa: E402


def main():
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    path = sys.argv[1]
    if not tiktok_api.production_ready():
        sys.exit("No production token yet: run automation/tiktok_oauth_production.py first.")
    if not tiktok_api.has_scope("video.upload"):
        sys.exit(
            "Token lacks scope video.upload. Run:\n"
            "  uv run python automation/tiktok_oauth_production.py --redirect-uri <your redirect URL> "
            "--scopes user.info.basic,video.publish,video.upload"
        )
    result = tiktok_api.upload_video_to_inbox(path)
    print("Uploaded:", result)
    print("Now open the TikTok app on your phone: there should be a notification/draft for this video.")


if __name__ == "__main__":
    main()
