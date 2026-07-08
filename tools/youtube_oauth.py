#!/usr/bin/env python3
"""One-time YouTube Data API login for PiPhone (OAuth device flow).

Prereq: download the OAuth client JSON from Google Cloud
(APIs & Services -> Credentials -> your "TV and Limited Input devices" client ->
Download JSON) and save it to state/youtube_oauth_client.json.

Then run:
  .venv/bin/python tools/youtube_oauth.py

It prints a URL + code; approve in a browser, and the refresh token is saved to
state/youtube_oauth.json. After that, playlist/duration features work and the
token refreshes silently.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import youtube_oauth as yo  # noqa: E402


def main() -> int:
    if yo.is_authed():
        print("Already authed (refresh token present). Re-running will refresh it.")
    ok = yo.device_login()
    if ok:
        tok = yo.get_access_token()
        print("Access token OK." if tok else "Saved, but token fetch failed — check logs.")
        rt = yo._load_token().get("refresh_token")
        if rt:
            print("\nTo share this auth across both Pis via git, set in private_config.py:")
            print(f'  YOUTUBE_OAUTH_REFRESH_TOKEN = "{rt}"')
        return 0 if tok else 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
