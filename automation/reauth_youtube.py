"""One-off: re-authorize YouTube access to get a fresh refresh token issued
under the OAuth app's new "In production" publishing status (fixed
2026-09-23 -- the app was stuck in "Testing" status, which caps refresh
tokens at 7 days regardless of use; that's why social-comment-check and
growth-audit kept hitting invalid_grant). The current token was issued
under the old Testing status and is already dead, so this needs to run once
to get a Production-issued token that doesn't expire on that same 7-day
timer.

Run manually: uv run python automation/reauth_youtube.py
Opens your browser for a Google sign-in/consent screen -- you approve it,
not this script. You may see an "unverified app" warning screen first
(the app requests sensitive YouTube scopes and hasn't gone through full
Google verification) -- click "Advanced" -> "Go to Nognize Automation
(unsafe)" to proceed; this is expected and does not indicate anything is
actually wrong, it's just Google's default warning for any app with
sensitive scopes that hasn't paid for/completed the verification review.
"""
import json
import os

from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT_SECRET_FILE = os.path.join(BASE_DIR, "secrets", "youtube_client_secret.json")
TOKEN_FILE = os.path.join(BASE_DIR, "secrets", "youtube_token.json")
SCOPES = [
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]


def main():
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=0)

    with open(TOKEN_FILE, "w") as f:
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
    print(f"New YouTube token written to {TOKEN_FILE}")


if __name__ == "__main__":
    main()
