"""
Direct YouTube Data API v3 integration: authenticate once via OAuth, then
upload + schedule videos programmatically with no browser interaction.

Credentials:
- secrets/youtube_client_secret.json - OAuth client (Google Cloud Console)
- secrets/youtube_token.json - cached user token, created on first auth

Docs: https://developers.google.com/youtube/v3/docs/videos/insert
"""
import os
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from loguru import logger

SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLIENT_SECRET_FILE = os.path.join(_BASE_DIR, "secrets", "youtube_client_secret.json")
TOKEN_FILE = os.path.join(_BASE_DIR, "secrets", "youtube_token.json")


def _get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        os.chmod(TOKEN_FILE, 0o600)

    return creds


def get_authenticated_service():
    return build("youtube", "v3", credentials=_get_credentials())


def get_analytics_service():
    return build("youtubeAnalytics", "v2", credentials=_get_credentials())


def upload_video(
    video_path: str,
    title: str,
    description: str = "",
    tags: Optional[list] = None,
    category_id: str = "27",  # Education -- more accurate than the old default
    # (22, People & Blogs) for psychology-facts content, and category feeds
    # into YouTube's related-video/recommendation matching.
    privacy_status: str = "private",
    publish_at: Optional[str] = None,
    made_for_kids: bool = False,
) -> dict:
    """
    publish_at: ISO 8601 UTC timestamp, e.g. "2026-09-14T11:00:00Z".
    When set, privacy_status is forced to "private" and YouTube auto-publishes
    the video publicly at that time (native scheduling, no cross-post API needed).
    """
    if not os.path.exists(video_path):
        raise FileNotFoundError(video_path)

    youtube = get_authenticated_service()

    status = {
        "privacyStatus": "private" if publish_at else privacy_status,
        "selfDeclaredMadeForKids": made_for_kids,
    }
    if publish_at:
        status["publishAt"] = publish_at

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags or [],
            "categoryId": category_id,
        },
        "status": status,
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True, mimetype="video/mp4")
    request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

    response = None
    while response is None:
        status_progress, response = request.next_chunk()
        if status_progress:
            logger.info(f"Upload progress: {int(status_progress.progress() * 100)}%")

    logger.info(f"Uploaded video id={response['id']} title={title!r}")
    return response


def update_video_description(video_id: str, description: str, title: str | None = None):
    """Update an already-uploaded video's description (and optionally title)
    in place. Needs the video's current snippet first -- the API replaces
    the whole snippet part, so category/tags must be preserved."""
    youtube = get_authenticated_service()
    resp = youtube.videos().list(part="snippet", id=video_id).execute()
    items = resp.get("items", [])
    if not items:
        raise ValueError(f"video not found: {video_id}")
    snippet = items[0]["snippet"]
    snippet["description"] = description[:5000]
    if title:
        snippet["title"] = title[:100]
    return youtube.videos().update(part="snippet", body={"id": video_id, "snippet": snippet}).execute()


def get_channel_stats() -> dict:
    """Lifetime channel-level stats (subscriber/view/video counts) via the
    Data API -- cheap, no analytics scope quirks, good enough for a
    monetization-eligibility check."""
    youtube = get_authenticated_service()
    resp = youtube.channels().list(part="statistics", mine=True).execute()
    items = resp.get("items", [])
    return items[0]["statistics"] if items else {}


def get_watch_minutes(days: int) -> float:
    """Rolling estimated watch minutes over the trailing `days` window, via
    the YouTube Analytics API. Used to check YouTube Partner Program
    eligibility (3,000 watch hours/12mo for entry, 4,000 for full
    monetization)."""
    from datetime import date, timedelta

    analytics = get_analytics_service()
    end = date.today()
    start = end - timedelta(days=days)
    resp = (
        analytics.reports()
        .query(
            ids="channel==MINE",
            startDate=start.isoformat(),
            endDate=end.isoformat(),
            metrics="estimatedMinutesWatched",
        )
        .execute()
    )
    rows = resp.get("rows")
    return float(rows[0][0]) if rows else 0.0


def get_views_in_window(days: int) -> int:
    """Rolling total views over the trailing `days` window, via the YouTube
    Analytics API. Used as a proxy for the Shorts-views monetization path
    (3M/90d entry, 10M/90d full) since this channel is Shorts-only."""
    from datetime import date, timedelta

    analytics = get_analytics_service()
    end = date.today()
    start = end - timedelta(days=days)
    resp = (
        analytics.reports()
        .query(
            ids="channel==MINE",
            startDate=start.isoformat(),
            endDate=end.isoformat(),
            metrics="views",
        )
        .execute()
    )
    rows = resp.get("rows")
    return int(rows[0][0]) if rows else 0


def get_channel_analytics(video_id: str) -> dict:
    """Fetch lifetime views/likes/comments for a video (Data API, no separate analytics scope)."""
    youtube = get_authenticated_service()
    resp = youtube.videos().list(part="statistics", id=video_id).execute()
    items = resp.get("items", [])
    return items[0]["statistics"] if items else {}


def get_video_retention(video_id: str, start_date: str = "2020-01-01") -> dict:
    """
    Fetch average view duration/percentage for a video via the YouTube
    Analytics API (needs yt-analytics.readonly, already in SCOPES).
    Returns {} if the video has too little traffic for Analytics to report yet.
    """
    from datetime import date

    analytics = get_analytics_service()
    resp = (
        analytics.reports()
        .query(
            ids="channel==MINE",
            startDate=start_date,
            endDate=date.today().isoformat(),
            metrics="averageViewDuration,averageViewPercentage,views",
            filters=f"video=={video_id}",
        )
        .execute()
    )
    rows = resp.get("rows")
    if not rows:
        return {}
    headers = [h["name"] for h in resp.get("columnHeaders", [])]
    return dict(zip(headers, rows[0]))


def set_thumbnail(video_id: str, image_path: str):
    """Upload a custom thumbnail (jpg/png, <2MB) for an already-uploaded video."""
    if not os.path.exists(image_path):
        raise FileNotFoundError(image_path)
    youtube = get_authenticated_service()
    media = MediaFileUpload(image_path, mimetype="image/jpeg")
    return youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
