#!/usr/bin/env python3
import sys
from pathlib import Path

# Ensure repo root is on sys.path so imports work no matter where we run from.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from command_runtime import run_command


def _parse_cli(argv):
    text = None
    live = False
    capture = False
    isolated = False
    source_id = None
    source_type = None
    origin = None
    source_room = None
    effective_target_room = None

    i = 0
    while i < len(argv):
        arg = argv[i]

        if arg == "--live":
            live = True
            i += 1
            continue

        if arg == "--capture":
            capture = True
            i += 1
            continue

        if arg in {"--isolated", "--test"}:
            isolated = True
            i += 1
            continue

        if arg == "--source" and (i + 1) < len(argv):
            source_id = argv[i + 1]
            i += 2
            continue

        if arg == "--source-type" and (i + 1) < len(argv):
            source_type = argv[i + 1]
            i += 2
            continue

        if arg == "--origin" and (i + 1) < len(argv):
            origin = argv[i + 1]
            i += 2
            continue

        if arg == "--source-room" and (i + 1) < len(argv):
            source_room = argv[i + 1]
            i += 2
            continue

        if arg == "--target-room" and (i + 1) < len(argv):
            effective_target_room = argv[i + 1]
            i += 2
            continue

        if text is None:
            text = arg
            i += 1
            continue

        print(f"Unknown or extra argument: {arg}")
        raise SystemExit(2)

    if not text:
        print('Usage: test_commands.py "phrase here" [--live | --capture | --isolated] [--source SOURCE_ID] [--source-type TYPE] [--origin ORIGIN] [--source-room ROOM] [--target-room ROOM]')
        raise SystemExit(2)

    if sum((live, capture, isolated)) > 1:
        print("Choose only one of --live, --capture, or --isolated")
        raise SystemExit(2)

    # Safe command checks should see the deployment's actual HA state. The
    # isolated all-stubbed mode remains available for focused parser work.
    mode = "live" if live else ("test" if isolated else "capture")

    return {
        "text": text,
        "mode": mode,
        "source_id": source_id,
        "source_type": source_type,
        "origin": origin,
        "source_room": source_room,
        "effective_target_room": effective_target_room,
    }


def main():
    opts = _parse_cli(sys.argv[1:])

    result = run_command(
        opts["text"],
        mode=opts["mode"],
        source_id=opts["source_id"],
        source_type=opts["source_type"],
        origin=opts["origin"],
        source_room=opts["source_room"],
        effective_target_room=opts["effective_target_room"],
    )

    print("TEXT:", result.text)
    print("MODE:", result.mode)
    print("CONTEXT:", result.context.to_log_dict() if result.context else None)
    print("RETURN:", repr(result.return_value))
    print("_ACTION_OCCURRED:", result.action_occurred)

    if result.return_value is None and not result.action_occurred:
        print("(no device command matched; would hand off to conversational assistant)")


if __name__ == "__main__":
    main()
