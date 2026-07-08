#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
try:
    from env_compat import env_get, install_homesuite_env_aliases
    install_homesuite_env_aliases()
except Exception:
    env_get = lambda name, default=None: os.environ.get(name, default)
import time
import uuid
import urllib.error
import urllib.request
from typing import Any, Dict, Optional


DEFAULT_BRAIN_URL = env_get(
    "PIPHONE_BRAIN_URL",
    "http://localhost:8765/command",
)

DEFAULT_SOURCE_ID = env_get(
    "PIPHONE_SATELLITE_SOURCE_ID",
    "kitchen_satellite",
)

DEFAULT_SOURCE_TYPE = env_get(
    "PIPHONE_SATELLITE_SOURCE_TYPE",
    "satellite",
)

DEFAULT_ORIGIN = env_get(
    "PIPHONE_SATELLITE_ORIGIN",
    "satellite_http",
)

DEFAULT_SOURCE_ROOM = env_get(
    "PIPHONE_SATELLITE_SOURCE_ROOM",
    "kitchen",
)

DEFAULT_API_KEY = env_get(
    "PIPHONE_HTTP_API_KEY",
    "",
)


def _read_api_key_from_file(path: Optional[str]) -> str:
    if not path:
        return ""
    try:
        with open(os.path.expanduser(path), "r", encoding="utf-8") as f:
            return (f.read() or "").strip()
    except Exception:
        return ""


def _json_post(url: str, payload: Dict[str, Any], *, api_key: str = "", timeout_s: float = 20.0) -> Dict[str, Any]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if api_key:
        headers["X-API-Key"] = api_key

    req = urllib.request.Request(
        url,
        data=body,
        headers=headers,
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = resp.read()
            text = raw.decode("utf-8", errors="replace")
            try:
                return json.loads(text)
            except Exception:
                return {
                    "ok": False,
                    "error": "invalid_json_response",
                    "status": getattr(resp, "status", None),
                    "raw": text,
                }

    except urllib.error.HTTPError as e:
        raw = e.read() if e.fp else b""
        text = raw.decode("utf-8", errors="replace")
        try:
            data = json.loads(text) if text else {}
        except Exception:
            data = {"raw": text}
        data.setdefault("ok", False)
        data.setdefault("error", f"http_{e.code}")
        data.setdefault("status", e.code)
        return data

    except Exception as e:
        return {
            "ok": False,
            "error": str(e),
        }


def _build_payload(args) -> Dict[str, Any]:
    request_id = args.request_id or f"{args.source_id}-{int(time.time())}-{uuid.uuid4().hex[:8]}"

    payload: Dict[str, Any] = {
        "text": args.text,
        "source_id": args.source_id,
        "source_type": args.source_type,
        "origin": args.origin,
        "source_room": args.room,
        "request_id": request_id,
        "response_mode": args.response_mode,
    }

    if args.target_room:
        payload["effective_target_room"] = args.target_room

    if args.stt_provider or args.stt_model:
        payload["stt"] = {
            "provider": args.stt_provider,
            "model": args.stt_model,
        }

    return payload


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Send a text command from a PiPhone satellite to the brain HTTP API.",
    )

    parser.add_argument(
        "text",
        nargs="+",
        help="Command text to send, e.g. 'volume 20'",
    )

    parser.add_argument(
        "--url",
        default=DEFAULT_BRAIN_URL,
        help=f"Brain command URL. Default: {DEFAULT_BRAIN_URL}",
    )

    parser.add_argument(
        "--api-key",
        default=DEFAULT_API_KEY,
        help="HTTP API key. Defaults to HOMESUITE_HTTP_API_KEY, then PIPHONE_HTTP_API_KEY.",
    )

    parser.add_argument(
        "--api-key-file",
        default=env_get("PIPHONE_HTTP_API_KEY_FILE", ""),
        help="Optional file containing HTTP API key.",
    )

    parser.add_argument(
        "--source-id",
        default=DEFAULT_SOURCE_ID,
        help=f"Stable satellite/source id. Default: {DEFAULT_SOURCE_ID}",
    )

    parser.add_argument(
        "--source-type",
        default=DEFAULT_SOURCE_TYPE,
        help=f"Source type. Default: {DEFAULT_SOURCE_TYPE}",
    )

    parser.add_argument(
        "--origin",
        default=DEFAULT_ORIGIN,
        help=f"Origin string. Default: {DEFAULT_ORIGIN}",
    )

    parser.add_argument(
        "--room",
        default=DEFAULT_SOURCE_ROOM,
        help=f"Physical source room. Default: {DEFAULT_SOURCE_ROOM}",
    )

    parser.add_argument(
        "--target-room",
        default="",
        help="Optional effective target room override.",
    )

    parser.add_argument(
        "--request-id",
        default="",
        help="Optional request id. Auto-generated if omitted.",
    )

    parser.add_argument(
        "--response-mode",
        default="text",
        choices=("text", "none"),
        help="Desired response mode. Default: text.",
    )

    parser.add_argument(
        "--stt-provider",
        default="",
        help="Optional STT provider metadata.",
    )

    parser.add_argument(
        "--stt-model",
        default="",
        help="Optional STT model metadata.",
    )

    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="HTTP timeout in seconds.",
    )

    parser.add_argument(
        "--json",
        action="store_true",
        help="Print full JSON response.",
    )

    args = parser.parse_args()
    args.text = " ".join(args.text).strip()

    if not args.text:
        print("No command text provided.", file=sys.stderr)
        return 2

    api_key = args.api_key or _read_api_key_from_file(args.api_key_file)
    payload = _build_payload(args)

    response = _json_post(
        args.url,
        payload,
        api_key=api_key,
        timeout_s=float(args.timeout),
    )

    if args.json:
        print(json.dumps(response, indent=2, ensure_ascii=False))
    else:
        ok = bool(response.get("ok"))
        handled = response.get("handled")
        text = response.get("text") or response.get("response") or ""
        error = response.get("error") or ""

        if ok:
            if text:
                print(text)
            elif handled:
                print("Okay.")
            else:
                print("Not handled.")
        else:
            print(f"Error: {error or 'request failed'}", file=sys.stderr)

    return 0 if response.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
