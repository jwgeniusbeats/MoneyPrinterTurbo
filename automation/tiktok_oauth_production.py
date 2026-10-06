#!/usr/bin/env python3
"""One-time consent for the PRODUCTION TikTok app (run on the Mac, in the repo root).

  python3 automation/tiktok_oauth_production.py --redirect-uri https://<your registered redirect URL>

What it does:
 1. Reads secrets/tiktok_app.json ({"client_key": ..., "client_secret": ...});
    if the file does not exist it asks for both (hidden input) and creates it.
 2. Prints the TikTok authorize URL. Open it in the browser where you are logged
    into the Nognize TikTok account and press Authorize.
 3. TikTok redirects to your redirect URI (your callback page shows the query
    string). Paste the FULL redirected URL here (or just the code).
 4. Exchanges the code for tokens and writes secrets/tiktok_token.json with
    "sandbox": false (the sandbox token is first copied to
    secrets/tiktok_token.sandbox.bak.json).
 5. Asks TikTok which privacy levels this account may post with. If
    PUBLIC_TO_EVERYONE is not listed, the audit/approval did not cover public
    posting yet and the pipeline must NOT be expected to post publicly.

The redirect URI must be exactly one of the URIs registered under
"URL properties"/Login Kit of the app. Endpoints and parameters follow TikTok's
docs (Login Kit web, user access token management). Not tested against TikTok
from the build environment: the first real run is the test.
"""
import argparse
import getpass
import json
import os
import secrets as pysecrets
import shutil
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, unquote, urlparse

import requests

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_FILE = os.path.join(BASE, "secrets", "tiktok_app.json")
TOKEN_FILE = os.path.join(BASE, "secrets", "tiktok_token.json")
SANDBOX_BAK = os.path.join(BASE, "secrets", "tiktok_token.sandbox.bak.json")
AUTH_URL = "https://www.tiktok.com/v2/auth/authorize/"
API = "https://open.tiktokapis.com"


def load_app():
    if os.path.exists(APP_FILE):
        with open(APP_FILE) as f:
            app = json.load(f)
        if app.get("client_key") and app.get("client_secret"):
            return app
        sys.exit(f"{APP_FILE} exists but client_key/client_secret are missing")
    print("secrets/tiktok_app.json not found. Copy the production Client key and Client secret")
    print("from the TikTok developer portal (Nognize > Production > App details > Credentials).")
    app = {
        "client_key": getpass.getpass("Client key: ").strip(),
        "client_secret": getpass.getpass("Client secret: ").strip(),
    }
    if not app["client_key"] or not app["client_secret"]:
        sys.exit("empty value, aborting")
    os.makedirs(os.path.dirname(APP_FILE), exist_ok=True)
    with open(APP_FILE, "w") as f:
        json.dump(app, f, indent=2)
    os.chmod(APP_FILE, 0o600)
    print(f"wrote {APP_FILE}")
    return app


def extract_code(pasted, expected_state):
    """Accept the full redirected URL, a bare query string, or the raw code."""
    pasted = pasted.strip()
    if "code=" in pasted:
        qs = urlparse(pasted).query if "://" in pasted else pasted.lstrip("?")
        params = parse_qs(qs)  # parse_qs already URL-decodes
        if "error" in params:
            sys.exit(f"TikTok returned an error: {params.get('error')} {params.get('error_description', '')}")
        state = params.get("state", [None])[0]
        if state != expected_state:
            sys.exit("state does not match: this is not the response to the URL printed above. Aborting.")
        return params["code"][0]
    if "://" in pasted or "/" in pasted:
        sys.exit(
            "That is a URL without '?code=...' in it, so it is not the response from TikTok.\n"
            "Open the Authorize link printed above, press Authorize, and copy the address bar of the page\n"
            "you land on AFTER that: it must contain code=... and state=..."
        )
    return unquote(pasted)  # TikTok requires the code to be URL-decoded


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--redirect-uri", required=True, help="exactly as registered in the TikTok app")
    ap.add_argument("--scopes", default="user.info.basic,video.publish", help="comma separated, no spaces")
    a = ap.parse_args()
    if not a.redirect_uri.startswith("https://") or "PLAK" in a.redirect_uri.upper():
        sys.exit("--redirect-uri must be the real https:// URL only (no placeholder text in front of it).")

    app = load_app()
    state = pysecrets.token_urlsafe(16)
    url = (
        f"{AUTH_URL}?client_key={quote(app['client_key'])}&scope={quote(a.scopes, safe=',.')}"
        f"&response_type=code&redirect_uri={quote(a.redirect_uri, safe='')}&state={state}"
    )
    print("\nOpen this URL in the browser logged into the Nognize TikTok account and press Authorize:\n")
    print(url)
    print("\nAfter that, copy the full URL of the page you land on (the address bar) and paste it here.")
    code = extract_code(input("\nPasted URL or code: "), state)

    r = requests.post(
        f"{API}/v2/oauth/token/",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_key": app["client_key"],
            "client_secret": app["client_secret"],
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": a.redirect_uri,
        },
        timeout=30,
    )
    data = r.json()
    if not r.ok or "access_token" not in data:
        sys.exit(f"token exchange failed ({r.status_code}): {json.dumps(data)[:500]}")

    if os.path.exists(TOKEN_FILE):
        shutil.copy2(TOKEN_FILE, SANDBOX_BAK)
        print(f"previous token copied to {SANDBOX_BAK}")
    token = {
        "access_token": data["access_token"],
        "refresh_token": data["refresh_token"],
        "expires_in": data["expires_in"],
        "refresh_expires_in": data.get("refresh_expires_in"),
        "open_id": data.get("open_id"),
        "scope": data.get("scope"),
        "obtained_at": datetime.now(timezone.utc).isoformat(),
        "sandbox": False,
    }
    os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
    with open(TOKEN_FILE, "w") as f:
        json.dump(token, f, indent=2)
    os.chmod(TOKEN_FILE, 0o600)
    print(f"wrote {TOKEN_FILE} (sandbox=False, scopes: {token['scope']})")

    # Which privacy levels may this account post with via the API?
    c = requests.post(
        f"{API}/v2/post/publish/creator_info/query/",
        headers={"Authorization": f"Bearer {token['access_token']}", "Content-Type": "application/json; charset=UTF-8"},
        timeout=30,
    )
    try:
        info = c.json().get("data", {})
    except ValueError:
        info = {}
    levels = info.get("privacy_level_options")
    print("\nPrivacy levels offered by TikTok for this account:", levels)
    print(f"Granted scopes: {token['scope']}")
    print("The pipeline uploads drafts to your TikTok inbox by default (needs video.upload); you finish the post in the app.")
    if levels and "PUBLIC_TO_EVERYONE" in levels:
        print("Note: TikTok lists PUBLIC_TO_EVERYONE, but unaudited apps can still be blocked at post time")
        print("(error unaudited_client_can_only_post_to_private_accounts). Direct Post only works if TIKTOK_MODE=direct AND the audit passed.")
    else:
        print("WARNING: PUBLIC_TO_EVERYONE is NOT offered. Do not rely on public API posting yet.")
        print("Check in the TikTok developer portal that Content Posting API / Direct Post is approved for the production app.")
        print("Raw response:", json.dumps(c.json())[:500] if c.content else c.status_code)


if __name__ == "__main__":
    main()
