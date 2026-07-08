"""youtube_oauth.py — OAuth for the YouTube Data API v3 (device flow).

Headless-friendly OAuth 2.0 Device Authorization flow (RFC 8628) for a Pi:
the user visits a URL and types a short code; we poll for the token. The refresh
token is persisted and access tokens are refreshed silently thereafter.

Files (both gitignored, under state/):
  * youtube_oauth_client.json — the OAuth *client* (downloaded from Google Cloud,
    the {"installed": {...}} client_secret file). client_id/secret for an
    installed/TV client are not true secrets but we keep them out of git anyway.
  * youtube_oauth.json — the saved *token* (access + refresh + expiry).

Public API:
  * device_login()    — run the interactive device flow (used by the CLI tool).
  * get_access_token() — a valid access token, refreshing if needed, or None.
  * is_authed()       — True if we have a refresh token on disk.
  * SCOPES            — the scope(s) we request.

Everything is defensive: network/refresh failures return None and log, never
raise into the command path. The CLI surfaces errors loudly.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Optional

import requests

log = logging.getLogger("youtube_oauth")

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
_STATE_DIR = os.path.join(_BASE_DIR, "state")
_CLIENT_PATH = os.path.join(_STATE_DIR, "youtube_oauth_client.json")
_TOKEN_PATH = os.path.join(_STATE_DIR, "youtube_oauth.json")

# "youtube" scope = manage the account's playlists (create/insert/delete items)
# plus read access used for videos.list durations.
SCOPES = "https://www.googleapis.com/auth/youtube"

_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"

_EXPIRY_SKEW = 60  # refresh this many seconds before actual expiry


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def _load_client() -> Optional[dict]:
    # Preferred: private_config.py (committed in this private repo, so both Pis
    # get the creds via git). Fallback: a downloaded client_secret JSON in state/.
    try:
        import private_config as secrets
        cid = getattr(secrets, "YOUTUBE_OAUTH_CLIENT_ID", "")
        csec = getattr(secrets, "YOUTUBE_OAUTH_CLIENT_SECRET", "")
        if cid and csec:
            return {"client_id": cid, "client_secret": csec}
    except Exception as e:
        log.debug("youtube_oauth: private_config client unavailable: %s", e)
    try:
        with open(_CLIENT_PATH, "r") as f:
            data = json.load(f)
        c = data.get("installed") or data.get("web") or data
        if c.get("client_id") and c.get("client_secret"):
            return c
        log.warning("youtube_oauth: client file missing client_id/secret")
    except FileNotFoundError:
        log.warning("youtube_oauth: no client creds (private_config or %s)", _CLIENT_PATH)
    except Exception as e:
        log.warning("youtube_oauth: failed to read client file: %s", e)
    return None


def _load_token() -> dict:
    try:
        with open(_TOKEN_PATH, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        log.warning("youtube_oauth: failed to load token: %s", e)
        return {}


def _save_token(data: dict) -> None:
    try:
        os.makedirs(_STATE_DIR, exist_ok=True)
        tmp = _TOKEN_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, _TOKEN_PATH)
    except Exception as e:
        log.warning("youtube_oauth: failed to save token: %s", e)


# ---------------------------------------------------------------------------
# Token lifecycle
# ---------------------------------------------------------------------------

def _refresh_token() -> Optional[str]:
    """Refresh token from the runtime state file, or pinned in private_config
    (the latter lets both Pis share one auth via git, like Spotify's)."""
    rt = _load_token().get("refresh_token")
    if rt:
        return rt
    try:
        import private_config as secrets
        rt = getattr(secrets, "YOUTUBE_OAUTH_REFRESH_TOKEN", "") or None
    except Exception:
        rt = None
    return rt


def is_authed() -> bool:
    return bool(_refresh_token())


def _store_token_response(resp: dict, *, keep_refresh: Optional[str] = None) -> dict:
    """Normalize a Google token response into our on-disk shape."""
    data = _load_token()
    if resp.get("access_token"):
        data["access_token"] = resp["access_token"]
        data["expiry"] = time.time() + int(resp.get("expires_in", 3600))
    # refresh_token only comes back on first auth; preserve the existing one.
    rt = resp.get("refresh_token") or keep_refresh or data.get("refresh_token")
    if rt:
        data["refresh_token"] = rt
    if resp.get("scope"):
        data["scope"] = resp["scope"]
    _save_token(data)
    return data


def _refresh(client: dict, refresh_token: str) -> Optional[str]:
    try:
        r = requests.post(_TOKEN_URL, data={
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }, timeout=20)
        j = r.json()
        if r.status_code == 200 and j.get("access_token"):
            _store_token_response(j, keep_refresh=refresh_token)
            return j["access_token"]
        log.warning("youtube_oauth: refresh failed (%s): %s",
                    r.status_code, j.get("error_description") or j.get("error"))
    except Exception as e:
        log.warning("youtube_oauth: refresh error: %s", e)
    return None


def get_access_token() -> Optional[str]:
    """Return a currently-valid access token, refreshing silently if needed.
    None if we're not authed or the refresh fails."""
    client = _load_client()
    if not client:
        return None
    rt = _refresh_token()
    if not rt:
        return None
    tok = _load_token()
    if tok.get("access_token") and tok.get("expiry", 0) - _EXPIRY_SKEW > time.time():
        return tok["access_token"]
    return _refresh(client, rt)


# ---------------------------------------------------------------------------
# Device flow (interactive; used by tools/youtube_oauth.py)
# ---------------------------------------------------------------------------

def device_login(*, on_prompt=None) -> bool:
    """Run the device authorization flow. Prints/relays the verification URL and
    user code, then polls until the user approves. Returns True on success.

    `on_prompt(verification_url, user_code)` is called once with the details; if
    omitted, they're printed to stdout.
    """
    client = _load_client()
    if not client:
        print("No OAuth client file. Place the downloaded client_secret JSON at:")
        print(f"  {_CLIENT_PATH}")
        return False

    try:
        r = requests.post(_DEVICE_CODE_URL, data={
            "client_id": client["client_id"],
            "scope": SCOPES,
        }, timeout=20)
        dc = r.json()
        if r.status_code != 200 or not dc.get("device_code"):
            print(f"Device-code request failed ({r.status_code}): "
                  f"{dc.get('error_description') or dc.get('error') or dc}")
            return False
    except Exception as e:
        print(f"Device-code request error: {e}")
        return False

    url = dc.get("verification_url") or dc.get("verification_uri")
    code = dc["user_code"]
    if on_prompt:
        on_prompt(url, code)
    else:
        print("\n  1) On any device, open: " + url)
        print("  2) Enter this code:     " + code)
        print("  3) Approve access for PiPhone (click through the 'unverified app' "
              "warning if shown).\n  Waiting for approval...")

    interval = int(dc.get("interval", 5))
    deadline = time.time() + int(dc.get("expires_in", 1800))
    while time.time() < deadline:
        time.sleep(interval)
        try:
            r = requests.post(_TOKEN_URL, data={
                "client_id": client["client_id"],
                "client_secret": client["client_secret"],
                "device_code": dc["device_code"],
                "grant_type": _DEVICE_GRANT,
            }, timeout=20)
            j = r.json()
        except Exception as e:
            log.warning("youtube_oauth: poll error: %s", e)
            continue
        if r.status_code == 200 and j.get("access_token"):
            _store_token_response(j)
            print("Authorized! Token saved.")
            return True
        err = j.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        if err in ("access_denied", "expired_token", "invalid_grant"):
            print(f"Authorization failed: {err}")
            return False
        # Unknown error — log and keep trying until the deadline.
        log.warning("youtube_oauth: poll returned %s: %s", r.status_code, j)
    print("Timed out waiting for approval.")
    return False
