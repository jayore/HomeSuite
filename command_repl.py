#!/usr/bin/env python3
import sys

import command_runtime

# -----------------------------
# Environment / mode selection
# -----------------------------
# Default behavior (no --live): SAFE mode (no real device actions)
# --live: REAL mode
LIVE = bool(len(sys.argv) > 1 and sys.argv[1] == "--live")
mode = "live" if LIVE else "test"

gpio_ptt = command_runtime.initialize_runtime(mode)

if mode == "test":
    print("🧪 PiPhone REPL running in TEST MODE (HA calls stubbed)")

banner = "LIVE MODE" if LIVE else "TEST MODE"
print(f"\n🎤 PiPhone REPL ({banner})")
if LIVE:
    print("⚠️  Commands will control real devices")
print("Type phrases exactly as you would speak them.")
print("Ctrl+D or Ctrl+C to exit.\n")

while True:
    try:
        text = input("PiPhone > ").strip()
        if not text:
            continue
        response = gpio_ptt.process_device_commands(text)
        if response:
            print(response)
    except (EOFError, KeyboardInterrupt):
        print("\n👋 Exiting PiPhone REPL")
        sys.exit(0)
