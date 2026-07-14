#!/usr/bin/env python3
"""Interactive command harness with explicit live, capture, and isolated modes."""

from __future__ import annotations

import sys

import command_runtime


def parse_mode(argv: list[str]) -> str:
    """Return the requested command runtime mode.

    Capture mode is the default because it resolves against real Home Assistant
    state while blocking writes. ``--isolated`` retains the old all-stubbed
    behavior for unit-style parser work.
    """
    flags = set(argv)
    known = {"--live", "--capture", "--isolated", "--test"}
    unknown = flags - known
    if unknown:
        raise ValueError(f"Unknown option(s): {', '.join(sorted(unknown))}")

    requested = int("--live" in flags) + int("--isolated" in flags or "--test" in flags)
    if requested > 1:
        raise ValueError("Choose only one of --live or --isolated")
    if "--live" in flags:
        return "live"
    if "--isolated" in flags or "--test" in flags:
        return "test"
    return "capture"


def main(argv: list[str] | None = None) -> int:
    try:
        mode = parse_mode(list(argv if argv is not None else sys.argv[1:]))
    except ValueError as exc:
        print(f"Error: {exc}")
        print("Usage: command_repl.py [--live | --capture | --isolated]")
        return 2

    runtime = command_runtime.initialize_runtime(mode)
    labels = {
        "capture": "CAPTURE MODE",
        "live": "LIVE MODE",
        "test": "ISOLATED TEST MODE",
    }
    print(f"\nHomeSuite REPL ({labels[mode]})")
    if mode == "capture":
        print("Home Assistant state is live; device writes are blocked and shown as HA_STUB calls.")
    elif mode == "live":
        print("Commands will control real devices.")
    else:
        print("Home Assistant reads and writes are stubbed.")
    print("Type phrases exactly as you would speak them.")
    print("Ctrl+D or Ctrl+C to exit.\n")

    while True:
        try:
            text = input("homesuite > ").strip()
            if not text:
                continue
            response = runtime.process_device_commands(text)
            if response:
                print(response)
        except (EOFError, KeyboardInterrupt):
            print("\nExiting HomeSuite REPL")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
