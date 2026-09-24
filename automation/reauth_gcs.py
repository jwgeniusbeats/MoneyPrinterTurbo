"""One-off: re-authorize Google Cloud Storage access after the refresh token
in secrets/gcs_token.json was revoked/expired (root-caused 2026-09-20 --
Instagram uploads use GCS to briefly host the video file, and started
failing with invalid_grant once this token died; YouTube's own separate
token was unaffected).

Run manually: uv run python automation/reauth_gcs.py
Opens your browser for a Google sign-in/consent screen -- you approve it,
not this script.
"""
import json
import os

from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT_SECRET_FILE = os.path.join(BASE_DIR, "secrets", "youtube_client_secret.json")
GCS_TOKEN_FILE = os.path.join(BASE_DIR, "secrets", "gcs_token.json")
SCOPES = [
    "https://www.googleapis.com/auth/devstorage.read_write",
    "https://www.googleapis.com/auth/cloud-platform",
]


def main():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(GCS_TOKEN_FILE, "w") as f:
        json.dump({
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
            "universe_domain": "googleapis.com",
            "account": "",
            "expiry": creds.expiry.isoformat() + "Z" if creds.expiry else None,
        }, f, indent=2)
    print(f"New GCS token written to {GCS_TOKEN_FILE}")


if __name__ == "__main__":
    main()
