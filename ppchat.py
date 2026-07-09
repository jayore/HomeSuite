#!/usr/bin/env python3
import sys
import logging

import command_runtime
from interaction_flow import handle_text_interaction


def _quiet_console_logging():
    """
    Keep file logging intact, but remove stream handlers so ppchat stays clean.
    """
    try:
        root = logging.getLogger()
        for h in list(root.handlers):
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                root.removeHandler(h)
    except Exception:
        pass


def main():
    live = "--live" in sys.argv[1:]
    mode = "live" if live else "test"

    gpio_ptt = command_runtime.initialize_runtime(mode)
    _quiet_console_logging()

    banner = "LIVE MODE" if live else "TEST MODE"
    print(f"\nHomeSuite Chat ({banner})")
    if live:
        print("⚠️  Messages will control real devices")
    print("Type natural commands or questions.")
    print("Ctrl+D or Ctrl+C to exit.\n")

    while True:
        try:
            text = input("ppchat > ").strip()
            if not text:
                continue

            result = handle_text_interaction(gpio_ptt, text)
            if result.response_text:
                print(result.response_text)

        except (EOFError, KeyboardInterrupt):
            print("\nExiting HomeSuite Chat")
            sys.exit(0)


if __name__ == "__main__":
    main()
