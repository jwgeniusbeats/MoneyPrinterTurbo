"""
Direct Meta Graph API integration for Facebook Page video publishing.
Uses the long-lived Page access token obtained via app/services/meta_app OAuth
(see secrets/meta_token.json). Page tokens minted from a long-lived user token
do not expire.

Docs: https://developers.facebook.com/docs/video-api/guides/publishing
"""
import json
import os
from typing import Optional

import requests
from loguru import logger

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOKEN_FILE = os.path.join(_BASE_DIR, "secrets", "meta_token.json")
PAGE_NAME = "Nognize"
GRAPH_VERSION = "v21.0"


def _load_page_token() -> tuple[str, str]:
    with open(TOKEN_FILE) as f:
        data = json.load(f)
    page = next(p for p in data["pages"] if p["name"] == PAGE_NAME)
    return page["id"], page["access_token"]


def get_video_insights(video_id: str) -> dict:
    """Lifetime views/likes/comments for a Facebook Page video -- was
    blocked for months by the same Meta account restriction that blocked
    posting; confirmed working again once that cleared (2026-09-15)."""
    _, token = _load_page_token()
    resp = requests.get(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{video_id}",
        params={
            "fields": "views,likes.summary(true),comments.summary(true)",
            "access_token": token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    return {
        "views": data.get("views", 0),
        "likes": data.get("likes", {}).get("summary", {}).get("total_count", 0),
        "comments": data.get("comments", {}).get("summary", {}).get("total_count", 0),
    }


def list_comments(video_id: str) -> list[dict]:
    """Top-level comments on a Facebook Page video, newest first. Returns
    [{"comment_id", "from", "text", "created_time"}, ...]."""
    _, token = _load_page_token()
    resp = requests.get(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{video_id}/comments",
        params={
            "fields": "id,message,from,created_time",
            "order": "reverse_chronological",
            "access_token": token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return [
        {
            "comment_id": item["id"],
            "from": item.get("from", {}).get("name", ""),
            "text": item.get("message", ""),
            "created_time": item.get("created_time", ""),
        }
        for item in resp.json().get("data", [])
    ]


def reply_to_comment(comment_id: str, text: str) -> dict:
    """Posts a public reply to an existing top-level comment on our own video."""
    _, token = _load_page_token()
    resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{comment_id}/comments",
        data={"message": text[:8000], "access_token": token},
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()


def update_video_description(video_id: str, description: str) -> dict:
    """Update an already-published Facebook video's description in place."""
    _, token = _load_page_token()
    resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{video_id}",
        data={"description": description[:5000], "access_token": token},
        timeout=60,
    )
    resp.raise_for_status()
    return resp.json()


def upload_facebook_video(
    video_path: str,
    title: str,
    description: str = "",
    scheduled_publish_time: Optional[int] = None,
) -> dict:
    """
    scheduled_publish_time: Unix timestamp (UTC) to schedule the video's
    publish time. When set, the video uploads now but stays unpublished until
    that time (native Facebook scheduling, same pattern as YouTube's publishAt).
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(video_path)

    page_id, token = _load_page_token()

    data = {
        "access_token": token,
        "title": title[:255],
        "description": description[:5000],
    }
    if scheduled_publish_time:
        data["published"] = "false"
        data["scheduled_publish_time"] = str(scheduled_publish_time)

    with open(video_path, "rb") as f:
        files = {"source": f}
        resp = requests.post(
            f"https://graph-video.facebook.com/{GRAPH_VERSION}/{page_id}/videos",
            data=data,
            files=files,
            timeout=300,
        )

    if not resp.ok:
        logger.error(f"Facebook video upload failed {resp.status_code}: {resp.text}")
    resp.raise_for_status()
    result = resp.json()
    logger.info(f"Facebook video posted: {result}")
    return result
