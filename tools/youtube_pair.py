#!/usr/bin/env python3
"""One-time pairing of PiPhone with the Apple TV YouTube app (Lounge API).

Two ways:

  # A) TV code: Apple TV -> YouTube -> Settings -> Link with TV code
  .venv/bin/python tools/youtube_pair.py

  # B) Reuse an existing pairing's screen id (e.g. from iSponsorBlockTV's
  #    config.json -> devices[].screen_id) — no TV code needed:
  .venv/bin/python tools/youtube_pair.py --screen-id <SCREEN_ID>

Credentials are saved to state/youtube_lounge.json; after this, PiPhone
reconnects automatically (no re-pairing) until the token is revoked.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import youtube_lounge as yl  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Pair PiPhone with the Apple TV YouTube app.")
    ap.add_argument("--screen-id", help="Reuse a known screen id (skips the TV code).")
    args = ap.parse_args()

    if args.screen_id:
        print("Pairing via screen id (reusing an existing pairing)...")
        ok = yl.pair_with_screen_id(args.screen_id.strip())
    else:
        print("On the Apple TV: YouTube -> Settings -> Link with TV code.")
        try:
            code = input("Enter the TV code: ").strip().replace(" ", "").replace("-", "")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return 1
        if not code:
            print("No code entered.")
            return 1
        print("Pairing...")
        ok = yl.pair(code)

    if ok:
        print("Paired! PiPhone can now control the Apple TV YouTube app.")
        return 0
    print("Pairing failed. Re-check the code/screen id and retry.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
