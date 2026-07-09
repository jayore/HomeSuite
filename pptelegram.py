#!/usr/bin/env python3
import json
import logging
import sys
import time
import urllib.parse
import urllib.request
from typing import Optional

import command_runtime
from interaction_flow import handle_text_interaction
from request_context import (
    build_request_context,
    replace_current_request_context,
    set_current_request_context,
)

try:
    from private_config import (
        TELEGRAM_BOT_TOKEN,
        TELEGRAM_ALLOWED_USER_IDS,
        TELEGRAM_ALLOWED_CHAT_IDS,
    )
except Exception:
    TELEGRAM_BOT_TOKEN = ""
    TELEGRAM_ALLOWED_USER_IDS = []
    TELEGRAM_ALLOWED_CHAT_IDS = []

from integration_config import friendly_missing, missing, telegram_configured

API_BASE = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"


def _quiet_console_logging():
    """
    Keep file logging intact, but remove stream handlers so the bot process
    stays quiet in the console unless we explicitly print.
    """
    try:
        root = logging.getLogger()
        for h in list(root.handlers):
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                root.removeHandler(h)
    except Exception:
        pass


def _api_get(method: str, params: Optional[dict] = None, timeout: float = 30.0) -> dict:
    params = params or {}
    url = f"{API_BASE}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _api_post(method: str, data: Optional[dict] = None, timeout: float = 30.0) -> dict:
    data = data or {}
    url = f"{API_BASE}/{method}"
    body = urllib.parse.urlencode(data, doseq=True).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _is_allowed(message: dict) -> bool:
    try:
        chat_id = int(((message.get("chat") or {}).get("id")))
    except Exception:
        return False

    allowed_chats = set(TELEGRAM_ALLOWED_CHAT_IDS or [])

    print(f"[AUTH DEBUG] chat_id={chat_id}")
    print(f"[AUTH DEBUG] allowed_chats={allowed_chats}")

    # ✅ Trust chat, not sender
    # This allows:
    # - you (manual Telegram messages)
    # - Raycast (bot-origin messages)
    if allowed_chats and chat_id not in allowed_chats:
        print("[AUTH DEBUG] REJECTED: chat_id not allowed")
        return False

    return True


def _extract_text(message: dict) -> str:
    text = (message.get("text") or "").strip()
    if not text:
        return ""
    return text


def _handle_message(gpio_ptt, message: dict) -> Optional[str]:
    if not _is_allowed(message):
        return None

    text = _extract_text(message)
    if not text:
        return None

    request_ctx = build_request_context(
        source_id="telegram",
        origin="telegram",
    )
    previous_ctx = replace_current_request_context(request_ctx)

    try:
        try:
            logging.info("REQUEST_CONTEXT %s", request_ctx.to_log_dict())
        except Exception:
            pass

        if text == "/start":
            return "HomeSuite Telegram bot is ready."

        result = handle_text_interaction(gpio_ptt, text)
        return (result.response_text or "").strip() or "Okay."
    finally:
        set_current_request_context(previous_ctx)


def main():
    if not telegram_configured():
        print(friendly_missing("Telegram", missing("TELEGRAM_BOT_TOKEN")))
        sys.exit(2)

    gpio_ptt = command_runtime.initialize_runtime("live")
    _quiet_console_logging()

    print("HomeSuite Telegram bot starting (polling, live mode, allowlisted).")

    offset = None

    while True:
        try:
            params = {"timeout": 30}
            if offset is not None:
                params["offset"] = offset

            data = _api_get("getUpdates", params=params, timeout=35.0)
            if not data.get("ok"):
                time.sleep(2)
                continue

            for upd in data.get("result", []):
                try:
                    update_id = int(upd.get("update_id"))
                except Exception:
                    continue

                offset = update_id + 1

                message = upd.get("message") or {}
                if not message:
                    continue

                reply = _handle_message(gpio_ptt, message)
                if not reply:
                    continue

                chat_id = ((message.get("chat") or {}).get("id"))
                if not chat_id:
                    continue

                _api_post("sendMessage", {
                    "chat_id": chat_id,
                    "text": reply,
                })

        except KeyboardInterrupt:
            print("\nExiting PiPhone Telegram bot")
            sys.exit(0)
        except Exception as e:
            try:
                print(f"Telegram bot loop error: {e}")
            except Exception:
                pass
            time.sleep(2)


if __name__ == "__main__":
    main()
