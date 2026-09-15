"""
Minimal Google Cloud Storage integration used only to get a short-lived,
public HTTPS URL for a video file so Instagram's classic (non-resumable)
media-container flow can fetch it (Instagram requires a public video_url;
the resumable-upload flow needs Meta App Review we don't have yet).

Credentials:
- secrets/youtube_client_secret.json - reused OAuth client (same GCP project)
- secrets/gcs_token.json - cached user token for storage scopes

Docs: https://cloud.google.com/storage/docs/json_api/v1
"""
import os
import uuid

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from loguru import logger

SCOPES = [
    "https://www.googleapis.com/auth/devstorage.read_write",
    "https://www.googleapis.com/auth/cloud-platform",
]

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CLIENT_SECRET_FILE = os.path.join(_BASE_DIR, "secrets", "youtube_client_secret.json")
TOKEN_FILE = os.path.join(_BASE_DIR, "secrets", "gcs_token.json")

PROJECT_ID = "avian-pact-508500-r7"
BUCKET_NAME = f"nognize-automation-media-{PROJECT_ID}"


def _get_credentials() -> Credentials:
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=8765, open_browser=False)
        with open(TOKEN_FILE, "w") as f:
            f.write(creds.to_json())
        os.chmod(TOKEN_FILE, 0o600)

    return creds


def _auth_header() -> dict:
    creds = _get_credentials()
    return {"Authorization": f"Bearer {creds.token}"}


def _ensure_bucket():
    resp = requests.get(
        f"https://storage.googleapis.com/storage/v1/b/{BUCKET_NAME}",
        headers=_auth_header(),
        timeout=30,
    )
    if resp.status_code == 200:
        return
    if resp.status_code != 404:
        logger.error(f"GCS bucket lookup failed {resp.status_code}: {resp.text}")
        resp.raise_for_status()

    create_resp = requests.post(
        "https://storage.googleapis.com/storage/v1/b",
        headers=_auth_header(),
        params={"project": PROJECT_ID},
        json={
            "name": BUCKET_NAME,
            "location": "US",
            "storageClass": "STANDARD",
            "iamConfiguration": {"uniformBucketLevelAccess": {"enabled": False}},
        },
        timeout=30,
    )
    if not create_resp.ok:
        logger.error(f"GCS bucket creation failed {create_resp.status_code}: {create_resp.text}")
    create_resp.raise_for_status()
    logger.info(f"GCS bucket created: {BUCKET_NAME}")


def upload_public_video(video_path: str) -> tuple[str, str]:
    """
    Uploads video_path to GCS with public-read ACL. Returns (public_url, object_name).
    """
    _ensure_bucket()
    object_name = f"ig-tmp/{uuid.uuid4().hex}.mp4"

    with open(video_path, "rb") as f:
        video_bytes = f.read()

    headers = _auth_header()
    headers["Content-Type"] = "video/mp4"
    upload_resp = requests.post(
        f"https://storage.googleapis.com/upload/storage/v1/b/{BUCKET_NAME}/o",
        headers=headers,
        params={"uploadType": "media", "name": object_name, "predefinedAcl": "publicRead"},
        data=video_bytes,
        timeout=300,
    )
    if not upload_resp.ok:
        logger.error(f"GCS upload failed {upload_resp.status_code}: {upload_resp.text}")
    upload_resp.raise_for_status()

    public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{object_name}"
    logger.info(f"Uploaded temp public video: {public_url}")
    return public_url, object_name


def upload_public_file(local_path: str, object_name: str, content_type: str) -> str:
    """
    Uploads local_path to GCS at a fixed object_name (not a random uuid) with
    public-read ACL, and does NOT delete it afterward -- for small static
    files meant to stay reachable permanently (e.g. a legal/policy page, or
    a third-party domain-ownership verification file), unlike
    upload_public_video()'s short-lived temp objects. Returns the public URL.
    """
    _ensure_bucket()

    with open(local_path, "rb") as f:
        file_bytes = f.read()

    headers = _auth_header()
    headers["Content-Type"] = content_type
    upload_resp = requests.post(
        f"https://storage.googleapis.com/upload/storage/v1/b/{BUCKET_NAME}/o",
        headers=headers,
        params={"uploadType": "media", "name": object_name, "predefinedAcl": "publicRead"},
        data=file_bytes,
        timeout=60,
    )
    if not upload_resp.ok:
        logger.error(f"GCS static-file upload failed {upload_resp.status_code}: {upload_resp.text}")
    upload_resp.raise_for_status()

    public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{object_name}"
    logger.info(f"Uploaded public static file: {public_url}")
    return public_url


def delete_object(object_name: str):
    from urllib.parse import quote

    resp = requests.delete(
        f"https://storage.googleapis.com/storage/v1/b/{BUCKET_NAME}/o/{quote(object_name, safe='')}",
        headers=_auth_header(),
        timeout=30,
    )
    if resp.status_code not in (200, 204, 404):
        logger.error(f"GCS delete failed {resp.status_code}: {resp.text}")
    else:
        logger.info(f"Deleted temp object: {object_name}")
