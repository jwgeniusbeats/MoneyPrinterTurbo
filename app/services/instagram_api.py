"""
Direct Instagram Graph API integration (Instagram API with Instagram Login).
Uses the long-lived Instagram user token obtained via app/services meta OAuth
flow (see secrets/instagram_token.json). Publishes video/Reels directly via
Meta's resumable upload protocol -- no public video URL required.

Docs: https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media
      https://developers.facebook.com/docs/instagram-platform/content-publishing/resumable-uploads/
"""
import json
import os
import time
from typing import Optional

import requests
from loguru import logger

from app.services import gcs_api

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOKEN_FILE = os.path.join(_BASE_DIR, "secrets", "instagram_token.json")
GRAPH_VERSION = "v21.0"


def _load_token() -> tuple[str, str]:
    with open(TOKEN_FILE) as f:
        data = json.load(f)
    return str(data["user_id"]), data["long_lived_token"]


def get_reel_insights(media_id: str) -> dict:
    """Lifetime views/likes/comments for a published Reel. Was blocked for
    months by the same Meta account restriction that blocked posting;
    confirmed working again once that cleared (2026-09-15)."""
    _, token = _load_token()
    resp = requests.get(
        f"https://graph.instagram.com/{GRAPH_VERSION}/{media_id}/insights",
        params={"metric": "views,likes,comments", "access_token": token},
        timeout=30,
    )
    resp.raise_for_status()
    result = {"views": 0, "likes": 0, "comments": 0}
    for item in resp.json().get("data", []):
        name = item.get("name")
        values = item.get("values", [])
        if name in result and values:
            result[name] = values[0].get("value", 0)
    return result


def upload_reel(
    video_path: str,
    caption: str,
    poll_interval: int = 5,
    poll_timeout: int = 300,
) -> dict:
    """
    Uploads a video as an Instagram Reel via resumable upload (no public URL
    needed), waits for processing, then publishes it.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(video_path)

    ig_user_id, token = _load_token()
    file_size = os.path.getsize(video_path)
    auth_header = {"Authorization": f"Bearer {token}"}

    # 1. Create the media container
    create_resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{ig_user_id}/media",
        headers=auth_header,
        json={
            "media_type": "REELS",
            "upload_type": "resumable",
            "caption": caption[:2200],
        },
        timeout=60,
    )
    if not create_resp.ok:
        logger.error(f"IG container creation failed {create_resp.status_code}: {create_resp.text}")
    create_resp.raise_for_status()
    container_id = create_resp.json()["id"]
    logger.info(f"Instagram media container created: {container_id}")

    # 2. Upload the video bytes to the resumable upload session
    with open(video_path, "rb") as f:
        video_bytes = f.read()

    upload_resp = requests.post(
        f"https://rupload.facebook.com/ig-api-upload/{GRAPH_VERSION}/{container_id}",
        headers={
            "Authorization": f"OAuth {token}",
            "offset": "0",
            "file_size": str(file_size),
        },
        data=video_bytes,
        timeout=300,
    )
    if not upload_resp.ok:
        logger.error(f"IG video upload failed {upload_resp.status_code}: {upload_resp.text}")
    upload_resp.raise_for_status()
    logger.info(f"Instagram video bytes uploaded: {upload_resp.text}")

    # 3. Poll for processing completion
    deadline = time.time() + poll_timeout
    while time.time() < deadline:
        status_resp = requests.get(
            f"https://graph.facebook.com/{GRAPH_VERSION}/{container_id}",
            headers=auth_header,
            params={"fields": "status_code,status"},
            timeout=30,
        )
        status_resp.raise_for_status()
        status = status_resp.json()
        code = status.get("status_code")
        logger.info(f"Instagram container status: {status}")
        if code == "FINISHED":
            break
        if code == "ERROR":
            raise RuntimeError(f"Instagram video processing failed: {status}")
        time.sleep(poll_interval)
    else:
        raise TimeoutError("Instagram video processing did not finish in time")

    # 4. Publish
    publish_resp = requests.post(
        f"https://graph.facebook.com/{GRAPH_VERSION}/{ig_user_id}/media_publish",
        headers=auth_header,
        data={"creation_id": container_id},
        timeout=60,
    )
    if not publish_resp.ok:
        logger.error(f"IG publish failed {publish_resp.status_code}: {publish_resp.text}")
    publish_resp.raise_for_status()
    result = publish_resp.json()
    logger.info(f"Instagram Reel published: {result}")
    return result


def upload_reel_via_url(
    video_path: str,
    caption: str,
    poll_interval: int = 5,
    poll_timeout: int = 300,
) -> dict:
    """
    Publishes a video as an Instagram Reel using the classic video_url flow:
    briefly hosts the file on Google Cloud Storage (public-read), points
    Instagram's media container at that URL, waits for processing, publishes,
    then deletes the temp GCS object. Used because the resumable-upload
    protocol (no public URL needed) returns "video_url is required" for this
    account/app -- that path needs Meta App Review / Advanced Access we don't
    have yet.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(video_path)

    ig_user_id, token = _load_token()
    public_url, object_name = gcs_api.upload_public_video(video_path)

    try:
        create_resp = requests.post(
            f"https://graph.instagram.com/{GRAPH_VERSION}/{ig_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": public_url,
                "caption": caption[:2200],
                "access_token": token,
            },
            timeout=60,
        )
        if not create_resp.ok:
            logger.error(f"IG container creation failed {create_resp.status_code}: {create_resp.text}")
        create_resp.raise_for_status()
        container_id = create_resp.json()["id"]
        logger.info(f"Instagram media container created: {container_id}")

        deadline = time.time() + poll_timeout
        while time.time() < deadline:
            status_resp = requests.get(
                f"https://graph.instagram.com/{GRAPH_VERSION}/{container_id}",
                params={"fields": "status_code,status", "access_token": token},
                timeout=30,
            )
            status_resp.raise_for_status()
            status = status_resp.json()
            code = status.get("status_code")
            logger.info(f"Instagram container status: {status}")
            if code == "FINISHED":
                break
            if code == "ERROR":
                raise RuntimeError(f"Instagram video processing failed: {status}")
            time.sleep(poll_interval)
        else:
            raise TimeoutError("Instagram video processing did not finish in time")

        publish_resp = requests.post(
            f"https://graph.instagram.com/{GRAPH_VERSION}/{ig_user_id}/media_publish",
            data={"creation_id": container_id, "access_token": token},
            timeout=60,
        )
        if not publish_resp.ok:
            logger.error(f"IG publish failed {publish_resp.status_code}: {publish_resp.text}")
        publish_resp.raise_for_status()
        result = publish_resp.json()
        logger.info(f"Instagram Reel published: {result}")
        return result
    finally:
        gcs_api.delete_object(object_name)
