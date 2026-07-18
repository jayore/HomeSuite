from __future__ import annotations

import asyncio
import threading
import unittest

from satellite_coordination import (
    SatelliteCoordinationClient,
    normalize_satellite_ws_url,
)
from wakeword_arbitration import WakewordArbitrator, score_candidate


def _candidate(
    candidate_id: str,
    event_at_ms: int,
    *,
    wake_score: float = 0.85,
    separation_db: float = 14.0,
    p90_dbfs: float = -18.0,
    label: str = "hal_v2",
    clock_synchronized: bool = True,
) -> dict:
    return {
        "candidate_id": candidate_id,
        "wakeword_label": label,
        "wakeword_score": wake_score,
        "wakeword_threshold": 0.75,
        "audio_quality": {
            "separation_db": separation_db,
            "p90_dbfs": p90_dbfs,
            "clip_pct": 0.0,
        },
        "timing": {
            "clock": {"ntp_synchronized": clock_synchronized},
            "wakeword": {
                "label": label,
                "audio_end_at_ms": event_at_ms,
            }
        },
    }


class WakewordArbitratorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events: list[tuple[str, dict]] = []

        async def emit(source_id: str, payload: dict) -> None:
            self.events.append((source_id, payload))

        self.arbitrator = WakewordArbitrator(
            emit,
            election_window_ms=20,
            cohort_window_ms=700,
            lease_seconds=10.0,
        )

    async def test_single_connected_device_is_granted_without_hold(self):
        self.arbitrator.register_source("piphone1", source_room="living_room")

        await self.arbitrator.submit_candidate(
            "piphone1",
            _candidate("candidate-a", 1_000),
        )

        self.assertEqual(len(self.events), 1)
        source_id, decision = self.events[0]
        self.assertEqual(source_id, "piphone1")
        self.assertEqual(decision["disposition"], "granted")
        self.assertEqual(decision["election_hold_ms"], 0)
        self.assertTrue(decision["interaction_id"])
        self.assertTrue(decision["winner_token"])

    async def test_multi_device_election_prefers_clearer_candidate_and_suppresses_loser(self):
        self.arbitrator.register_source("hall", source_room="hall")
        self.arbitrator.register_source("kitchen", source_room="kitchen")

        await self.arbitrator.submit_candidate(
            "hall",
            _candidate(
                "candidate-hall",
                2_000,
                wake_score=0.79,
                separation_db=5.0,
                p90_dbfs=-34.0,
            ),
        )
        self.assertEqual(self.events, [])
        await self.arbitrator.submit_candidate(
            "kitchen",
            _candidate(
                "candidate-kitchen",
                2_040,
                wake_score=0.91,
                separation_db=22.0,
                p90_dbfs=-12.0,
            ),
        )
        await asyncio.sleep(0.04)

        decisions = {source_id: payload for source_id, payload in self.events}
        self.assertEqual(decisions["kitchen"]["disposition"], "granted")
        self.assertEqual(decisions["hall"]["disposition"], "suppressed")
        self.assertEqual(decisions["hall"]["winner_source_id"], "kitchen")
        self.assertEqual(decisions["kitchen"]["election_hold_ms"], 20)

    async def test_late_matching_candidate_is_suppressed_by_closed_cohort(self):
        self.arbitrator.register_source("hall", source_room="hall")
        self.arbitrator.register_source("kitchen", source_room="kitchen")
        await self.arbitrator.submit_candidate(
            "hall",
            _candidate("candidate-hall", 3_000),
        )
        await asyncio.sleep(0.04)
        self.events.clear()

        await self.arbitrator.submit_candidate(
            "kitchen",
            _candidate("candidate-kitchen", 3_080, wake_score=0.99),
        )

        self.assertEqual(len(self.events), 1)
        self.assertEqual(self.events[0][0], "kitchen")
        self.assertEqual(self.events[0][1]["disposition"], "suppressed")
        self.assertEqual(self.events[0][1]["reason"], "late_duplicate")

    async def test_differently_named_models_still_compete_for_the_same_wake(self):
        self.arbitrator.register_source("hall", source_room="hall")
        self.arbitrator.register_source("kitchen", source_room="kitchen")
        await self.arbitrator.submit_candidate(
            "hall",
            _candidate("candidate-hall", 3_500, label="hal_v2", wake_score=0.80),
        )
        await self.arbitrator.submit_candidate(
            "kitchen",
            _candidate("candidate-kitchen", 3_530, label="hal_custom", wake_score=0.92),
        )
        await asyncio.sleep(0.04)

        decisions = {source_id: payload for source_id, payload in self.events}
        self.assertEqual(decisions["kitchen"]["disposition"], "granted")
        self.assertEqual(decisions["hall"]["disposition"], "suppressed")

    async def test_unsynchronized_clocks_fall_back_to_brain_arrival_time(self):
        self.arbitrator.register_source("hall", source_room="hall")
        self.arbitrator.register_source("kitchen", source_room="kitchen")
        await self.arbitrator.submit_candidate(
            "hall",
            _candidate(
                "candidate-hall",
                10_000,
                wake_score=0.80,
                clock_synchronized=False,
            ),
        )
        await self.arbitrator.submit_candidate(
            "kitchen",
            _candidate(
                "candidate-kitchen",
                90_000,
                wake_score=0.92,
                clock_synchronized=False,
            ),
        )
        await asyncio.sleep(0.04)

        decisions = {source_id: payload for source_id, payload in self.events}
        self.assertEqual(decisions["kitchen"]["disposition"], "granted")
        self.assertEqual(decisions["hall"]["disposition"], "suppressed")

    async def test_unsynchronized_clock_is_not_used_to_break_score_tie(self):
        self.arbitrator.register_source("hall", source_room="hall")
        self.arbitrator.register_source("kitchen", source_room="kitchen")
        await self.arbitrator.submit_candidate(
            "hall",
            _candidate(
                "candidate-hall",
                90_000,
                clock_synchronized=False,
            ),
        )
        await self.arbitrator.submit_candidate(
            "kitchen",
            _candidate(
                "candidate-kitchen",
                10_000,
                clock_synchronized=False,
            ),
        )
        await asyncio.sleep(0.04)

        decisions = {source_id: payload for source_id, payload in self.events}
        self.assertEqual(decisions["hall"]["disposition"], "granted")
        self.assertEqual(decisions["kitchen"]["disposition"], "suppressed")

    async def test_mixed_quality_telemetry_uses_model_confidence_for_everyone(self):
        self.arbitrator.register_source("hall", source_room="hall")
        self.arbitrator.register_source("kitchen", source_room="kitchen")
        no_quality = _candidate("candidate-hall", 95_000, wake_score=0.92)
        no_quality["audio_quality"] = {}
        await self.arbitrator.submit_candidate("hall", no_quality)
        await self.arbitrator.submit_candidate(
            "kitchen",
            _candidate(
                "candidate-kitchen",
                95_030,
                wake_score=0.84,
                separation_db=24.0,
                p90_dbfs=-10.0,
            ),
        )
        await asyncio.sleep(0.04)

        decisions = {source_id: payload for source_id, payload in self.events}
        self.assertEqual(decisions["hall"]["disposition"], "granted")
        self.assertEqual(decisions["kitchen"]["disposition"], "suppressed")

    async def test_winner_lease_executes_once_and_replays_cached_result(self):
        self.arbitrator.register_source("piphone1", source_room="living_room")
        await self.arbitrator.submit_candidate(
            "piphone1",
            _candidate("candidate-a", 4_000),
        )
        decision = self.events[0][1]
        kwargs = {
            "source_id": "piphone1",
            "interaction_id": decision["interaction_id"],
            "winner_token": decision["winner_token"],
        }

        first = self.arbitrator.begin_command(**kwargs)
        duplicate = self.arbitrator.begin_command(**kwargs)
        self.assertEqual(first["state"], "execute")
        self.assertEqual(duplicate["state"], "wait")

        result = {"ok": True, "handled": True, "response": "Done."}
        self.arbitrator.finish_command(decision["interaction_id"], result)
        waited_payload, waited_status = await duplicate["future"]
        self.assertEqual(waited_payload, result)
        self.assertEqual(waited_status, 200)

        cached = self.arbitrator.begin_command(**kwargs)
        self.assertEqual(cached["state"], "cached")
        self.assertEqual(cached["payload"], result)

        rejected = self.arbitrator.begin_command(
            source_id="another-device",
            interaction_id=decision["interaction_id"],
            winner_token=decision["winner_token"],
        )
        self.assertEqual(rejected["state"], "suppressed")

    async def test_disconnect_removes_candidate_before_election(self):
        self.arbitrator.register_source("hall", source_room="hall")
        self.arbitrator.register_source("kitchen", source_room="kitchen")
        await self.arbitrator.submit_candidate(
            "hall",
            _candidate("candidate-hall", 5_000, wake_score=0.99),
        )
        self.arbitrator.unregister_source("hall")
        await self.arbitrator.submit_candidate(
            "kitchen",
            _candidate("candidate-kitchen", 5_030, wake_score=0.80),
        )

        decisions = {source_id: payload for source_id, payload in self.events}
        self.assertEqual(decisions["kitchen"]["disposition"], "granted")
        self.assertNotIn("hall", decisions)


class WakewordCandidateScoreTests(unittest.TestCase):
    def test_signal_quality_can_outweigh_a_small_raw_model_score_difference(self):
        noisy = score_candidate(
            _candidate(
                "noisy",
                1,
                wake_score=0.90,
                separation_db=4.0,
                p90_dbfs=-38.0,
            )
        )
        clear = score_candidate(
            _candidate(
                "clear",
                1,
                wake_score=0.87,
                separation_db=20.0,
                p90_dbfs=-16.0,
            )
        )
        self.assertGreater(clear["total"], noisy["total"])


class SatelliteCoordinationClientTests(unittest.TestCase):
    def _client(self) -> SatelliteCoordinationClient:
        return SatelliteCoordinationClient(
            brain_url="http://piphone.local:8765/command",
            api_key="test-key",
            source_id="piphone1",
            source_room="living_room",
        )

    def test_brain_url_normalizes_to_dedicated_websocket(self):
        self.assertEqual(
            normalize_satellite_ws_url("http://piphone.local:8765/command"),
            "ws://piphone.local:8765/satellite/ws",
        )
        self.assertEqual(
            normalize_satellite_ws_url("https://brain.example/base"),
            "wss://brain.example/base/satellite/ws",
        )

    def test_unconnected_single_device_uses_legacy_compatibility(self):
        decision = self._client().request_wakeword_decision(
            {"candidate_id": "candidate-a"}
        )
        self.assertEqual(decision.disposition, "legacy")

    def test_known_multi_device_cluster_fails_closed_when_disconnected(self):
        client = self._client()
        client._handle_message(
            {"type": "cluster_state", "eligible_wakeword_nodes": 2}
        )
        decision = client.request_wakeword_decision(
            {"candidate_id": "candidate-a"}
        )
        self.assertEqual(decision.disposition, "unavailable")

    def test_connected_client_correlates_decision_to_candidate(self):
        client = self._client()
        client._handle_message(
            {"type": "satellite_hello_ack", "eligible_wakeword_nodes": 1}
        )

        def respond() -> None:
            outgoing = client._outgoing.get(timeout=1.0)
            client._handle_message(
                {
                    "type": "wakeword_decision",
                    "candidate_id": outgoing["candidate_id"],
                    "disposition": "granted",
                    "interaction_id": "interaction-a",
                    "winner_token": "winner-token",
                    "winner_source_id": "piphone1",
                    "eligible_wakeword_nodes": 1,
                    "election_hold_ms": 0,
                }
            )

        thread = threading.Thread(target=respond)
        thread.start()
        decision = client.request_wakeword_decision(
            {"candidate_id": "candidate-a"},
            timeout_seconds=1.0,
        )
        thread.join(timeout=1.0)

        self.assertEqual(decision.disposition, "granted")
        self.assertEqual(decision.interaction_id, "interaction-a")
        self.assertEqual(decision.election_hold_ms, 0)


if __name__ == "__main__":
    unittest.main()
