from pathlib import Path
import sys

# Ensure repo root is importable when running from tools/
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from semantic_router import route_utterance, RouteOutcome

NOW = 1_000_000.0

tests = [
    ("hello", None, RouteOutcome.CHATGPT),
    ("thanks", None, RouteOutcome.CHATGPT),
    ("tell me a joke", None, RouteOutcome.CHATGPT),

    ("set living room brightness to 50", None, RouteOutcome.DEVICE),
    ("skip forward 10 seconds", None, RouteOutcome.DEVICE),
    ("movie", None, RouteOutcome.DEVICE),

    ("asdfasdf", None, RouteOutcome.ERROR),

    ("another one", None, RouteOutcome.ERROR),
    ("another one", NOW - 10, RouteOutcome.CHATGPT),
]

failed = 0
for text, last_ts, exp in tests:
    rr = route_utterance(text=text, now_ts=NOW, last_chatgpt_ts=last_ts)
    ok = rr.outcome == exp
    print(f"{'OK' if ok else 'FAIL'} | {text!r} | got={rr.outcome} expected={exp} last_chatgpt_ts={last_ts}")
    if not ok:
        failed += 1

raise SystemExit(1 if failed else 0)
