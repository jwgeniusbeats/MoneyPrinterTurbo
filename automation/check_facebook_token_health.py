"""Proactive check: Meta Page access tokens don't have a token expiry
(expires_at == 0, confirmed 2026-09-23 via debug_token) but they DO carry a
separate "data access expiration" clock -- data_access_expires_at -- that
Meta enforces independently of token validity. If that date passes without
the app re-confirming data access (re-consent flow), API calls can start
failing with the token still reporting "valid". Same silent-expiry shape as
the YouTube Testing-mode 7-day cap (see check_youtube_token_health.py),
just with a longer, rolling window (observed ~90 days out from last check).

This script does NOT attempt to renew anything itself -- renewing data
access requires the Page admin to go through Meta's re-consent flow. It
only checks the current data_access_expires_at and pushes a warning while
there's still runway to act.

Run manually: uv run python automation/check_facebook_token_health.py
"""
import json
import os
import sys
from datetime import datetime, timezone

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_FILE = os.path.join(BASE_DIR, "secrets", "meta_app.json")
TOKEN_FILE = os.path.join(BASE_DIR, "secrets", "meta_token.json")
PAGE_NAME = "Nognize"
GRAPH_VERSION = "v21.0"
WARN_AFTER_DAYS = 14  # push a warning once data access is within 2 weeks of expiring


def main():
    with open(APP_FILE) as f:
        app = json.load(f)
    with open(TOKEN_FILE) as f:
        token_data = json.load(f)

    page = next(p for p in token_data["pages"] if p["name"] == PAGE_NAME)
    app_token = f"{app['app_id']}|{app['app_secret']}"

    resp = requests.get(
        f"https://graph.facebook.com/debug_token",
        params={"input_token": page["access_token"], "access_token": app_token},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json().get("data", {})

    if not data.get("is_valid"):
        print(f"FAIL: Facebook Page token for '{PAGE_NAME}' is not valid: {data.get('error')}")
        return "fail", None

    expires_at = data.get("expires_at", 0)
    data_access_expires_at = data.get("data_access_expires_at")

    if expires_at:
        expires = datetime.fromtimestamp(expires_at, tz=timezone.utc)
        print(f"NOTE: token itself now reports an expiry at {expires.isoformat()} (was non-expiring before).")

    if not data_access_expires_at:
        print("OK: token valid, no data_access_expires_at reported.")
        return "ok", None

    data_access_expires = datetime.fromtimestamp(data_access_expires_at, tz=timezone.utc)
    days_left = (data_access_expires - datetime.now(timezone.utc)).total_seconds() / 86400

    print(f"Facebook Page token for '{PAGE_NAME}': valid, expires_at={expires_at} (0=never)")
    print(f"data_access_expires_at: {data_access_expires.isoformat()} ({days_left:.1f} days left)")

    if days_left <= WARN_AFTER_DAYS:
        print(f"WARN: data access expires in {days_left:.1f} days -- Page admin needs to re-confirm data access via Meta's consent flow.")
        return "warn", days_left
    print("OK: data access well within its window.")
    return "ok", days_left


if __name__ == "__main__":
    status, _ = main()
    sys.exit(0 if status == "ok" else 1)
