"""
Direct TikTok integration via the official Content Posting API (Direct Post,
FILE_UPLOAD source) -- replaces the fragile claude-in-chrome browser
automation against TikTok Studio's web uploader with a real API call.

Currently running against the app's Sandbox environment (secrets/
tiktok_sandbox_app.json) while the Production app is pending TikTok's audit.
Sandbox-authorized content can only publish as SELF_ONLY (private) -- once
the app passes audit, switch to secrets/tiktok_app.json and privacy_level
can move to PUBLIC_TO_EVERYONE.

Docs:
- https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post
- https://developers.tiktok.com/docs/en/content-posting-api-reference-get-video-status
"""
import json
import os
import time
from datetime import datetime, timezone

import requests
from loguru import logger

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SANDBOX_APP_FILE = os.path.join(_BASE_DIR, "secrets", "tiktok_sandbox_app.json")
PRODUCTION_APP_FILE = os.path.join(_BASE_DIR, "secrets", "tiktok_app.json")
TOKEN_FILE = os.path.join(_BASE_DIR, "secrets", "tiktok_token.json")

API_BASE = "https://open.tiktokapis.com"


def _load_app_credentials(sandbox: bool) -> dict:
    path = SANDBOX_APP_FILE if sandbox else PRODUCTION_APP_FILE
    with open(path) as f:
        return json.load(f)


def _load_token() -> dict:
    with open(TOKEN_FILE) as f:
        return json.load(f)


def _save_token(token: dict):
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f, indent=2)


def _token_expired(token: dict) -> bool:
    obtained_at = datetime.fromisoformat(token["obtained_at"])
    age_seconds = (datetime.now(timezone.utc) - obtained_at).total_seconds()
    return age_seconds >= token["expires_in"] - 300  # refresh 5 min early


def _refresh_access_token(token: dict) -> dict:
    creds = _load_app_credentials(sandbox=token.get("sandbox", False))
    resp = requests.post(
        f"{API_BASE}/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": creds["client_key"],
            "client_secret": creds["client_secret"],
            "grant_type": "refresh_token",
            "refresh_token": token["refresh_token"],
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    token.update({
        "access_token": data["access_token"],
        "refresh_token": data.get("refresh_token", token["refresh_token"]),
        "expires_in": data["expires_in"],
        "refresh_expires_in": data.get("refresh_expires_in", token["refresh_expires_in"]),
        "obtained_at": datetime.now(timezone.utc).isoformat(),
    })
    _save_token(token)
    logger.info("TikTok access token refreshed")
    return token


def _get_access_token() -> str:
    token = _load_token()
    if _token_expired(token):
        token = _refresh_access_token(token)
    return token["access_token"]


def upload_video_direct_post(
    video_path: str,
    title: str = "",
    privacy_level: str = "SELF_ONLY",
) -> dict:
    """
    Uploads video_path to TikTok via Direct Post (FILE_UPLOAD source) and
    waits for it to finish processing. Returns the final status-check
    response dict ({"status": "PUBLISH_COMPLETE", ...} or {"status":
    "FAILED", "fail_reason": ...}).

    privacy_level defaults to SELF_ONLY because Sandbox/unaudited apps can
    only post privately -- pass "PUBLIC_TO_EVERYONE" once the Production app
    has passed TikTok's audit.
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(video_path)

    access_token = _get_access_token()
    video_size = os.path.getsize(video_path)

    init_resp = requests.post(
        f"{API_BASE}/v2/post/publish/video/init/",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        },
        json={
            "post_info": {
                "privacy_level": privacy_level,
                "title": title[:2200],
            },
            "source_info": {
                "source": "FILE_UPLOAD",
                "video_size": video_size,
                "chunk_size": video_size,
                "total_chunk_count": 1,
            },
        },
        timeout=30,
    )
    if not init_resp.ok:
        logger.error(f"TikTok publish init failed {init_resp.status_code}: {init_resp.text}")
    init_resp.raise_for_status()
    init_data = init_resp.json()
    if init_data.get("error", {}).get("code") not in (None, "ok"):
        raise RuntimeError(f"TikTok publish init error: {init_data['error']}")

    publish_id = init_data["data"]["publish_id"]
    upload_url = init_data["data"]["upload_url"]
    logger.info(f"TikTok publish initialized: {publish_id}")

    with open(video_path, "rb") as f:
        video_bytes = f.read()

    upload_resp = requests.put(
        upload_url,
        headers={
            "Content-Type": "video/mp4",
            "Content-Length": str(video_size),
            "Content-Range": f"bytes 0-{video_size - 1}/{video_size}",
        },
        data=video_bytes,
        timeout=120,
    )
    if not upload_resp.ok:
        logger.error(f"TikTok video upload failed {upload_resp.status_code}: {upload_resp.text}")
    upload_resp.raise_for_status()
    logger.info(f"TikTok video bytes uploaded for {publish_id}")

    for attempt in range(6):
        time.sleep(5)
        status_resp = requests.post(
            f"{API_BASE}/v2/post/publish/status/fetch/",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json; charset=UTF-8",
            },
            json={"publish_id": publish_id},
            timeout=30,
        )
        status_resp.raise_for_status()
        status_data = status_resp.json()["data"]
        status = status_data.get("status")
        logger.info(f"TikTok publish status ({attempt + 1}/6): {status}")
        if status == "PUBLISH_COMPLETE":
            return status_data
        if status == "FAILED":
            raise RuntimeError(f"TikTok publish failed: {status_data.get('fail_reason')}")

    raise TimeoutError(f"TikTok publish status still pending after polling: {publish_id}")
