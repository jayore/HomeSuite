#!/usr/bin/env python3
"""Measure the bounded conversational-NL work outside command I/O."""

from __future__ import annotations

import argparse
from pathlib import Path
import statistics
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from conversational_nl import (
    build_intent_frame,
    normalize_conversational_command,
    resolve_intent_followup,
)
from device_clarification import build_binary_device_clarification


def _measure(callback, iterations: int, rounds: int = 5) -> tuple[float, float]:
    samples = []
    for _ in range(rounds):
        started = time.perf_counter()
        for _ in range(iterations):
            callback()
        samples.append((time.perf_counter() - started) * 1000.0 / iterations)
    return statistics.median(samples), max(samples)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=20_000)
    parser.add_argument("--states", type=int, default=2_000)
    parser.add_argument("--scan-iterations", type=int, default=20)
    args = parser.parse_args()

    phrases = (
        "Could you turn the stair light off for me?",
        "Would you mind making the kitchen lights half brightness?",
        "Please give me five minutes",
        "Could you play Dance for Me?",
    )
    phrase_index = 0

    def normalize_once():
        nonlocal phrase_index
        normalize_conversational_command(phrases[phrase_index % len(phrases)])
        phrase_index += 1

    frame = build_intent_frame(
        "color",
        "set stair light to red",
        target_keys=("light.stair",),
    )

    def followup_once():
        resolve_intent_followup("actually make it blue instead", frame)

    matching_states = [
        {
            "entity_id": f"light.bench_lamp_{index}",
            "state": "on",
            "attributes": {"friendly_name": f"Bench Lamp {index}"},
        }
        for index in range(args.states)
    ]

    controllable_count = min(40, max(2, args.states // 20))
    mixed_states = [
        {
            "entity_id": f"sensor.metric_{index}",
            "state": str(index),
            "attributes": {"friendly_name": f"Metric {index}"},
        }
        for index in range(max(0, args.states - controllable_count))
    ] + matching_states[:controllable_count]

    exact_states = mixed_states + [
        {
            "entity_id": "light.stair_light",
            "state": "on",
            "attributes": {"friendly_name": "Stair Light"},
        }
    ]

    def mixed_ambiguity_scan_once():
        build_binary_device_clarification(
            "turn bench lamp off",
            states_snapshot=mixed_states,
        )

    def mixed_exact_scan_once():
        build_binary_device_clarification(
            "turn stair light off",
            states_snapshot=exact_states,
        )

    def worst_case_scan_once():
        build_binary_device_clarification(
            "turn bench lamp off",
            states_snapshot=matching_states,
        )

    normalize_median, normalize_max = _measure(normalize_once, args.iterations)
    followup_median, followup_max = _measure(followup_once, args.iterations)
    mixed_median, mixed_max = _measure(mixed_ambiguity_scan_once, args.scan_iterations)
    exact_median, exact_max = _measure(mixed_exact_scan_once, args.scan_iterations)
    worst_median, worst_max = _measure(worst_case_scan_once, args.scan_iterations)

    print(f"normalization       median={normalize_median:.4f} ms max_round={normalize_max:.4f} ms")
    print(f"follow-up rewrite   median={followup_median:.4f} ms max_round={followup_max:.4f} ms")
    print(
        f"mixed ambiguity     median={mixed_median:.4f} ms max_round={mixed_max:.4f} ms "
        f"states={len(mixed_states)} controllable={controllable_count}"
    )
    print(
        f"mixed exact         median={exact_median:.4f} ms max_round={exact_max:.4f} ms "
        f"states={len(exact_states)}"
    )
    print(
        f"all-match ceiling   median={worst_median:.4f} ms max_round={worst_max:.4f} ms "
        f"states={len(matching_states)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
