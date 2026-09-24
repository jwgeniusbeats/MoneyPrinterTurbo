"""Proactive check: Google's OAuth "Testing" publishing status caps refresh
tokens at 7 days (confirmed 2026-09-23 via console.cloud.google.com/auth/audience
-- the Nognize Automation OAuth app is stuck in Testing status because its
restricted YouTube scopes were never declared for verification/publishing).
Until that's fixed, the youtube_token.json refresh token silently dies every
~7 days and social-comment-check/growth-audit start failing on invalid_grant
with no advance warning.

This script does NOT attempt to refresh or re-authenticate anything itself --
Google requires a real human click on the consent screen, and scripting past
that is exactly what got nognize37@gmail.com suspended before (see project
memory). It only checks the token's age and pushes a warning while there's
still time to reauth calmly, instead of finding out when something breaks.

Run manually: uv run python automation/check_youtube_token_health.py
"""
import json
import os
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN_FILE = os.path.join(BASE_DIR, "secrets", "youtube_token.json")
WARN_AFTER_DAYS = 5  # Testing-mode tokens die at 7 days; warn with 2 days of runway


def main():
    with open(TOKEN_FILE) as f:
        token = json.load(f)

    # google-auth doesn't store the refresh token's own issue date, only the
    # short-lived access token's "expiry". Fall back to file mtime as the
    # best available proxy for "when this refresh token was last (re)issued".
    mtime = datetime.fromtimestamp(os.path.getmtime(TOKEN_FILE), tz=timezone.utc)
    age_days = (datetime.now(timezone.utc) - mtime).total_seconds() / 86400

    print(f"youtube_token.json last written: {mtime.isoformat()} ({age_days:.1f} days ago)")

    if age_days >= WARN_AFTER_DAYS:
        print(f"WARN: token is {age_days:.1f} days old -- Testing-mode refresh tokens expire at 7 days.")
        return "warn", age_days
    print("OK: token still well within the 7-day Testing-mode window.")
    return "ok", age_days


if __name__ == "__main__":
    status, age = main()
    raise SystemExit(0 if status == "ok" else 1)
